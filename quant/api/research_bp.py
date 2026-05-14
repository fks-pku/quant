import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from flask import Blueprint, jsonify, request, send_file

from quant.features.research.models import ResearchConfig, ResearchResult
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.pool import CandidatePool
from quant.features.research.scheduler import ResearchScheduler
from quant.infrastructure.research.asset_paths import (
    IDEA_BANK_JSON,
    IDEA_BANK_MD,
    LATEST_REPORT_METADATA,
    LEGACY_IDEA_BANK_JSON,
    LEGACY_IDEA_BANK_MD,
    REPORT_HTML,
    latest_report_html_path,
)


def _make_backtest_fn():
    """Create a backtest function for the research pipeline.

    Defined at the API layer (not in features/) so cross-feature imports are
    architectural composition-root wiring, not feature-to-feature coupling.
    """
    from quant.features.backtest.engine import Backtester
    from quant.features.backtest.benchmark import BenchmarkProvider
    from quant.features.strategies.registry import StrategyRegistry
    from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.features.research.models import ResearchLogEntry
    from quant.domain.models.market import is_cn_symbol

    def _run_backtest(sid, result, config, integrator, pool):
        registry = StrategyRegistry()
        strategy_class = registry.get(sid)
        if strategy_class is None:
            result.errors.append(f"Strategy {sid} not in registry for backtest")
            return

        info = integrator.get_registry_entry(sid)
        symbols = _candidate_symbols(info, config.default_symbols)
        start = datetime.strptime(config.default_backtest_start, "%Y-%m-%d")
        end = datetime.strptime(config.default_backtest_end, "%Y-%m-%d")
        is_cn = any(is_cn_symbol(sym) for sym in symbols)
        initial_cash = 500000 if is_cn else 100000

        db_provider = DuckDBProvider()
        db_provider.connect()
        lot_sizes = {}
        benchmark_provider = None
        benchmark_meta = {"symbol": "", "coverage_start": "", "coverage_end": "", "rows": 0, "fallback_used": False}
        try:
            data_df = db_provider.get_bars_for_symbols(symbols, start, end, "1d")
            for sym in symbols:
                try:
                    lot_sizes[sym] = db_provider.storage.get_lot_size(sym) if is_cn_symbol(sym) else 1
                except Exception:
                    lot_sizes[sym] = 100 if is_cn_symbol(sym) else 1
            if is_cn:
                benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
        finally:
            db_provider.disconnect()

        if data_df.empty:
            result.errors.append(f"No data for {sid}")
            return

        data_provider = DataFrameProvider(data_df)
        strategy = strategy_class(symbols=symbols)

        bt_config = {
            "backtest": {"slippage_bps": 5},
            "execution": {"commission": {
                "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                "HK": {"type": "hk_realistic"},
                "CN": {"type": "cn_realistic"},
            }},
            "data": {"default_timeframe": "1d"},
            "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
        }

        backtester = Backtester(
            bt_config,
            portfolio_class=Portfolio,
            risk_engine_class=RiskEngine,
            sub_portfolio_class=SubPortfolio,
            lot_sizes=lot_sizes,
            benchmark_provider=benchmark_provider,
        )
        bt_result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=initial_cash,
            data_provider=data_provider,
            symbols=symbols,
        )
        benchmark_equity_curve = None
        if benchmark_provider is not None:
            try:
                benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, initial_cash)
            except Exception:
                benchmark_equity_curve = None

        strict_report = _strict_backtest_report(
            bt_result,
            start,
            end,
            initial_cash,
            symbols,
            benchmark_meta,
            lot_sizes,
            strategy,
            benchmark_equity_curve,
        )
        result.backtested += 1

        if info is not None:
            info["backtest"] = strict_report
            meta = info.setdefault("research_meta", {})
            meta["backtest_result"] = strict_report
            meta["strict_backtest_result"] = strict_report
            _persist_candidate_backtest(pool, sid, info, strict_report)
            if bt_result.sharpe_ratio < config.backtest_sharpe_threshold:
                pool.reject(sid, reason=f"Backtest Sharpe {bt_result.sharpe_ratio:.2f} below threshold")
                result.rejected += 1
                _update_hypothesis_backtest(
                    pool,
                    sid,
                    strict_report,
                    "rejected",
                    "backtest",
                    f"Strict Backtester Sharpe {bt_result.sharpe_ratio:.2f} < {config.backtest_sharpe_threshold}",
                )
                result.log.append(ResearchLogEntry(
                    phase="backtest", title=info.get("name", sid),
                    source="", source_url="", verdict="fail",
                    reason=f"Sharpe {bt_result.sharpe_ratio:.2f} < {config.backtest_sharpe_threshold}",
                    scores={
                        "sharpe": round(bt_result.sharpe_ratio, 2),
                        "sortino": round(float(bt_result.sortino_ratio), 2) if bt_result.sortino_ratio != float("inf") else "inf",
                        "max_dd": round(bt_result.max_drawdown_pct, 2),
                        "win_rate": round(bt_result.win_rate * 100, 2),
                        "profit_factor": round(bt_result.profit_factor, 2),
                        "trades": len(bt_result.trades),
                        "benchmark": benchmark_meta.get("symbol", ""),
                    },
                ))
            else:
                _update_hypothesis_backtest(
                    pool,
                    sid,
                    strict_report,
                    "candidate",
                    "backtest",
                    f"Strict Backtester Sharpe {bt_result.sharpe_ratio:.2f}",
                )
                result.log.append(ResearchLogEntry(
                    phase="backtest", title=info.get("name", sid),
                    source="", source_url="", verdict="pass",
                    reason=f"Sharpe {bt_result.sharpe_ratio:.2f}",
                    scores={
                        "sharpe": round(bt_result.sharpe_ratio, 2),
                        "sortino": round(float(bt_result.sortino_ratio), 2) if bt_result.sortino_ratio != float("inf") else "inf",
                        "max_dd": round(bt_result.max_drawdown_pct, 2),
                        "win_rate": round(bt_result.win_rate * 100, 2),
                        "profit_factor": round(bt_result.profit_factor, 2),
                        "trades": len(bt_result.trades),
                        "benchmark": benchmark_meta.get("symbol", ""),
                    },
                ))

    return _run_backtest


def _load_cn_benchmark_provider(db_provider, start, end, benchmark_provider_cls):
    import pandas as pd

    for symbol in ("000300", "510300"):
        bars = db_provider.get_bars(symbol, start, end, "1d")
        if bars.empty:
            continue
        price_column = "adj_close" if "adj_close" in bars.columns and not bars["adj_close"].isna().all() else "close"
        provider = benchmark_provider_cls(bars, price_column=price_column)
        timestamps = pd.to_datetime(bars["timestamp"], errors="coerce").dropna() if "timestamp" in bars.columns else None
        meta = {
            "symbol": symbol,
            "coverage_start": str(timestamps.min().date()) if timestamps is not None and not timestamps.empty else "",
            "coverage_end": str(timestamps.max().date()) if timestamps is not None and not timestamps.empty else "",
            "rows": int(len(bars)),
            "fallback_used": symbol != "000300",
            "price_column": price_column,
        }
        return provider, meta
    return None, {"symbol": "", "coverage_start": "", "coverage_end": "", "rows": 0, "fallback_used": False}


def _strict_backtest_report(
    bt_result,
    start,
    end,
    initial_cash,
    symbols,
    benchmark_meta,
    lot_sizes,
    strategy=None,
    benchmark_equity_curve=None,
):
    metrics = getattr(bt_result, "metrics", None)
    diagnostics = getattr(bt_result, "diagnostics", None)
    days = max(1, (end - start).days)
    cagr = (float(bt_result.final_nav) / float(initial_cash)) ** (365.25 / days) - 1.0 if initial_cash > 0 else 0.0
    max_drawdown_pct = _metric_float(getattr(bt_result, "max_drawdown_pct", 0.0))
    stat_sig = getattr(metrics, "statistical_significance", {}) if metrics is not None else {}
    benchmark_metrics = _benchmark_equity_metrics(benchmark_equity_curve, initial_cash, start, end)
    strategy_equity_curve = getattr(bt_result, "equity_curve", None)
    return {
        "framework": "Backtester + DataFrameProvider + Strategy + Portfolio/RiskEngine/SubPortfolio",
        "period": f"{start.date()}-{end.date()}",
        "initial_cash": float(initial_cash),
        "symbols": list(symbols),
        "metrics": {
            "sharpe": _metric_float(getattr(bt_result, "sharpe_ratio", 0.0)),
            "sortino": _metric_float(getattr(bt_result, "sortino_ratio", 0.0)),
            "cagr": _metric_float(cagr),
            "total_return": _metric_float(getattr(bt_result, "total_return", 0.0)),
            "max_drawdown_pct": max_drawdown_pct,
            "calmar_ratio": _metric_float(_calmar_ratio(cagr, max_drawdown_pct)),
            "win_rate": _metric_float(getattr(bt_result, "win_rate", 0.0)),
            "profit_factor": _metric_float(getattr(bt_result, "profit_factor", 0.0)),
            "total_trades": int(len(getattr(bt_result, "trades", []) or [])),
            "round_trip_trades": int(getattr(metrics, "total_trades", 0) if metrics is not None else 0),
            "t_stat": _metric_float(stat_sig.get("t_stat", 0.0) if isinstance(stat_sig, dict) else 0.0),
            "p_value": _metric_float(stat_sig.get("p_value", 1.0) if isinstance(stat_sig, dict) else 1.0),
        },
        "benchmark": {
            **dict(benchmark_meta or {}),
            **benchmark_metrics,
            "benchmark_return": _metric_float(getattr(metrics, "benchmark_return", None) if metrics is not None else None),
            "alpha": _metric_float(getattr(metrics, "alpha", None) if metrics is not None else None),
            "beta": _metric_float(getattr(metrics, "beta", None) if metrics is not None else None),
            "information_ratio": _metric_float(getattr(metrics, "information_ratio", None) if metrics is not None else None),
            "tracking_error": _metric_float(getattr(metrics, "tracking_error", None) if metrics is not None else None),
            "benchmark_yearly_returns": _yearly_returns_from_equity(benchmark_equity_curve, initial_cash),
        },
        "diagnostics": _diagnostics_dict(diagnostics),
        "equity_curve": {
            "strategy": _series_to_curve_points(strategy_equity_curve),
            "benchmark": _series_to_curve_points(benchmark_equity_curve),
        },
        "yearly_returns": _yearly_returns_from_equity(strategy_equity_curve, initial_cash),
        "constraints": {
            "hfq_signal_policy": "Strategy helpers use adj_* prices for signal logic; raw close is reserved for order sizing/fill accounting.",
            "long_only": True,
            "t_plus_1": True,
            "cn_lot_size": 100,
            "lot_sizes": dict(lot_sizes or {}),
            "volume_limit": "Backtester execution diagnostics record volume_limited_trades.",
            "price_limits": "Backtester execution diagnostics record limit_rejected_orders.",
            "commission": {"CN": "cn_realistic", "HK": "hk_realistic", "US": "per_share"},
            "slippage_bps": 5,
            "strategy_max_position_pct": _metric_float(getattr(strategy, "max_position_pct", None)),
            "strategy_max_positions": int(getattr(strategy, "max_positions", 0) or 0),
        },
    }


def _persist_candidate_backtest(pool, sid, info, strict_report):
    store = getattr(pool, "research_store", None)
    if store is None or not hasattr(store, "upsert_candidate"):
        return
    try:
        stored = store.get_candidate(sid) or dict(info or {"id": sid})
        stored["backtest"] = strict_report
        meta = stored.setdefault("research_meta", {})
        meta["backtest_result"] = strict_report
        meta["strict_backtest_result"] = strict_report
        store.upsert_candidate(stored)
    except Exception:
        return


def _update_hypothesis_backtest(pool, sid, strict_report, status, stage, reason):
    store = getattr(pool, "research_store", None)
    if store is None or not hasattr(store, "list_hypotheses"):
        return
    try:
        for row in store.list_hypotheses():
            if row.get("strategy_id") != sid:
                continue
            updated = dict(row)
            metrics = dict(updated.get("metrics") or {})
            metrics["strict_backtest"] = strict_report
            metrics["backtest_sharpe"] = strict_report.get("metrics", {}).get("sharpe", 0.0)
            metrics["backtest_sortino"] = strict_report.get("metrics", {}).get("sortino", 0.0)
            metrics["backtest_cagr"] = strict_report.get("metrics", {}).get("cagr", 0.0)
            metrics["backtest_max_drawdown_pct"] = strict_report.get("metrics", {}).get("max_drawdown_pct", 0.0)
            metrics["backtest_calmar_ratio"] = strict_report.get("metrics", {}).get("calmar_ratio", 0.0)
            metrics["benchmark_symbol"] = strict_report.get("benchmark", {}).get("symbol", "")
            updated["metrics"] = metrics
            updated["status"] = status
            updated["stage"] = stage
            updated["decision_reason"] = reason
            store.upsert_hypothesis(updated)
    except Exception:
        return


def _diagnostics_dict(diagnostics):
    if diagnostics is None:
        return {}
    fields = (
        "fill_count",
        "total_commission",
        "cost_drag_pct",
        "volume_limited_trades",
        "lot_adjusted_trades",
        "t1_rejected_sells",
        "limit_rejected_orders",
        "discarded_orders",
        "expired_orders",
        "submission_rejected",
        "risk_skipped_orders",
        "truncated_sells",
        "forced_closeout_orders",
        "forced_closeout_trades",
        "final_suspended_holding_nav",
        "final_suspended_holding_count",
    )
    data = {field: _metric_float(getattr(diagnostics, field, 0.0)) for field in fields}
    rejection_counts = dict(getattr(diagnostics, "rejection_counts", {}) or {})
    data["insufficient_cash_rejected_orders"] = int(rejection_counts.get("insufficient_cash", 0) or 0)
    data["final_suspended_symbols"] = list(getattr(diagnostics, "final_suspended_symbols", []) or [])
    data["rejection_counts"] = rejection_counts
    return data


def _series_to_curve_points(series):
    if series is None or not hasattr(series, "empty") or series.empty:
        return []
    points = []
    for idx, value in series.items():
        number = _metric_float(value)
        if isinstance(number, str):
            continue
        try:
            date_text = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        except Exception:
            date_text = str(idx)[:10]
        points.append({"date": date_text, "value": number})
    return points


def _benchmark_equity_metrics(series, initial_cash, start, end):
    if series is None or not hasattr(series, "empty") or series.empty:
        return {}
    try:
        from quant.features.backtest.analytics import (
            calculate_max_drawdown,
            calculate_sharpe,
            calculate_sortino,
        )

        equity = series.dropna()
        if equity.empty:
            return {}
        returns = equity.pct_change().dropna()
        days = max(1, (end - start).days)
        initial = float(initial_cash)
        final_value = float(equity.iloc[-1])
        total_return = (final_value / initial) - 1.0 if initial > 0 else 0.0
        cagr = (final_value / initial) ** (365.25 / days) - 1.0 if initial > 0 else 0.0
        _, max_drawdown_pct, _, _ = calculate_max_drawdown(equity)
        return {
            "benchmark_total_return": _metric_float(total_return),
            "benchmark_cagr": _metric_float(cagr),
            "benchmark_sharpe": _metric_float(calculate_sharpe(returns)),
            "benchmark_sortino": _metric_float(calculate_sortino(returns)),
            "benchmark_max_drawdown_pct": _metric_float(max_drawdown_pct),
            "benchmark_calmar_ratio": _metric_float(_calmar_ratio(cagr, max_drawdown_pct)),
        }
    except Exception:
        return {}


def _yearly_returns_from_equity(series, initial_cash=None):
    if series is None or not hasattr(series, "empty") or series.empty:
        return {}
    try:
        import pandas as pd

        equity = series.dropna().copy()
        if equity.empty:
            return {}
        if not isinstance(equity.index, pd.DatetimeIndex):
            equity.index = pd.to_datetime(equity.index, errors="coerce")
            equity = equity[~equity.index.isna()]
        equity = equity.sort_index()
        if equity.empty:
            return {}
        result = {}
        previous_close = None
        for year, group in equity.groupby(equity.index.year):
            if group.empty:
                continue
            base = previous_close
            if base is None:
                base = initial_cash if initial_cash is not None and float(initial_cash) > 0 else float(group.iloc[0])
            final_value = float(group.iloc[-1])
            if float(base) > 0:
                result[str(int(year))] = _metric_float(final_value / float(base) - 1.0)
            previous_close = final_value
        return result
    except Exception:
        return {}


def _metric_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number == float("inf"):
        return "inf"
    if number == float("-inf"):
        return "-inf"
    if number != number:
        return 0.0
    return number


def _calmar_ratio(cagr, max_drawdown):
    cagr_value = _metric_float(cagr)
    drawdown_value = _metric_float(max_drawdown)
    if isinstance(cagr_value, str) or isinstance(drawdown_value, str):
        return 0.0
    drawdown = abs(drawdown_value)
    if drawdown <= 1e-12:
        return 0.0
    return cagr_value / drawdown


def _serialize_walkforward_trade(trade, data_df):
    symbol = getattr(trade, "symbol", "")
    side = getattr(trade, "side", "")
    quantity = _safe_float(getattr(trade, "quantity", 0.0))
    price = _trade_price(trade)
    trade_value = abs(quantity) * price
    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "pnl": _safe_float(getattr(trade, "pnl", 0.0)),
        "trade_value": trade_value,
        "avg_daily_volume": _average_daily_volume(data_df, symbol, _trade_date(trade)),
    }


def _trade_price(trade) -> float:
    side = str(getattr(trade, "side", "")).upper()
    names = ["price", "fill_price"]
    names.extend(["exit_price", "entry_price"] if side == "SELL" else ["entry_price", "exit_price"])
    for name in names:
        value = _safe_float(getattr(trade, name, 0.0))
        if value > 0:
            return value
    return 0.0


def _trade_date(trade):
    for name in ("fill_date", "signal_date", "exit_time", "entry_time", "timestamp"):
        value = getattr(trade, name, None)
        if value is not None:
            return value
    return None


def _average_daily_volume(data_df, symbol, trade_date=None) -> float:
    if data_df is None or not hasattr(data_df, "empty") or data_df.empty or "volume" not in data_df.columns:
        return 0.0
    try:
        import pandas as pd

        data = data_df
        if symbol and "symbol" in data.columns:
            data = data[data["symbol"].astype(str) == str(symbol)]
        if data.empty:
            return 0.0

        date_col = "timestamp" if "timestamp" in data.columns else "date" if "date" in data.columns else None
        if date_col is not None:
            dated = data.copy()
            dated[date_col] = pd.to_datetime(dated[date_col], errors="coerce")
            dated = dated.dropna(subset=[date_col]).sort_values(date_col)
            if dated.empty:
                return _median_volume(data)
            if trade_date is not None:
                trade_ts = pd.Timestamp(trade_date).normalize()
                dated_days = dated[date_col].dt.normalize()
                exact = dated[dated_days == trade_ts]
                if not exact.empty:
                    return float(exact["volume"].mean())
                prior = dated[dated[date_col] <= pd.Timestamp(trade_date)].tail(63)
                if not prior.empty:
                    return float(prior["volume"].median())
            return float(dated.tail(63)["volume"].median())
        return _median_volume(data)
    except Exception:
        return 0.0


def _median_volume(data) -> float:
    try:
        return float(data["volume"].median())
    except Exception:
        return 0.0


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _make_walkforward_runner():
    from quant.features.backtest.engine import Backtester
    from quant.features.strategies.registry import StrategyRegistry
    from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.domain.models.market import is_cn_symbol
    import pandas as pd

    def _run_walkforward_backtest(sid, request):
        registry = StrategyRegistry()
        strategy_class = registry.get(sid)
        if strategy_class is None:
            return {"metrics": {"sharpe": 0.0, "max_dd": 0.0, "cagr": 0.0, "win_rate": 0.0}, "returns": pd.Series(dtype=float)}

        symbols = request.get("symbols") or []
        start = datetime.strptime(str(request["start"]), "%Y-%m-%d")
        end = datetime.strptime(str(request["end"]), "%Y-%m-%d")
        initial_cash = float(request.get("initial_cash", 100000))

        db_provider = DuckDBProvider()
        db_provider.connect()
        lot_sizes = {}
        try:
            data_df = db_provider.get_bars_for_symbols(symbols, start, end, "1d")
            for sym in symbols:
                try:
                    lot_sizes[sym] = db_provider.storage.get_lot_size(sym) if is_cn_symbol(sym) else 1
                except Exception:
                    lot_sizes[sym] = 100 if is_cn_symbol(sym) else 1
        finally:
            db_provider.disconnect()

        if data_df.empty:
            return {"metrics": {"sharpe": 0.0, "max_dd": 0.0, "cagr": 0.0, "win_rate": 0.0}, "returns": pd.Series(dtype=float)}

        data_provider = DataFrameProvider(data_df)
        strategy = strategy_class(symbols=symbols)
        bt_config = {
            "backtest": {"slippage_bps": 5},
            "execution": {"commission": {
                "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                "HK": {"type": "hk_realistic"},
                "CN": {"type": "cn_realistic"},
            }},
            "data": {"default_timeframe": "1d"},
            "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
        }
        backtester = Backtester(
            bt_config,
            portfolio_class=Portfolio,
            risk_engine_class=RiskEngine,
            sub_portfolio_class=SubPortfolio,
            lot_sizes=lot_sizes,
        )
        bt_result = backtester.run(start=start, end=end, strategies=[strategy], initial_cash=initial_cash, data_provider=data_provider, symbols=symbols)
        returns = bt_result.equity_curve.pct_change().dropna() if hasattr(bt_result, "equity_curve") else pd.Series(dtype=float)
        days = max(1, (end - start).days)
        trades = [_serialize_walkforward_trade(trade, data_df) for trade in getattr(bt_result, "trades", [])]
        return {
            "metrics": {
                "sharpe": float(getattr(bt_result, "sharpe_ratio", 0.0)),
                "max_dd": float(getattr(bt_result, "max_drawdown_pct", 0.0)),
                "cagr": float(getattr(bt_result, "total_return", 0.0) * 100 / max(1, days / 365.25)),
                "win_rate": float(getattr(bt_result, "win_rate", 0.0) * 100),
            },
            "returns": returns,
            "trades": trades,
        }

    return _run_walkforward_backtest


research_bp = Blueprint("research", __name__)

_research_jobs: dict = {}
_research_lock = threading.Lock()
_research_scheduler: ResearchScheduler = None


def _candidate_symbols(info, fallback):
    meta = dict((info or {}).get("research_meta") or {})
    spec = dict(meta.get("strategy_spec") or {})
    universe = spec.get("universe") or []
    symbols = [str(symbol) for symbol in universe if _is_a_share_symbol(str(symbol))]
    fallback_symbols = [str(symbol) for symbol in fallback or [] if _is_a_share_symbol(str(symbol))]
    return symbols or fallback_symbols or ["000300", "000905", "600519", "000001", "510300"]


def _is_a_share_symbol(symbol: str) -> bool:
    return len(symbol) == 6 and symbol.isdigit()


def _make_research_store(cfg: ResearchConfig):
    root = cfg.research_dir or str(Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research")
    if getattr(cfg, "tracking_enabled", False) and cfg.tracking_db_path:
        from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore

        return DuckDBResearchStore(db_path=cfg.tracking_db_path, artifact_root=root)
    from quant.infrastructure.research.repository import FileResearchStore

    return FileResearchStore(root)


def _research_artifact_root(cfg: ResearchConfig) -> Path:
    return Path(cfg.research_dir or Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research")


def _latest_report_path(cfg: ResearchConfig) -> Path:
    root = _research_artifact_root(cfg)
    primary = root / latest_report_html_path()
    legacy = root / REPORT_HTML
    if primary.exists() or not legacy.exists():
        return primary
    return legacy


def _latest_report_payload(cfg: ResearchConfig) -> dict:
    root = _research_artifact_root(cfg)
    path = _latest_report_path(cfg)
    payload = {
        "available": path.exists(),
        "url": "/api/research/report/latest",
        "path": str(path),
        "reports_root": str(root / "reports"),
    }
    metadata_path = root / LATEST_REPORT_METADATA
    if metadata_path.exists():
        try:
            with metadata_path.open("r", encoding="utf-8-sig") as f:
                payload["metadata"] = json.load(f)
        except Exception:
            payload["metadata"] = {}
    if path.exists():
        payload["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    return payload


def _idea_bank_payload(cfg: ResearchConfig) -> dict:
    root = _research_artifact_root(cfg)
    primary_json = root / IDEA_BANK_JSON
    primary_md = root / IDEA_BANK_MD
    legacy_json = root / LEGACY_IDEA_BANK_JSON
    legacy_md = root / LEGACY_IDEA_BANK_MD
    json_path = primary_json if primary_json.exists() or not legacy_json.exists() else legacy_json
    md_path = primary_md if primary_md.exists() or not legacy_md.exists() else legacy_md
    payload = {
        "available": json_path.exists() or md_path.exists(),
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "idea_bank_root": str(root / "idea_bank"),
    }
    if json_path.exists():
        payload["updated_at"] = datetime.fromtimestamp(json_path.stat().st_mtime).isoformat()
    elif md_path.exists():
        payload["updated_at"] = datetime.fromtimestamp(md_path.stat().st_mtime).isoformat()
    return payload


def _parse_statuses(value):
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_idea_ids(value):
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _make_experiment_stores(cfg: ResearchConfig):
    if not getattr(cfg, "tracking_enabled", False):
        return None, None
    from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore
    from quant.infrastructure.research.file_artifact_store import FileArtifactStore
    db_path = cfg.tracking_db_path or str(
        Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research" / "experiments.duckdb"
    )
    artifact_root = cfg.research_dir or str(
        Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research" / "artifacts"
    )
    return DuckDBExperimentStore(db_path), FileArtifactStore(artifact_root)


def _make_experiment_store(cfg: ResearchConfig):
    if not getattr(cfg, "tracking_enabled", False) or not cfg.tracking_db_path:
        return None
    from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore
    return DuckDBExperimentStore(cfg.tracking_db_path)


def _make_rigor_hub(cfg: ResearchConfig, experiment_store=None):
    from quant.features.research.rigor.backtest_hub import RigorHub
    return RigorHub(
        backtest_runner=_make_walkforward_runner(),
        config=cfg.rigor_config,
        experiment_store=experiment_store,
    )


def _make_strategy_scout(cfg: ResearchConfig):
    from quant.features.research.discovery.source_hub import SourceHub
    from quant.features.research.scout import StrategyScout
    from quant.infrastructure.research.sources import ArxivSource, BlogSource, NBERSource, SSRNSource

    scout_cfg = getattr(cfg, "scout_config", {}) or {}
    source_hub = SourceHub({
        "arxiv": ArxivSource(),
        "ssrn": SSRNSource(),
        "nber": NBERSource(),
        "blog": BlogSource(),
    }, query_plan=scout_cfg.get("query_plan"), quality_config=scout_cfg)
    return StrategyScout.from_source_hub(source_hub, sources=getattr(cfg, "sources", None), config=scout_cfg)


def _make_pit_data(cfg: ResearchConfig):
    if not getattr(cfg, "pit_enabled", False):
        return None
    from quant.infrastructure.research.pit_duckdb import PITDuckDBData

    return PITDuckDBData()


def _make_factor_data(cfg: ResearchConfig):
    from quant.infrastructure.research.factors.ff_factor_store import FFFactorStore

    validation_cfg = getattr(cfg, "validation_config", {}) or {}
    return FFFactorStore(cache_dir=validation_cfg.get("factor_cache_dir"))


def _validation_config(cfg: ResearchConfig) -> dict:
    validation_cfg = dict(getattr(cfg, "validation_config", {}) or {})
    validation_cfg.setdefault("min_observations", getattr(cfg, "validation_min_obs", 252))
    validation_cfg.setdefault("start_date", getattr(cfg, "default_backtest_start", "2012-01-01"))
    validation_cfg.setdefault("end_date", getattr(cfg, "default_backtest_end", "2025-12-31"))
    return validation_cfg


def _make_validation_components(cfg: ResearchConfig):
    if not getattr(cfg, "validation_enabled", True):
        return None, None

    from quant.features.research.validation import FactorValidator, StrategySpecBuilder

    validation_cfg = _validation_config(cfg)
    market_data = _make_research_market_data(cfg)
    validation_cfg.setdefault("default_universe", _default_research_universe(market_data))
    return (
        StrategySpecBuilder(validation_cfg),
        FactorValidator(
            market_data,
            config=validation_cfg,
            factor_data_port=_make_factor_data(cfg),
        ),
    )


def _default_research_universe(market_data):
    if not hasattr(market_data, "get_universe_symbols"):
        return []
    try:
        return [str(symbol) for symbol in market_data.get_universe_symbols("cn") if str(symbol).isdigit()]
    except Exception:
        return []


def _make_research_market_data(cfg: ResearchConfig, as_of_date: str = None):
    from quant.infrastructure.research.market_data import DuckDBResearchMarketData

    return DuckDBResearchMarketData(
        pit_data=_make_pit_data(cfg),
        pit_as_of_date=as_of_date or getattr(cfg, "default_backtest_end", None),
    )


def _make_benchmark_data_loader(cfg: ResearchConfig):
    from quant.features.research.rigor.regime_detector import benchmark_symbol_for_universe

    market_data = _make_research_market_data(cfg)

    def _load(symbols, start, end):
        benchmark_symbol = benchmark_symbol_for_universe(symbols)
        data = market_data.get_daily_bars([benchmark_symbol], start, end)
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data

    return _load


def _create_llm_adapter(cfg: ResearchConfig):
    if cfg.llm_provider == "openai":
        if not (cfg.llm_api_key or os.environ.get("OPENAI_API_KEY")):
            return None
        from quant.features.cio.llm_adapters.openai_adapter import OpenAIAdapter
        return OpenAIAdapter(model=cfg.llm_model, api_key=cfg.llm_api_key or "", temperature=cfg.llm_temperature)
    elif cfg.llm_provider == "claude":
        if not (cfg.llm_api_key or os.environ.get("ANTHROPIC_API_KEY")):
            return None
        from quant.features.cio.llm_adapters.claude_adapter import ClaudeAdapter
        return ClaudeAdapter(model=cfg.llm_model, api_key=cfg.llm_api_key or "", temperature=cfg.llm_temperature)
    elif cfg.llm_provider == "ollama":
        from quant.features.cio.llm_adapters.ollama_adapter import OllamaAdapter
        return OllamaAdapter(model=cfg.llm_model)
    elif cfg.llm_provider == "minimax":
        if not (cfg.llm_api_key or os.environ.get("MINIMAX_API_KEY")):
            return None
        from quant.features.cio.llm_adapters.minimax_adapter import MiniMaxAdapter
        return MiniMaxAdapter(
            model=cfg.llm_model,
            api_key=cfg.llm_api_key or "",
            temperature=cfg.llm_temperature,
            base_url=cfg.llm_base_url or "https://api.minimax.chat/v1",
            group_id=cfg.llm_group_id or "",
        )
    elif cfg.llm_provider == "deepseek":
        if not (cfg.llm_api_key or os.environ.get("DEEPSEEK_API_KEY")):
            return None
        from quant.features.cio.llm_adapters.deepseek_adapter import DeepSeekAdapter
        return DeepSeekAdapter(
            model=cfg.llm_model or "deepseek-chat",
            api_key=cfg.llm_api_key or "",
            temperature=cfg.llm_temperature,
            base_url=cfg.llm_base_url or "https://api.deepseek.com/v1",
        )
    elif cfg.llm_provider == "glm":
        if not (cfg.llm_api_key or os.environ.get("GLM_API_KEY")):
            return None
        from quant.features.cio.llm_adapters.glm_adapter import GLMAdapter
        return GLMAdapter(
            model=cfg.llm_model or "glm-5.1",
            api_key=cfg.llm_api_key or "",
            temperature=cfg.llm_temperature,
            base_url=cfg.llm_base_url or "https://api.z.ai/api/coding/paas/v4/",
        )
    return None


def _get_scheduler() -> ResearchScheduler:
    global _research_scheduler
    if _research_scheduler is None:
        cfg = _load_research_config()
        research_store = _make_research_store(cfg)
        llm_adapter = _create_llm_adapter(cfg)
        from quant.features.research.evaluator import StrategyEvaluator
        evaluator = StrategyEvaluator(llm_adapter=llm_adapter, rubric_config=getattr(cfg, "evaluation_config", {}) or {})
        experiment_store, artifact_store = _make_experiment_stores(cfg)
        spec_builder, validator = _make_validation_components(cfg)
        engine = ResearchEngine(
            config=cfg,
            scout=_make_strategy_scout(cfg),
            evaluator=evaluator,
            backtest_fn=_make_backtest_fn(),
            research_store=research_store,
            rigor_hub=_make_rigor_hub(cfg, experiment_store=experiment_store) if cfg.rigor_enabled else None,
            benchmark_data_loader=_make_benchmark_data_loader(cfg) if cfg.rigor_enabled else None,
            spec_builder=spec_builder,
            validator=validator,
        )
        if experiment_store:
            engine._experiment_store = experiment_store
            engine._artifact_store = artifact_store
            if getattr(cfg, "ensemble_enabled", False):
                from quant.features.research.ensemble.ensemble import StrategyEnsemble
                engine._ensemble = StrategyEnsemble(experiment_store, cfg.ensemble_config)
        _research_scheduler = ResearchScheduler(engine, cfg)
        if cfg.auto_run:
            _research_scheduler.start()
    return _research_scheduler


def _load_research_config() -> ResearchConfig:
    from quant.shared.utils.config_loader import ConfigLoader
    try:
        data = _load_research_config_data(ConfigLoader)
        research_cfg = data.get("research", {})
        validation_cfg = data.get("validation", {})
        evaluation_cfg = data.get("evaluation", {})
        pit_cfg = data.get("pit", {})
        llm_cfg = data.get("llm", {})
        merged_validation = dict(research_cfg.get("validation_config", {}) or {})
        merged_validation.update(validation_cfg or {})
        research_cfg["validation_config"] = merged_validation
        merged_evaluation = dict(research_cfg.get("evaluation_config", {}) or {})
        merged_evaluation.update(evaluation_cfg or {})
        research_cfg["evaluation_config"] = merged_evaluation
        if "enabled" in pit_cfg:
            research_cfg["pit_enabled"] = bool(pit_cfg.get("enabled"))
        if "universe_snapshot_dir" in pit_cfg:
            research_cfg["pit_universe_snapshot_dir"] = pit_cfg.get("universe_snapshot_dir")
        research_cfg.setdefault("llm_provider", llm_cfg.get("provider", "minimax"))
        research_cfg.setdefault("llm_model", llm_cfg.get("model", "MiniMax-M2.7"))
        research_cfg.setdefault("llm_api_key", llm_cfg.get("api_key"))
        research_cfg.setdefault("llm_temperature", llm_cfg.get("temperature", 0.3))
        research_cfg.setdefault("llm_base_url", llm_cfg.get("base_url"))
        research_cfg.setdefault("llm_group_id", llm_cfg.get("group_id"))
        return ResearchConfig(**research_cfg)
    except Exception:
        return ResearchConfig()


def _load_research_config_data(loader_cls=None) -> dict:
    if loader_cls is None:
        from quant.shared.utils.config_loader import ConfigLoader as loader_cls
    config_dir = Path(__file__).resolve().parent.parent / "features" / "research" / "config"
    try:
        return loader_cls(config_dir=str(config_dir)).load("research.yaml")
    except Exception:
        path = config_dir / "research.yaml"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        raise


@research_bp.route("/api/research/run", methods=["POST"])
def run_research():
    data = request.get_json() or {}
    sources = data.get("sources")
    max_results = data.get("max_results")
    mode = str(data.get("mode", "full")).lower().replace("-", "_")
    idea_statuses = _parse_statuses(data.get("idea_statuses") or data.get("statuses"))
    idea_ids = _parse_idea_ids(data.get("idea_ids") or data.get("idea_id"))
    max_ideas = data.get("max_ideas")
    job_id = str(uuid.uuid4())[:8]

    cfg = _load_research_config()
    if sources:
        cfg.sources = sources
    if max_results is not None:
        cfg.max_results_per_source = int(max_results)

    llm_adapter = _create_llm_adapter(cfg)
    from quant.features.research.evaluator import StrategyEvaluator
    evaluator = StrategyEvaluator(llm_adapter=llm_adapter, rubric_config=getattr(cfg, "evaluation_config", {}) or {})
    experiment_store, artifact_store = _make_experiment_stores(cfg)
    ensemble = None
    if getattr(cfg, "ensemble_enabled", False) and experiment_store is not None:
        from quant.features.research.ensemble.ensemble import StrategyEnsemble
        ensemble = StrategyEnsemble(experiment_store, cfg.ensemble_config)
    spec_builder, validator = _make_validation_components(cfg)
    engine = ResearchEngine(
        config=cfg,
        scout=_make_strategy_scout(cfg),
        evaluator=evaluator,
        backtest_fn=_make_backtest_fn(),
        research_store=_make_research_store(cfg),
        experiment_store=experiment_store,
        artifact_store=artifact_store,
        rigor_hub=_make_rigor_hub(cfg, experiment_store=experiment_store) if cfg.rigor_enabled else None,
        ensemble=ensemble,
        benchmark_data_loader=_make_benchmark_data_loader(cfg) if cfg.rigor_enabled else None,
        spec_builder=spec_builder,
        validator=validator,
    )

    def _run():
        try:
            if mode in {"discover", "discovery", "discovery_only"}:
                engine.run_discovery_only(sources=sources, result=result_obj)
            elif mode in {"formal", "research", "from_bank", "formal_research"}:
                engine.run_formal_research_from_idea_bank(
                    statuses=idea_statuses,
                    idea_ids=idea_ids,
                    max_ideas=int(max_ideas) if max_ideas is not None else None,
                    result=result_obj,
                )
            else:
                engine.run_full_pipeline(sources=sources, result=result_obj)
            with _research_lock:
                _research_jobs[job_id]["status"] = "completed"
                _research_jobs[job_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        except Exception as e:
            with _research_lock:
                _research_jobs[job_id].update({
                    "status": "error",
                    "error": str(e),
                    "updated_at": datetime.utcnow().isoformat() + "Z",
                })

    result_obj = ResearchResult()
    started_at = datetime.utcnow().isoformat() + "Z"
    with _research_lock:
        _research_jobs[job_id] = {
            "status": "running",
            "result": result_obj,
            "mode": mode,
            "started_at": started_at,
            "started_monotonic": time.monotonic(),
            "updated_at": started_at,
        }
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"research_id": job_id, "status": "running", "mode": mode})


@research_bp.route("/api/research/status/<research_id>")
def get_research_status(research_id):
    with _research_lock:
        job = _research_jobs.get(research_id)
    if job is None:
        return jsonify({"error": "Research job not found"}), 404
    response = {"research_id": research_id, "status": job["status"]}
    mode = job.get("mode", "full")
    response["mode"] = mode
    response["started_at"] = job.get("started_at")
    response["updated_at"] = job.get("updated_at")
    started_monotonic = job.get("started_monotonic")
    if started_monotonic is not None:
        response["elapsed_seconds"] = round(time.monotonic() - float(started_monotonic), 1)
    result = job.get("result")
    if result is not None:
        response["result"] = result.to_dict()
    if job["status"] == "completed":
        if mode in {"discover", "discovery", "discovery_only"}:
            response["idea_bank"] = _idea_bank_payload(_load_research_config())
        else:
            response["report"] = _latest_report_payload(_load_research_config())
    elif job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")
    return jsonify(response)


@research_bp.route("/api/research/candidates")
def list_candidates():
    pool = CandidatePool(research_store=_make_research_store(_load_research_config()))
    return jsonify({"candidates": pool.list_candidates()})


@research_bp.route("/api/research/ideas")
def list_ideas():
    status = _parse_statuses(request.args.get("status"))
    store = _make_research_store(_load_research_config())
    return jsonify({"ideas": store.list_ideas(status)})


@research_bp.route("/api/research/report")
def get_latest_report_info():
    return jsonify({"report": _latest_report_payload(_load_research_config())})


@research_bp.route("/api/research/report/latest")
def get_latest_report():
    path = _latest_report_path(_load_research_config())
    if not path.exists():
        return jsonify({"error": "Full research report not found"}), 404
    return send_file(str(path), mimetype="text/html")


@research_bp.route("/api/research/promote/<strategy_id>", methods=["POST"])
def promote_candidate(strategy_id):
    pool = CandidatePool(research_store=_make_research_store(_load_research_config()))
    success = pool.promote(strategy_id)
    if success:
        return jsonify({"success": True, "strategy_id": strategy_id, "status": "paused"})
    return jsonify({"success": False, "error": "Promotion failed"}), 400


@research_bp.route("/api/research/reject/<strategy_id>", methods=["POST"])
def reject_candidate(strategy_id):
    data = request.get_json() or {}
    reason = data.get("reason", "")
    pool = CandidatePool(research_store=_make_research_store(_load_research_config()))
    success = pool.reject(strategy_id, reason=reason)
    if success:
        return jsonify({"success": True, "strategy_id": strategy_id, "status": "rejected"})
    return jsonify({"success": False, "error": "Rejection failed"}), 400


@research_bp.route("/api/research/schedule", methods=["GET"])
def get_schedule():
    cfg = _load_research_config()
    return jsonify({
        "auto_run": cfg.auto_run,
        "interval_days": cfg.interval_days,
        "sources": cfg.sources,
        "max_results_per_source": cfg.max_results_per_source,
        "evaluation_threshold": cfg.evaluation_threshold,
        "scout_config": cfg.scout_config,
        "evaluation_config": cfg.evaluation_config,
        "backtest_sharpe_threshold": cfg.backtest_sharpe_threshold,
        "auto_backtest": cfg.auto_backtest,
        "llm_provider": cfg.llm_provider,
        "llm_model": cfg.llm_model,
        "llm_api_key_set": cfg.llm_api_key is not None,
    })


@research_bp.route("/api/research/schedule", methods=["POST"])
def update_schedule():
    data = request.get_json() or {}
    scheduler = _get_scheduler()
    if data.get("auto_run") and not scheduler.is_running:
        scheduler.start()
    elif not data.get("auto_run") and scheduler.is_running:
        scheduler.stop()
    return jsonify({"success": True, "schedule": data})


@research_bp.route("/api/research/run-scheduled", methods=["POST"])
def trigger_scheduled():
    scheduler = _get_scheduler()
    scheduler.trigger_now()
    return jsonify({"success": True, "message": "Scheduled research triggered"})


@research_bp.route("/api/research/experiments/<strategy_id>")
def list_experiments(strategy_id):
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    runs = store.list_runs(strategy_id=strategy_id)
    return jsonify({"runs": runs})


@research_bp.route("/api/research/experiments/<strategy_id>/<run_id>")
def get_experiment(strategy_id, run_id):
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    run = store.get_run(run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    metrics = store.list_metrics(run_id)
    run["metrics"] = metrics
    return jsonify(run)


@research_bp.route("/api/research/compare")
def compare_strategies():
    ids_str = request.args.get("ids", "")
    metric = request.args.get("metric", "sharpe")
    ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    if not ids:
        return jsonify({"error": "Provide ids parameter"}), 400
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    from quant.features.research.tracking.comparison import StrategyComparator
    comparator = StrategyComparator(store)
    results = comparator.compare(ids, metric_name=metric)
    return jsonify({"comparisons": results})


@research_bp.route("/api/research/ensemble")
def get_ensemble():
    cfg = _load_research_config()
    pool = CandidatePool(research_store=_make_research_store(cfg))
    candidates = pool.list_candidates()
    if len(candidates) < 2:
        return jsonify({"ensemble": None, "message": "Need at least 2 candidates"})
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    from quant.features.research.ensemble.ensemble import StrategyEnsemble
    ensemble = StrategyEnsemble(store, cfg.ensemble_config)
    ids = [c["id"] for c in candidates]
    result = ensemble.build(ids)
    return jsonify({"ensemble": {
        "strategy_ids": result.strategy_ids,
        "weights": result.weights,
        "portfolio_sharpe": result.portfolio_sharpe,
        "diversification_ratio": result.diversification_ratio,
        "mean_correlation": result.mean_correlation,
        "effective_n": result.effective_n,
    }})


@research_bp.route("/api/research/ensemble/rebuild", methods=["POST"])
def rebuild_ensemble():
    return get_ensemble()
