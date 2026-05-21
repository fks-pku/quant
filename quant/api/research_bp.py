import importlib.util
import hashlib
import json
import os
import pickle
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
    STAGE_REPORT_HTML,
    latest_stage_report_html_path,
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
        info = _candidate_info_for_backtest(sid, integrator, pool)
        strategy_class = registry.get(sid)
        if strategy_class is None:
            archive_dir = ((info or {}).get("research_meta") or {}).get("rejected_strategy_dir")
            strategy_class = _load_archived_strategy_class(sid, archive_dir)
        if strategy_class is None:
            result.errors.append(f"Strategy {sid} not in registry for backtest")
            return

        symbols = _candidate_symbols(info, config.default_symbols)
        start = datetime.strptime(config.default_backtest_start, "%Y-%m-%d")
        end = datetime.strptime(config.default_backtest_end, "%Y-%m-%d")
        is_cn = any(is_cn_symbol(sym) for sym in symbols)
        initial_cash = 500000 if is_cn else 100000
        execution_cost_model = _strict_execution_cost_model(sid, info, is_cn)

        db_provider = DuckDBProvider()
        db_provider.connect()
        lot_sizes = {}
        benchmark_provider = None
        benchmark_meta = {"symbol": "", "coverage_start": "", "coverage_end": "", "rows": 0, "fallback_used": False}
        survivorship_audit = {}
        try:
            if _candidate_requires_market_cap(info):
                market_cap_cols = _available_market_cap_columns(db_provider)
                if not market_cap_cols:
                    strict_report = _strict_backtest_blocked_report(
                        sid,
                        start,
                        end,
                        initial_cash,
                        symbols,
                        reason="Missing point-in-time market cap field: expected one of total_mv, circ_mv, market_cap, total_market_cap",
                    )
                    result.backtested += 1
                    if info is not None:
                        info["backtest"] = strict_report
                        meta = info.setdefault("research_meta", {})
                        meta["backtest_result"] = strict_report
                        meta["strict_backtest_result"] = strict_report
                        _persist_candidate_backtest(pool, sid, info, strict_report)
                    _update_hypothesis_backtest(
                        pool,
                        sid,
                        strict_report,
                        "rejected",
                        "backtest",
                        strict_report["data_blocked"]["reason"],
                    )
                    result.rejected += 1
                    result.log.append(ResearchLogEntry(
                        phase="backtest", title=sid,
                        source="", source_url="", verdict="fail",
                        reason=strict_report["data_blocked"]["reason"],
                        scores={"sharpe": 0.0, "trades": 0, "data_blocked": True},
                    ))
                    return
            use_streaming_provider = is_cn and len(symbols) > 1000
            data_df = None
            streaming_provider = None
            if use_streaming_provider:
                streaming_provider = _DuckDBDailyDateProvider(
                    symbols,
                    start,
                    end,
                    include_daily_basic=_candidate_requires_market_cap(info),
                    include_execution_liquidity_features=bool(execution_cost_model and execution_cost_model.get("enabled")),
                )
            else:
                data_df = db_provider.get_bars_for_symbols(symbols, start, end, "1d")
                data_df = _add_execution_liquidity_features(data_df, execution_cost_model)
            lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
            if is_cn:
                benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
                survivorship_audit = _cn_survivorship_audit(
                    db_provider,
                    start,
                    end,
                    formula_key=_candidate_formula_key(info),
                )
        finally:
            db_provider.disconnect()

        if streaming_provider is None and (data_df is None or data_df.empty):
            result.errors.append(f"No data for {sid}")
            return

        data_provider = streaming_provider or DataFrameProvider(data_df)
        strategy = strategy_class(symbols=symbols)

        backtest_config = {"slippage_bps": 5}
        if execution_cost_model:
            backtest_config["execution_cost_model"] = execution_cost_model
        commission_cfg = {
            "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
            "HK": {"type": "hk_realistic"},
            "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
        }
        bt_config = {
            "backtest": backtest_config,
            "execution": {"commission": commission_cfg},
            "data": {"default_timeframe": "1d"},
            "risk": _candidate_backtest_risk_config(info),
        }

        backtester = Backtester(
            bt_config,
            portfolio_class=Portfolio,
            risk_engine_class=RiskEngine,
            sub_portfolio_class=SubPortfolio,
            lot_sizes=lot_sizes,
            benchmark_provider=benchmark_provider,
        )
        try:
            bt_result = backtester.run(
                start=start,
                end=end,
                strategies=[strategy],
                initial_cash=initial_cash,
                data_provider=data_provider,
                symbols=symbols,
            )
        finally:
            close_data_provider = getattr(data_provider, "close", None)
            if callable(close_data_provider):
                close_data_provider()
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
            survivorship_audit,
            {**backtest_config, "commission": commission_cfg},
        )
        result.backtested += 1

        if info is not None:
            info["backtest"] = strict_report
            meta = info.setdefault("research_meta", {})
            meta["backtest_result"] = strict_report
            meta["strict_backtest_result"] = strict_report
            _persist_candidate_backtest(pool, sid, info, strict_report)
            audit_material = bool((strict_report.get("data_quality") or {}).get("survivorship_audit", {}).get("material"))
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
                    f"Strict Backtester Sharpe {bt_result.sharpe_ratio:.2f}"
                    + ("; survivorship audit requires review" if audit_material else ""),
                )
                result.log.append(ResearchLogEntry(
                    phase="backtest", title=info.get("name", sid),
                    source="", source_url="", verdict="warning" if audit_material else "pass",
                    reason=(
                        f"Sharpe {bt_result.sharpe_ratio:.2f}; survivorship audit requires review"
                        if audit_material
                        else f"Sharpe {bt_result.sharpe_ratio:.2f}"
                    ),
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


def _candidate_requires_market_cap(info):
    meta = dict((info or {}).get("research_meta") or {})
    spec = dict(meta.get("strategy_spec") or {})
    required = {str(field) for field in spec.get("required_fields") or []}
    formula = str(spec.get("signal_formula_key") or "")
    return any("market_cap" in field for field in required) or formula in {
        "joinquant_small_cap_size_factor",
        "joinquant_small_cap_low_price_factor",
    }


def _candidate_formula_key(info):
    meta = dict((info or {}).get("research_meta") or {})
    spec = dict(meta.get("strategy_spec") or {})
    return str(spec.get("signal_formula_key") or "")


def _candidate_backtest_risk_config(info):
    meta = dict((info or {}).get("research_meta") or {})
    spec = dict(meta.get("strategy_spec") or {})
    max_position_pct = spec.get("max_position_pct", meta.get("max_position_pct", 0.20))
    try:
        max_position_pct = float(max_position_pct)
    except (TypeError, ValueError):
        max_position_pct = 0.20
    max_position_pct = min(max(max_position_pct, 0.01), 1.0)
    return {
        "max_position_pct": max_position_pct,
        "max_sector_pct": 1.0,
        "max_daily_loss_pct": 0.10,
        "max_leverage": 2.0,
    }


def _strict_execution_cost_model(strategy_id, info, is_cn):
    if not is_cn:
        return None
    meta = dict((info or {}).get("research_meta") or {})
    spec = dict(meta.get("strategy_spec") or {})
    text = " ".join(
        str(value or "")
        for value in (
            strategy_id,
            (info or {}).get("name"),
            (info or {}).get("description"),
            spec.get("signal_formula_key"),
            " ".join(str(field) for field in (spec.get("required_fields") or [])),
        )
    ).lower()
    if not any(token in text for token in ("small_cap", "low_price", "market_cap", "circ_mv", "total_mv")):
        return None
    return {
        "enabled": True,
        "name": "small_cap_realistic",
        "markets": ["CN"],
        "tick_size": 0.01,
        "half_spread_ticks": 0.5,
        "min_slippage_bps": 5,
        "max_participation_rate": 0.01,
        "impact_coefficient": 0.5,
        "volatility_fallback": 0.03,
        "adv_value_field": "adv20_value",
        "volatility_field": "volatility20",
    }


def _add_execution_liquidity_features(data_df, execution_cost_model):
    if not isinstance(execution_cost_model, dict) or not execution_cost_model.get("enabled"):
        return data_df
    if data_df is None or data_df.empty or not {"symbol", "timestamp", "close", "volume"}.issubset(data_df.columns):
        return data_df
    import pandas as pd

    frame = data_df.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    if "turnover" in frame.columns:
        value = pd.to_numeric(frame["turnover"], errors="coerce")
        implied = close * volume
        ratio = implied / value.where(value > 0)
        tushare_amount_units = ratio.between(5.0, 20.0) | ratio.between(500.0, 2000.0)
        value = value.where(~tushare_amount_units, value * 1000.0)
        value = value.where(value > 0, implied)
    else:
        value = close * volume
    frame["adv20_value"] = value.groupby(frame["symbol"]).transform(
        lambda item: item.shift(1).rolling(20, min_periods=1).mean()
    )
    returns = close.groupby(frame["symbol"], group_keys=False).pct_change(fill_method=None)
    frame["volatility20"] = returns.groupby(frame["symbol"]).transform(
        lambda item: item.shift(1).rolling(20, min_periods=2).std()
    )
    return frame


def _available_market_cap_columns(db_provider):
    wanted = {"total_mv", "circ_mv", "market_cap", "total_market_cap", "float_market_cap", "circulating_market_cap"}
    columns = set()
    try:
        rows = db_provider.storage.conn.execute("PRAGMA table_info('daily_cn_ochl')").fetchall()
    except Exception:
        rows = []
    columns.update(str(row[1]) for row in rows)
    daily_basic_columns = getattr(db_provider.storage, "_daily_basic_columns", None)
    if callable(daily_basic_columns):
        try:
            columns.update(str(column) for column in daily_basic_columns())
        except Exception:
            pass
    return [column for column in sorted(columns) if column in wanted]


def _cn_survivorship_audit(db_provider, start, end, formula_key=""):
    storage = getattr(db_provider, "storage", db_provider)
    conn = getattr(storage, "conn", None)
    audit = {
        "kind": "cn_survivorship_audit",
        "material": False,
        "formula_key": str(formula_key or ""),
    }
    if conn is None:
        audit["reason"] = "DuckDB connection unavailable; survivorship audit not run."
        return audit
    try:
        daily_basic_available = bool(storage._daily_basic_available()) if hasattr(storage, "_daily_basic_available") else False
        status_available = bool(storage._status_available()) if hasattr(storage, "_status_available") else False
    except Exception as exc:
        audit["reason"] = f"Survivorship audit preflight failed: {exc}"
        return audit
    audit["daily_basic_available"] = daily_basic_available
    audit["status_available"] = status_available
    if not daily_basic_available:
        audit["reason"] = "daily_basic sidecar unavailable; cannot compare historical listing universe."
        return audit

    start_date = start.date().isoformat() if hasattr(start, "date") else str(start)[:10]
    end_date = end.date().isoformat() if hasattr(end, "date") else str(end)[:10]
    status_cte = (
        "SELECT DISTINCT symbol FROM security_status.cn_security_status_daily "
        f"WHERE trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'"
        if status_available
        else "SELECT CAST(NULL AS VARCHAR) AS symbol WHERE FALSE"
    )
    try:
        coverage = _duckdb_fetch_one_dict(
            conn,
            f"""
            WITH dbs AS (
                SELECT DISTINCT symbol
                FROM daily_basic.cn_daily_basic
                WHERE trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
            ),
            ss AS ({status_cte}),
            os AS (
                SELECT DISTINCT symbol
                FROM daily_cn_ochl
                WHERE CAST(timestamp AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'
            )
            SELECT
                (SELECT COUNT(*) FROM dbs) AS daily_basic_symbols,
                (SELECT COUNT(*) FROM ss) AS status_symbols,
                (SELECT COUNT(*) FROM os) AS ohlc_symbols,
                (SELECT COUNT(*) FROM dbs WHERE symbol NOT IN (SELECT symbol FROM ss)) AS daily_basic_not_status_symbols,
                (SELECT COUNT(*) FROM dbs WHERE symbol NOT IN (SELECT symbol FROM os)) AS daily_basic_not_ohlc_symbols,
                (SELECT COUNT(*) FROM ss WHERE symbol NOT IN (SELECT symbol FROM dbs)) AS status_not_daily_basic_symbols
            """,
        )
        audit.update(coverage)
        missing_summary = _duckdb_fetch_one_dict(
            conn,
            f"""
            WITH os AS (
                SELECT DISTINCT symbol
                FROM daily_cn_ochl
                WHERE CAST(timestamp AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'
            ),
            missing AS (
                SELECT
                    db.symbol,
                    db.trade_date,
                    db.total_mv,
                    db.total_mv / NULLIF(db.total_share, 0) AS inferred_price
                FROM daily_basic.cn_daily_basic db
                LEFT JOIN os USING(symbol)
                WHERE db.trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
                  AND os.symbol IS NULL
                  AND db.total_mv > 0
                  AND db.total_share > 0
            )
            SELECT
                COUNT(*) AS missing_daily_basic_rows,
                COUNT(DISTINCT symbol) AS missing_daily_basic_symbols,
                SUM(CASE WHEN inferred_price > 0 AND inferred_price <= 20 THEN 1 ELSE 0 END) AS missing_low_price_rows,
                COUNT(DISTINCT CASE WHEN inferred_price > 0 AND inferred_price <= 20 THEN symbol END) AS missing_low_price_symbols,
                COUNT(DISTINCT CASE WHEN inferred_price > 0 AND inferred_price <= 20
                    AND NOT (starts_with(symbol, '920') OR starts_with(symbol, '8') OR starts_with(symbol, '4')) THEN symbol END)
                    AS missing_low_price_symbols_excluding_920,
                MIN(CASE WHEN inferred_price > 0 AND inferred_price <= 20 THEN total_mv END) AS min_missing_low_price_total_mv
            FROM missing
            """,
        )
        audit.update(missing_summary)
        included_date_expr = "s.trade_date" if status_available else "CAST(b.timestamp AS DATE)"
        included_symbol_expr = "s.symbol" if status_available else "b.symbol"
        included_from = (
            "security_status.cn_security_status_daily s JOIN daily_cn_ochl b "
            "ON s.symbol = b.symbol AND s.trade_date = CAST(b.timestamp AS DATE)"
            if status_available
            else "daily_cn_ochl b"
        )
        included_status_filter = (
            f"AND s.trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}' "
            "AND s.tradable = TRUE AND s.is_st = FALSE"
            if status_available
            else ""
        )
        top20_summary = _duckdb_fetch_one_dict(
            conn,
            f"""
            WITH included AS (
                SELECT {included_date_expr} AS trade_date, {included_symbol_expr} AS symbol, db.total_mv, b.close
                FROM {included_from}
                JOIN daily_basic.cn_daily_basic db
                  ON b.symbol = db.symbol
                 AND CAST(b.timestamp AS DATE) = db.trade_date
                WHERE CAST(b.timestamp AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'
                  {included_status_filter}
                  AND b.close > 0
                  AND b.close <= 20
                  AND db.total_mv > 0
            ),
            ranked AS (
                SELECT
                    trade_date,
                    symbol,
                    total_mv,
                    ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY total_mv ASC, symbol ASC) AS rn
                FROM included
            ),
            threshold AS (
                SELECT trade_date, MAX(total_mv) AS top20_mv_threshold
                FROM ranked
                WHERE rn <= 20
                GROUP BY trade_date
            ),
            os AS (
                SELECT DISTINCT symbol
                FROM daily_cn_ochl
                WHERE CAST(timestamp AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'
            ),
            missing AS (
                SELECT
                    db.trade_date,
                    db.symbol,
                    db.total_mv,
                    db.total_mv / NULLIF(db.total_share, 0) AS inferred_price
                FROM daily_basic.cn_daily_basic db
                LEFT JOIN os USING(symbol)
                WHERE db.trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
                  AND os.symbol IS NULL
                  AND db.total_mv > 0
                  AND db.total_share > 0
                  AND db.total_mv / NULLIF(db.total_share, 0) > 0
                  AND db.total_mv / NULLIF(db.total_share, 0) <= 20
            ),
            displaced AS (
                SELECT m.*
                FROM missing m
                JOIN threshold t ON m.trade_date = t.trade_date
                WHERE m.total_mv < t.top20_mv_threshold
            ),
            displaced_ex_920 AS (
                SELECT *
                FROM displaced
                WHERE NOT (starts_with(symbol, '920') OR starts_with(symbol, '8') OR starts_with(symbol, '4'))
            ),
            by_date_ex_920 AS (
                SELECT trade_date, COUNT(*) AS n_symbols
                FROM displaced_ex_920
                GROUP BY trade_date
            )
            SELECT
                (SELECT COUNT(DISTINCT trade_date) FROM displaced) AS dates_with_missing_below_top20,
                (SELECT COUNT(*) FROM displaced) AS missing_rows_below_top20,
                (SELECT COUNT(DISTINCT symbol) FROM displaced) AS missing_symbols_below_top20,
                (SELECT COUNT(DISTINCT trade_date) FROM displaced_ex_920) AS dates_with_missing_below_top20_excluding_920,
                (SELECT COUNT(*) FROM displaced_ex_920) AS missing_rows_below_top20_excluding_920,
                (SELECT COUNT(DISTINCT symbol) FROM displaced_ex_920) AS missing_symbols_below_top20_excluding_920,
                (SELECT MAX(n_symbols) FROM by_date_ex_920) AS max_missing_below_top20_per_date_excluding_920
            """,
        )
        audit.update(top20_summary)
        samples = _duckdb_fetch_dicts(
            conn,
            f"""
            WITH os AS (
                SELECT DISTINCT symbol
                FROM daily_cn_ochl
                WHERE CAST(timestamp AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'
            ),
            per_symbol AS (
                SELECT
                    db.symbol,
                    MIN(db.trade_date) AS first_date,
                    MAX(db.trade_date) AS last_date,
                    COUNT(*) AS n_rows,
                    SUM(CASE WHEN db.total_mv / NULLIF(db.total_share, 0) > 0
                              AND db.total_mv / NULLIF(db.total_share, 0) <= 20 THEN 1 ELSE 0 END) AS low_price_rows,
                    MIN(CASE WHEN db.total_mv / NULLIF(db.total_share, 0) > 0
                              AND db.total_mv / NULLIF(db.total_share, 0) <= 20 THEN db.total_mv END) AS min_total_mv,
                    MIN(db.total_mv / NULLIF(db.total_share, 0)) AS min_inferred_price
                FROM daily_basic.cn_daily_basic db
                LEFT JOIN os USING(symbol)
                WHERE db.trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
                  AND os.symbol IS NULL
                  AND NOT (starts_with(db.symbol, '920') OR starts_with(db.symbol, '8') OR starts_with(db.symbol, '4'))
                  AND db.total_mv > 0
                  AND db.total_share > 0
                GROUP BY db.symbol
            )
            SELECT symbol, first_date, last_date, n_rows, low_price_rows, min_total_mv, min_inferred_price
            FROM per_symbol
            WHERE low_price_rows > 0
            ORDER BY last_date DESC, min_total_mv ASC
            LIMIT 12
            """,
        )
        audit["sample_missing_symbols"] = samples
    except Exception as exc:
        audit["reason"] = f"Survivorship audit failed: {exc}"
        return audit

    missing_below_top20 = int(audit.get("missing_symbols_below_top20_excluding_920") or 0)
    missing_ohlc = int(audit.get("daily_basic_not_ohlc_symbols") or 0)
    audit["material"] = missing_below_top20 > 0
    if audit["material"]:
        audit["reason"] = (
            f"daily_basic has {missing_ohlc} symbols absent from OHLC; "
            f"{missing_below_top20} Shanghai/Shenzhen symbols would fall below the current Top20 small-cap threshold. "
            "Strict backtest may be upward biased until historical OHLC/status coverage is completed."
        )
    else:
        audit["reason"] = "No missing Shanghai/Shenzhen daily_basic symbols fell below the current Top20 small-cap threshold."
    return audit


def _duckdb_fetch_one_dict(conn, query, params=None):
    cursor = conn.execute(query, params or [])
    row = cursor.fetchone()
    columns = [item[0] for item in cursor.description]
    if row is None:
        return {column: 0 for column in columns}
    return {column: _json_scalar(row[index]) for index, column in enumerate(columns)}


def _duckdb_fetch_dicts(conn, query, params=None):
    cursor = conn.execute(query, params or [])
    columns = [item[0] for item in cursor.description]
    return [
        {column: _json_scalar(row[index]) for index, column in enumerate(columns)}
        for row in cursor.fetchall()
    ]


def _json_scalar(value):
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def _candidate_info_for_backtest(sid, integrator, pool):
    info = None
    if integrator is not None and hasattr(integrator, "get_registry_entry"):
        try:
            info = integrator.get_registry_entry(sid)
        except Exception:
            info = None
    if info is not None:
        return info
    store = getattr(pool, "research_store", None)
    if store is not None and hasattr(store, "get_candidate"):
        try:
            info = store.get_candidate(sid)
        except Exception:
            info = None
    if info is not None:
        return info
    return _archived_candidate_info(sid)


def _archived_candidate_info(sid):
    archive_dir = Path(__file__).resolve().parent.parent / "features" / "rejected_strategy" / str(sid)
    config_path = archive_dir / "config.yaml"
    strategy_path = archive_dir / "strategy.py"
    if not config_path.exists() and not strategy_path.exists():
        return None
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}
    params = dict(config.get("parameters") or {})
    strategy_cfg = dict(config.get("strategy") or {})
    symbols = [str(symbol) for symbol in params.get("symbols") or [] if _is_a_share_symbol(str(symbol))]
    formula_key = "joinquant_small_cap_size_factor"
    if "low_price" in str(sid):
        formula_key = "joinquant_small_cap_low_price_factor"
    return {
        "id": str(sid),
        "name": strategy_cfg.get("name") or str(sid),
        "description": f"Archived research strategy {sid}",
        "status": strategy_cfg.get("status") or "rejected",
        "parameters": params,
        "research_meta": {
            "source": "archived_rejected_strategy",
            "rejected_strategy_dir": str(archive_dir),
            "strategy_spec": {
                "strategy_id": str(sid),
                "strategy_type": "factor",
                "signal_formula_key": formula_key,
                "required_fields": ["close", "market_cap", "turnover"],
                "lookback_days": int(params.get("lookback", 1) or 1),
                "horizon_days": int(params.get("holding_days", 5) or 5),
                "execution_lag_days": 1,
                "universe": symbols,
            },
        },
    }


class _BarRecordBatch:
    def __init__(self, records):
        self.records = records

    def __iter__(self):
        return iter(self.records)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        return self.records[index]


_STREAMING_PROVIDER_CACHE_VERSION = 1
_STREAMING_PROVIDER_CACHE_MIN_SYMBOLS = 1000


def _streaming_provider_cache_dir():
    return Path(__file__).resolve().parents[1] / "infrastructure" / "var" / "research" / "cache" / "daily_date_provider"


def _file_fingerprint(path):
    item = Path(path)
    try:
        stat = item.stat()
    except OSError:
        return {"path": str(item), "exists": False}
    return {
        "path": str(item.resolve()),
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


class _DuckDBDailyDateProvider:
    def __init__(
        self,
        symbols,
        start,
        end,
        db_path=None,
        status_db_path=None,
        daily_basic_db_path=None,
        financial_indicator_db_path=None,
        include_daily_basic=True,
        include_financial_indicators=False,
        include_execution_liquidity_features=False,
        cache_dir=None,
        cache_enabled=None,
    ):
        import pandas as pd
        from quant.infrastructure.data.storage_duckdb import (
            DuckDBStorage,
            _DEFAULT_DAILY_BASIC_DB,
            _DEFAULT_DB,
            _DEFAULT_FINANCIAL_INDICATOR_DB,
            _DEFAULT_STATUS_DB,
            _FINANCIAL_INDICATOR_SCHEMA,
            _FINANCIAL_INDICATOR_TABLE,
        )

        self.symbols = list(dict.fromkeys(str(symbol) for symbol in symbols))
        self._symbol_lookup = set(self.symbols)
        self._start_day = pd.Timestamp(start).date()
        self._end_day = pd.Timestamp(end).date()
        self._include_daily_basic = bool(include_daily_basic)
        self._include_financial_indicators = bool(include_financial_indicators)
        self._include_execution_liquidity_features = bool(include_execution_liquidity_features)
        self._financial_indicator_schema = _FINANCIAL_INDICATOR_SCHEMA
        self._financial_indicator_table = _FINANCIAL_INDICATOR_TABLE
        self._storage = DuckDBStorage(
            db_path or _DEFAULT_DB,
            read_only=True,
            use_security_status=True,
            status_db_path=status_db_path or _DEFAULT_STATUS_DB,
            daily_basic_db_path=daily_basic_db_path or _DEFAULT_DAILY_BASIC_DB,
            financial_indicator_db_path=financial_indicator_db_path or _DEFAULT_FINANCIAL_INDICATOR_DB,
        )
        self._bar_columns = self._resolve_bar_columns()
        rows = self._storage.conn.execute(
            """
            SELECT DISTINCT CAST(timestamp AS DATE) AS trade_date
            FROM daily_cn_ochl
            WHERE CAST(timestamp AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ORDER BY trade_date
            """,
            [start, end],
        ).fetchall()
        self._trading_dates_list = [pd.Timestamp(row[0]).date() for row in rows]
        self.trading_dates = set(self._trading_dates_list)
        self._chunk_size = 63
        self._cached_rows_by_date = {}
        self._cache_enabled = (
            len(self.symbols) >= _STREAMING_PROVIDER_CACHE_MIN_SYMBOLS
            if cache_enabled is None
            else bool(cache_enabled)
        )
        self._cache_dir = Path(cache_dir) if cache_dir is not None else _streaming_provider_cache_dir()
        self._cache_key_base = self._build_cache_key_base()

    def get_bars_for_date(self, trading_date):
        import pandas as pd

        day = pd.Timestamp(trading_date).date()
        if day not in self._cached_rows_by_date:
            self._load_chunk(day)
        return self._cached_rows_by_date.get(day, [])

    def _load_chunk(self, day):
        import bisect
        import pandas as pd

        if not self._trading_dates_list:
            self._cached_rows_by_date = {day: []}
            return
        index = bisect.bisect_left(self._trading_dates_list, day)
        if index >= len(self._trading_dates_list):
            self._cached_rows_by_date = {day: []}
            return
        chunk_dates = self._trading_dates_list[index:index + self._chunk_size]
        start_day = chunk_dates[0]
        end_day = chunk_dates[-1]
        lookback_start_day = start_day
        if self._include_execution_liquidity_features:
            lookback_index = max(0, index - 25)
            lookback_start_day = self._trading_dates_list[lookback_index]
        if self._load_cached_chunk(chunk_dates, lookback_start_day, end_day):
            return
        frame = self._fetch_frame(lookback_start_day, end_day)
        if self._include_execution_liquidity_features:
            frame = _add_execution_liquidity_features(frame, {"enabled": True})
        grouped = {item: [] for item in chunk_dates}
        if frame is not None and not frame.empty:
            if "symbol" in frame.columns:
                frame = frame[frame["symbol"].astype(str).isin(self._symbol_lookup)]
            if not frame.empty:
                timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
                frame = frame.assign(_cache_date=timestamps.dt.date)
                frame = frame[frame["_cache_date"].isin(set(chunk_dates))]
                for record in frame.to_dict("records"):
                    cache_date = record.pop("_cache_date", None)
                    if cache_date in grouped:
                        grouped[cache_date].append(record)
        self._cached_rows_by_date = {
            item: _BarRecordBatch(records) if records else []
            for item, records in grouped.items()
        }
        self._store_cached_chunk(chunk_dates, lookback_start_day, end_day, grouped)

    def _build_cache_key_base(self):
        status_available = self._storage._status_available()
        include_basic = self._include_daily_basic and self._storage._daily_basic_available()
        include_financial = self._include_financial_indicators and self._financial_indicator_available()
        symbols_digest = hashlib.sha256("\n".join(sorted(self._symbol_lookup)).encode("utf-8")).hexdigest()
        return {
            "version": _STREAMING_PROVIDER_CACHE_VERSION,
            "symbols_digest": symbols_digest,
            "symbols_count": len(self._symbol_lookup),
            "start": self._start_day.isoformat(),
            "end": self._end_day.isoformat(),
            "bar_columns": list(self._bar_columns),
            "include_daily_basic": bool(self._include_daily_basic),
            "include_financial_indicators": bool(self._include_financial_indicators),
            "include_execution_liquidity_features": bool(self._include_execution_liquidity_features),
            "status_available": bool(status_available),
            "daily_basic_available": bool(include_basic),
            "financial_indicator_available": bool(include_financial),
            "market_db": _file_fingerprint(self._storage.db_path),
            "status_db": _file_fingerprint(self._storage._status_db_path) if status_available else None,
            "daily_basic_db": _file_fingerprint(self._storage._daily_basic_db_path) if include_basic else None,
            "financial_indicator_db": _file_fingerprint(self._storage._financial_indicator_db_path) if include_financial else None,
        }

    def _chunk_cache_path(self, chunk_dates, lookback_start_day, end_day):
        if not self._cache_enabled:
            return None
        key = {
            **self._cache_key_base,
            "chunk_start": chunk_dates[0].isoformat(),
            "chunk_end": chunk_dates[-1].isoformat(),
            "lookback_start": lookback_start_day.isoformat(),
            "fetch_end": end_day.isoformat(),
        }
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()
        return self._cache_dir / f"chunk_{digest}.pkl"

    def _load_cached_chunk(self, chunk_dates, lookback_start_day, end_day):
        cache_path = self._chunk_cache_path(chunk_dates, lookback_start_day, end_day)
        if cache_path is None or not cache_path.exists():
            return False
        try:
            with cache_path.open("rb") as handle:
                payload = pickle.load(handle)
        except Exception:
            return False
        if payload.get("version") != _STREAMING_PROVIDER_CACHE_VERSION:
            return False
        expected_dates = [item.isoformat() for item in chunk_dates]
        if payload.get("chunk_dates") != expected_dates:
            return False
        rows_by_date = payload.get("rows_by_date")
        if not isinstance(rows_by_date, dict):
            return False
        grouped = {}
        for item in chunk_dates:
            records = rows_by_date.get(item.isoformat(), [])
            grouped[item] = _BarRecordBatch(records) if records else []
        self._cached_rows_by_date = grouped
        return True

    def _store_cached_chunk(self, chunk_dates, lookback_start_day, end_day, grouped):
        cache_path = self._chunk_cache_path(chunk_dates, lookback_start_day, end_day)
        if cache_path is None:
            return
        payload = {
            "version": _STREAMING_PROVIDER_CACHE_VERSION,
            "chunk_dates": [item.isoformat() for item in chunk_dates],
            "rows_by_date": {item.isoformat(): records for item, records in grouped.items()},
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_path.with_suffix(".tmp")
            with tmp_path.open("wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, cache_path)
        except Exception:
            return

    def _fetch_frame(self, start_day, end_day):
        placeholders = ", ".join("?" for _ in self.symbols)
        symbol_params = list(self.symbols)
        if self._storage._status_available():
            include_basic = self._include_daily_basic and self._storage._daily_basic_available()
            sidecar_select = ""
            sidecar_join = ""
            if include_basic:
                sidecar_select = """
                    db.turnover_rate,
                    db.turnover_rate_f,
                    db.volume_ratio,
                    db.pe,
                    db.pe_ttm,
                    db.pb,
                    db.ps,
                    db.ps_ttm,
                    db.dv_ratio,
                    db.dv_ttm,
                    db.total_share,
                    db.float_share,
                    db.free_share,
                    db.total_mv,
                    db.circ_mv,
                """
                sidecar_join = """
                LEFT JOIN daily_basic.cn_daily_basic db
                  ON s.symbol = db.symbol
                 AND s.trade_date = db.trade_date
                """
            bar_select = ",\n                    ".join(f"b.{column}" for column in self._bar_columns)
            frame = self._storage.conn.execute(
                f"""
                SELECT
                    CAST(s.trade_date AS TIMESTAMP) AS timestamp,
                    s.symbol,
                    {bar_select},
                    {sidecar_select}
                    s.is_st,
                    s.st_type,
                    s.is_suspended AS status_is_suspended,
                    s.has_daily_bar,
                    s.tradable,
                    s.up_limit,
                    s.down_limit,
                    s.pre_close AS status_pre_close,
                    s.is_listed,
                    s.list_status,
                    s.suspend_type,
                    s.suspend_timing
                FROM security_status.cn_security_status_daily s
                LEFT JOIN daily_cn_ochl b
                  ON s.symbol = b.symbol
                 AND s.trade_date = CAST(b.timestamp AS DATE)
                {sidecar_join}
                WHERE s.trade_date BETWEEN ? AND ?
                  AND s.symbol IN ({placeholders})
                """,
                [start_day, end_day, *symbol_params],
            ).fetchdf()
            return self._add_financial_indicators(self._storage._normalize_status_enriched_bars(frame))
        elif self._include_daily_basic and self._storage._daily_basic_available():
            frame = self._storage.conn.execute(
                f"""
                SELECT
                    b.timestamp,
                    b.symbol,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.turnover,
                    b.adj_open,
                    b.adj_high,
                    b.adj_low,
                    b.adj_close,
                    b.adj_factor,
                    db.total_mv,
                    db.circ_mv
                FROM daily_cn_ochl b
                LEFT JOIN daily_basic.cn_daily_basic db
                  ON b.symbol = db.symbol
                 AND CAST(b.timestamp AS DATE) = db.trade_date
                WHERE CAST(b.timestamp AS DATE) BETWEEN ? AND ?
                  AND b.symbol IN ({placeholders})
                """,
                [start_day, end_day, *symbol_params],
            ).fetchdf()
            return self._add_financial_indicators(frame)
        else:
            bar_select = ", ".join(f"b.{column}" for column in self._bar_columns)
            frame = self._storage.conn.execute(
                f"""
                SELECT b.timestamp, b.symbol, {bar_select}
                FROM daily_cn_ochl b
                WHERE CAST(b.timestamp AS DATE) BETWEEN ? AND ?
                  AND b.symbol IN ({placeholders})
                """,
                [start_day, end_day, *symbol_params],
            ).fetchdf()
            return self._add_financial_indicators(frame)

    def _financial_indicator_available(self):
        if not self._include_financial_indicators:
            return False
        if not self._storage._financial_indicator_db_path.exists():
            return False
        if not self._storage._ensure_sidecar_attached(
            self._financial_indicator_schema,
            self._storage._financial_indicator_db_path,
        ):
            return False
        return self._storage._table_exists(f"{self._financial_indicator_schema}.{self._financial_indicator_table}")

    def _financial_indicator_columns(self):
        if not self._financial_indicator_available():
            return []
        rows = self._storage.conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_catalog = ?
              AND table_name = ?
            ORDER BY ordinal_position
            """,
            [self._financial_indicator_schema, self._financial_indicator_table],
        ).fetchall()
        keys = {"symbol", "ts_code", "ann_date", "end_date", "updated_at"}
        return [str(row[0]) for row in rows if str(row[0]) not in keys]

    def _add_financial_indicators(self, frame):
        if frame is None or frame.empty:
            return frame
        columns = self._financial_indicator_columns()
        if not columns:
            return frame
        import pandas as pd

        result = frame.copy()
        result["_finance_order"] = range(len(result))
        result["_trade_ts"] = pd.to_datetime(result["timestamp"], errors="coerce")
        symbols = [str(symbol) for symbol in result["symbol"].dropna().astype(str).unique().tolist()]
        if not symbols:
            return result.drop(columns=["_finance_order", "_trade_ts"])
        end_day = result["_trade_ts"].dt.date.max()
        placeholders = ", ".join("?" for _ in symbols)
        select_cols = ", ".join(["symbol", "ann_date", "end_date", *columns])
        financial = self._storage.conn.execute(
            f"""
            SELECT {select_cols}
            FROM {self._financial_indicator_schema}.{self._financial_indicator_table}
            WHERE symbol IN ({placeholders})
              AND ann_date <= ?
            ORDER BY symbol, ann_date, end_date
            """,
            [*symbols, end_day],
        ).fetchdf()
        if financial.empty:
            for column in columns:
                if column not in result.columns:
                    result[column] = pd.NA
            return result.drop(columns=["_finance_order", "_trade_ts"])
        financial["_ann_ts"] = pd.to_datetime(financial["ann_date"], errors="coerce")
        pieces = []
        finance_columns = ["_ann_ts", *columns]
        for symbol, group in result.groupby("symbol", sort=False):
            symbol_financial = financial[financial["symbol"].astype(str) == str(symbol)].sort_values(["_ann_ts", "end_date"])
            ordered_group = group.sort_values("_trade_ts")
            if symbol_financial.empty:
                merged = ordered_group.copy()
                for column in columns:
                    if column not in merged.columns:
                        merged[column] = pd.NA
            else:
                merged = pd.merge_asof(
                    ordered_group,
                    symbol_financial[finance_columns],
                    left_on="_trade_ts",
                    right_on="_ann_ts",
                    direction="backward",
                )
            pieces.append(merged)
        merged_frame = pd.concat(pieces, ignore_index=True).sort_values("_finance_order")
        return merged_frame.drop(columns=[column for column in ("_finance_order", "_trade_ts", "_ann_ts") if column in merged_frame.columns])

    def close(self):
        self._storage.close()

    def _resolve_bar_columns(self):
        wanted = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
            "adj_factor",
        ]
        rows = self._storage.conn.execute("PRAGMA table_info('daily_cn_ochl')").fetchall()
        available = {str(row[1]) for row in rows}
        return [column for column in wanted if column in available]


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


def _load_lot_sizes(db_provider, symbols, is_cn_symbol):
    lot_sizes = {sym: 100 if is_cn_symbol(sym) else 1 for sym in symbols}
    try:
        meta = db_provider.storage.get_all_instrument_meta()
    except Exception:
        return lot_sizes
    if meta is None or meta.empty or "symbol" not in meta.columns or "lot_size" not in meta.columns:
        return lot_sizes

    wanted = set(symbols)
    for row in meta[["symbol", "lot_size"]].itertuples(index=False):
        sym = str(row.symbol)
        if sym not in wanted:
            continue
        try:
            size = int(row.lot_size)
        except (TypeError, ValueError):
            continue
        if size > 0:
            lot_sizes[sym] = size
    return lot_sizes


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
    survivorship_audit=None,
    backtest_config=None,
):
    metrics = getattr(bt_result, "metrics", None)
    diagnostics = getattr(bt_result, "diagnostics", None)
    days = max(1, (end - start).days)
    cagr = (float(bt_result.final_nav) / float(initial_cash)) ** (365.25 / days) - 1.0 if initial_cash > 0 else 0.0
    max_drawdown_pct = _metric_float(getattr(bt_result, "max_drawdown_pct", 0.0))
    stat_sig = getattr(metrics, "statistical_significance", {}) if metrics is not None else {}
    benchmark_metrics = _benchmark_equity_metrics(benchmark_equity_curve, initial_cash, start, end)
    strategy_equity_curve = getattr(bt_result, "equity_curve", None)
    diagnostics_dict = _diagnostics_dict(diagnostics)
    execution_observations = list(getattr(diagnostics, "execution_observations", []) or [])
    execution_observations = _enrich_execution_observations_with_adv20(execution_observations, start, end, symbols)
    exposure_snapshots = list(getattr(diagnostics, "exposure_snapshots", []) or [])
    trades = list(getattr(bt_result, "trades", []) or [])
    supplemental = _strict_supplemental_diagnostics(
        bt_result,
        trades,
        strategy_equity_curve,
        benchmark_equity_curve,
        execution_observations,
        exposure_snapshots,
        initial_cash,
        start,
        end,
        diagnostics_dict,
    )
    final_nav = _metric_float(getattr(bt_result, "final_nav", 0.0))
    frozen_nav = _metric_float(diagnostics_dict.get("final_suspended_holding_nav", 0.0))
    if not isinstance(final_nav, str) and not isinstance(frozen_nav, str):
        frozen_zero_nav = max(0.0, float(final_nav) - float(frozen_nav))
        diagnostics_dict["final_suspended_holding_nav_pct_of_final_nav"] = (
            float(frozen_nav) / float(final_nav) if float(final_nav) > 0 else 0.0
        )
        diagnostics_dict["frozen_zero_final_nav"] = frozen_zero_nav
        diagnostics_dict["frozen_zero_total_return"] = (
            frozen_zero_nav / float(initial_cash) - 1.0 if float(initial_cash) > 0 else 0.0
        )
        diagnostics_dict["frozen_zero_cagr"] = (
            (frozen_zero_nav / float(initial_cash)) ** (365.25 / days) - 1.0
            if float(initial_cash) > 0 and frozen_zero_nav > 0
            else -1.0
        )
    backtest_config = dict(backtest_config or {})
    execution_cost_model = backtest_config.get("execution_cost_model")
    volume_limit = "Backtester execution diagnostics record volume_limited_trades."
    if isinstance(execution_cost_model, dict) and execution_cost_model.get("enabled"):
        try:
            participation = float(execution_cost_model.get("max_participation_rate"))
            participation_text = f"{participation:.2%}"
        except (TypeError, ValueError):
            participation_text = "configured rate"
        volume_limit = f"Liquidity cap uses max_participation_rate={participation_text} when ADV/value data is available."
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
            "winning_trades": int(getattr(metrics, "winning_trades", 0) if metrics is not None else 0),
            "losing_trades": int(getattr(metrics, "losing_trades", 0) if metrics is not None else 0),
            "avg_trade_duration_days": _duration_days(getattr(bt_result, "avg_trade_duration", None)),
            "payoff_ratio": _metric_float(getattr(metrics, "payoff_ratio", 0.0) if metrics is not None else 0.0),
            "expectancy": _metric_float(getattr(metrics, "expectancy", 0.0) if metrics is not None else 0.0),
            "gain_to_pain_ratio": _metric_float(getattr(metrics, "gain_to_pain_ratio", 0.0) if metrics is not None else 0.0),
            "ulcer_index": _metric_float(getattr(metrics, "ulcer_index", 0.0) if metrics is not None else 0.0),
            "tail_ratio": _metric_float(getattr(metrics, "tail_ratio", 0.0) if metrics is not None else 0.0),
            "recovery_factor": _metric_float(getattr(metrics, "recovery_factor", 0.0) if metrics is not None else 0.0),
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
            "up_capture": _metric_float(getattr(metrics, "up_capture", None) if metrics is not None else None),
            "down_capture": _metric_float(getattr(metrics, "down_capture", None) if metrics is not None else None),
            "benchmark_yearly_returns": _yearly_returns_from_equity(benchmark_equity_curve, initial_cash),
        },
        "diagnostics": diagnostics_dict,
        **supplemental,
        "guard_diagnostics": _strategy_guard_diagnostics(strategy),
        "data_quality": {
            "survivorship_audit": dict(survivorship_audit or {}),
        },
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
            "volume_limit": volume_limit,
            "price_limits": "Backtester execution diagnostics record limit_rejected_orders.",
            "commission": backtest_config.get("commission") or {"CN": "cn_realistic", "HK": "hk_realistic", "US": "per_share"},
            "slippage_bps": backtest_config.get("slippage_bps", 5),
            "execution_cost_model": execution_cost_model,
            "strategy_max_position_pct": _metric_float(getattr(strategy, "max_position_pct", None)),
            "strategy_max_positions": int(getattr(strategy, "max_positions", 0) or 0),
            "delisting_risk_guard": {
                "enabled": bool(getattr(strategy, "delisting_risk_guard", False)),
                "min_trade_price": _metric_float(getattr(strategy, "min_trade_price", 0.0)),
                "min_avg_turnover": _metric_float(getattr(strategy, "min_avg_turnover", 0.0)),
                "liquidity_lookback": int(getattr(strategy, "liquidity_lookback", 0) or 0),
                "max_recent_suspended_days": int(getattr(strategy, "max_recent_suspended_days", 0) or 0),
            },
        },
    }


def _strict_backtest_blocked_report(sid, start, end, initial_cash, symbols, reason):
    return {
        "framework": "Backtester preflight",
        "period": f"{start.date()}-{end.date()}",
        "initial_cash": float(initial_cash),
        "symbols": list(symbols),
        "metrics": {
            "sharpe": 0.0,
            "sortino": 0.0,
            "cagr": 0.0,
            "total_return": 0.0,
            "max_drawdown_pct": 0.0,
            "calmar_ratio": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "round_trip_trades": 0,
            "t_stat": 0.0,
            "p_value": 1.0,
        },
        "benchmark": {
            "symbol": "",
            "coverage_start": "",
            "coverage_end": "",
            "rows": 0,
            "fallback_used": False,
            "benchmark_return": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "information_ratio": 0.0,
            "tracking_error": 0.0,
            "benchmark_yearly_returns": {},
        },
        "diagnostics": {
            "fill_count": 0,
            "total_commission": 0.0,
            "cost_drag_pct": 0.0,
            "volume_limited_trades": 0,
            "lot_adjusted_trades": 0,
            "t1_rejected_sells": 0,
            "limit_rejected_orders": 0,
            "discarded_orders": 0,
            "expired_orders": 0,
            "submission_rejected": 0,
            "data_blocked": 1,
        },
        "equity_curve": {"strategy": [], "benchmark": []},
        "yearly_returns": {},
        "constraints": {
            "hfq_signal_policy": "Strategy helpers use adj_* prices for signal logic; raw close is reserved for order sizing/fill accounting.",
            "long_only": True,
            "t_plus_1": True,
            "cn_lot_size": 100,
            "commission": {"CN": "cn_realistic", "HK": "hk_realistic", "US": "per_share"},
            "slippage_bps": 5,
            "required_point_in_time_market_cap": True,
        },
        "data_blocked": {
            "strategy_id": sid,
            "reason": reason,
            "required_fields": ["total_mv", "circ_mv", "market_cap", "total_market_cap"],
        },
    }


def _strict_supplemental_diagnostics(
    bt_result,
    trades,
    equity_curve,
    benchmark_equity_curve,
    execution_observations,
    exposure_snapshots,
    initial_cash,
    start,
    end,
    diagnostics_dict,
):
    return {
        "turnover": _turnover_diagnostics(trades, equity_curve, initial_cash, start, end),
        "exposure": _exposure_diagnostics(exposure_snapshots),
        "capacity": _capacity_diagnostics(execution_observations, initial_cash),
        "trade_distribution": _trade_distribution(trades),
        "drawdown_episodes": _drawdown_episodes(equity_curve),
        "rolling_stability": _rolling_stability(equity_curve, benchmark_equity_curve),
        "regime_breakdown": _regime_breakdown(equity_curve, benchmark_equity_curve, initial_cash),
        "cost_decomposition": _cost_decomposition(bt_result, initial_cash, diagnostics_dict),
    }


def _turnover_diagnostics(trades, equity_curve, initial_cash, start, end):
    years = max(1.0 / 365.25, (end - start).days / 365.25)
    avg_nav = _series_mean(equity_curve) or float(initial_cash or 0.0)
    rows = []
    daily = {}
    buy_value = 0.0
    sell_value = 0.0
    for trade in trades or []:
        notional = abs(_safe_trade_number(getattr(trade, "quantity", 0.0)) * _safe_trade_number(getattr(trade, "fill_price", 0.0)))
        side = str(getattr(trade, "side", "") or "").upper()
        if side == "BUY":
            buy_value += notional
        elif side == "SELL":
            sell_value += notional
        date_text = _trade_date_text(trade)
        daily[date_text] = daily.get(date_text, 0.0) + notional
        rows.append(notional)
    gross_value = buy_value + sell_value
    one_way_value = min(buy_value, sell_value)
    avg_daily_value = sum(daily.values()) / len(daily) if daily else 0.0
    return {
        "gross_traded_value": _metric_float(gross_value),
        "buy_traded_value": _metric_float(buy_value),
        "sell_traded_value": _metric_float(sell_value),
        "one_way_traded_value": _metric_float(one_way_value),
        "annual_gross_turnover": _metric_float(gross_value / avg_nav / years if avg_nav > 0 else 0.0),
        "annual_one_way_turnover": _metric_float(one_way_value / avg_nav / years if avg_nav > 0 else 0.0),
        "avg_daily_traded_value": _metric_float(avg_daily_value),
        "max_daily_traded_value": _metric_float(max(daily.values()) if daily else 0.0),
        "traded_days": len(daily),
        "avg_trade_notional": _metric_float(sum(rows) / len(rows) if rows else 0.0),
        "max_trade_notional": _metric_float(max(rows) if rows else 0.0),
    }


def _exposure_diagnostics(snapshots):
    if not snapshots:
        return {}
    def values(key):
        return [_safe_trade_number(item.get(key, 0.0)) for item in snapshots]
    position_counts = values("position_count")
    gross = values("gross_exposure_pct")
    cash = values("cash_pct")
    max_weight = values("max_position_weight")
    return {
        "observations": len(snapshots),
        "avg_position_count": _metric_float(_mean(position_counts)),
        "min_position_count": _metric_float(min(position_counts) if position_counts else 0.0),
        "max_position_count": _metric_float(max(position_counts) if position_counts else 0.0),
        "avg_gross_exposure_pct": _metric_float(_mean(gross)),
        "min_gross_exposure_pct": _metric_float(min(gross) if gross else 0.0),
        "max_gross_exposure_pct": _metric_float(max(gross) if gross else 0.0),
        "avg_cash_pct": _metric_float(_mean(cash)),
        "min_cash_pct": _metric_float(min(cash) if cash else 0.0),
        "max_cash_pct": _metric_float(max(cash) if cash else 0.0),
        "avg_max_position_weight": _metric_float(_mean(max_weight)),
        "p95_max_position_weight": _metric_float(_quantile(max_weight, 0.95)),
        "max_position_weight": _metric_float(max(max_weight) if max_weight else 0.0),
    }


def _capacity_diagnostics(observations, initial_cash):
    if not observations:
        return {}
    adv_participation = [_safe_trade_number(item.get("adv_participation", 0.0)) for item in observations if _safe_trade_number(item.get("adv_participation", 0.0)) > 0]
    volume_participation = [_safe_trade_number(item.get("volume_participation", 0.0)) for item in observations if _safe_trade_number(item.get("volume_participation", 0.0)) > 0]
    notionals = [_safe_trade_number(item.get("notional", 0.0)) for item in observations if _safe_trade_number(item.get("notional", 0.0)) > 0]
    impacts = [_safe_trade_number(item.get("impact_bps", 0.0)) for item in observations]
    max_adv = max(adv_participation) if adv_participation else 0.0
    p95_adv = _quantile(adv_participation, 0.95)
    return {
        "executed_orders": len(observations),
        "avg_adv_participation": _metric_float(_mean(adv_participation)),
        "p50_adv_participation": _metric_float(_quantile(adv_participation, 0.50)),
        "p95_adv_participation": _metric_float(p95_adv),
        "max_adv_participation": _metric_float(max_adv),
        "avg_volume_participation": _metric_float(_mean(volume_participation)),
        "p95_volume_participation": _metric_float(_quantile(volume_participation, 0.95)),
        "max_volume_participation": _metric_float(max(volume_participation) if volume_participation else 0.0),
        "p50_trade_notional": _metric_float(_quantile(notionals, 0.50)),
        "p95_trade_notional": _metric_float(_quantile(notionals, 0.95)),
        "max_trade_notional": _metric_float(max(notionals) if notionals else 0.0),
        "max_impact_bps": _metric_float(max(impacts) if impacts else 0.0),
        "estimated_capacity_at_1pct_adv_max": _metric_float(float(initial_cash) * 0.01 / max_adv if max_adv > 0 else 0.0),
        "estimated_capacity_at_1pct_adv_p95": _metric_float(float(initial_cash) * 0.01 / p95_adv if p95_adv > 0 else 0.0),
    }


def _enrich_execution_observations_with_adv20(observations, start, end, symbols=None):
    if len(observations or []) < 100:
        return observations
    universe = [str(symbol) for symbol in symbols or []]
    if len(universe) < 1000 or not all(symbol.isdigit() and len(symbol) == 6 for symbol in universe[:100]):
        return observations
    try:
        import duckdb
        import pandas as pd
        from quant.infrastructure.data.storage_duckdb import _DEFAULT_DB

        rows = []
        for item in observations:
            symbol = str(item.get("symbol") or "")
            date_text = str(item.get("date") or "")[:10]
            if symbol and date_text:
                rows.append({"symbol": symbol, "trade_date": date_text})
        if not rows:
            return observations
        obs_frame = pd.DataFrame(rows).drop_duplicates()
        lookback_start = (pd.Timestamp(start) - pd.Timedelta(days=90)).date()
        end_day = pd.Timestamp(end).date()
        db_path = str(_DEFAULT_DB).replace("'", "''")
        conn = duckdb.connect(database=":memory:")
        try:
            conn.execute(f"ATTACH '{db_path}' AS market (READ_ONLY)")
            conn.register("obs", obs_frame)
            adv_frame = conn.execute(
                """
                WITH bars AS (
                    SELECT
                        symbol,
                        CAST(timestamp AS DATE) AS trade_date,
                        CASE
                            WHEN turnover IS NULL OR turnover <= 0 THEN close * volume
                            WHEN close * volume / NULLIF(turnover, 0) BETWEEN 5.0 AND 20.0 THEN turnover * 1000.0
                            WHEN close * volume / NULLIF(turnover, 0) BETWEEN 500.0 AND 2000.0 THEN turnover * 1000.0
                            ELSE turnover
                        END AS traded_value
                    FROM market.daily_cn_ochl
                    WHERE symbol IN (SELECT DISTINCT symbol FROM obs)
                      AND CAST(timestamp AS DATE) BETWEEN ? AND ?
                ),
                features AS (
                    SELECT
                        symbol,
                        trade_date,
                        AVG(traded_value) OVER (
                            PARTITION BY symbol
                            ORDER BY trade_date
                            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                        ) AS adv20_value
                    FROM bars
                )
                SELECT
                    o.symbol,
                    CAST(o.trade_date AS DATE) AS trade_date,
                    f.adv20_value
                FROM obs o
                LEFT JOIN features f
                  ON o.symbol = f.symbol
                 AND CAST(o.trade_date AS DATE) = f.trade_date
                """,
                [lookback_start, end_day],
            ).fetchdf()
        finally:
            conn.close()
        adv_map = {
            (str(row.symbol), str(pd.Timestamp(row.trade_date).date())): _safe_trade_number(row.adv20_value)
            for row in adv_frame.itertuples(index=False)
        }
        enriched = []
        for item in observations:
            copied = dict(item)
            key = (str(copied.get("symbol") or ""), str(copied.get("date") or "")[:10])
            adv_value = adv_map.get(key, 0.0)
            if adv_value > 0:
                copied["adv_value"] = adv_value
                notional = _safe_trade_number(copied.get("notional", 0.0))
                copied["adv_participation"] = notional / adv_value if adv_value > 0 else 0.0
                copied["adv_value_source"] = "adv20_value_postrun"
            enriched.append(copied)
        return enriched
    except Exception:
        return observations


def _trade_distribution(trades):
    sell_trades = [trade for trade in trades or [] if str(getattr(trade, "side", "") or "").upper() == "SELL"]
    pnls = [_safe_trade_number(getattr(trade, "pnl", 0.0)) for trade in sell_trades]
    returns = [_safe_trade_number(getattr(trade, "return_pct", 0.0)) / 100.0 for trade in sell_trades]
    durations = [_safe_trade_number(getattr(trade, "duration_days", 0.0)) for trade in sell_trades]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    return {
        "sell_trades": len(sell_trades),
        "avg_pnl": _metric_float(_mean(pnls)),
        "median_pnl": _metric_float(_quantile(pnls, 0.50)),
        "p05_pnl": _metric_float(_quantile(pnls, 0.05)),
        "p95_pnl": _metric_float(_quantile(pnls, 0.95)),
        "max_win": _metric_float(max(wins) if wins else 0.0),
        "max_loss": _metric_float(min(losses) if losses else 0.0),
        "avg_win": _metric_float(_mean(wins)),
        "avg_loss": _metric_float(_mean(losses)),
        "avg_return": _metric_float(_mean(returns)),
        "median_return": _metric_float(_quantile(returns, 0.50)),
        "avg_duration_days": _metric_float(_mean(durations)),
        "median_duration_days": _metric_float(_quantile(durations, 0.50)),
        "p95_duration_days": _metric_float(_quantile(durations, 0.95)),
    }


def _drawdown_episodes(equity_curve, limit=5):
    if equity_curve is None or not hasattr(equity_curve, "empty") or equity_curve.empty:
        return []
    try:
        equity = equity_curve.dropna().sort_index()
        running_max = equity.cummax()
        drawdown = equity / running_max - 1.0
        episodes = []
        in_drawdown = False
        start_date = None
        trough_date = None
        trough_value = 0.0
        for idx, value in drawdown.items():
            if value < 0 and not in_drawdown:
                in_drawdown = True
                start_date = idx
                trough_date = idx
                trough_value = float(value)
            elif value < 0 and in_drawdown and float(value) < trough_value:
                trough_date = idx
                trough_value = float(value)
            elif value >= 0 and in_drawdown:
                episodes.append(_drawdown_episode_dict(start_date, trough_date, idx, trough_value))
                in_drawdown = False
        if in_drawdown:
            episodes.append(_drawdown_episode_dict(start_date, trough_date, None, trough_value))
        return sorted(episodes, key=lambda item: item.get("drawdown_pct", 0.0))[:limit]
    except Exception:
        return []


def _drawdown_episode_dict(start_date, trough_date, recovery_date, drawdown_pct):
    start_ts = pd_timestamp(start_date)
    trough_ts = pd_timestamp(trough_date)
    recovery_ts = pd_timestamp(recovery_date) if recovery_date is not None else None
    end_ts = recovery_ts or trough_ts
    return {
        "start": start_ts.date().isoformat() if start_ts is not None else "",
        "trough": trough_ts.date().isoformat() if trough_ts is not None else "",
        "recovery": recovery_ts.date().isoformat() if recovery_ts is not None else "",
        "duration_days": int((end_ts - start_ts).days) if start_ts is not None and end_ts is not None else 0,
        "drawdown_pct": _metric_float(drawdown_pct),
    }


def _rolling_stability(equity_curve, benchmark_equity_curve):
    if equity_curve is None or not hasattr(equity_curve, "empty") or equity_curve.empty:
        return {}
    try:
        import pandas as pd
        import numpy as np

        equity = equity_curve.dropna().sort_index()
        returns = equity.pct_change(fill_method=None).dropna()
        result = {
            "rolling_1y_sharpe": _rolling_stat_summary(_rolling_sharpe_series(returns, 252)),
            "rolling_3y_sharpe": _rolling_stat_summary(_rolling_sharpe_series(returns, 756)),
        }
        if benchmark_equity_curve is not None and hasattr(benchmark_equity_curve, "empty") and not benchmark_equity_curve.empty:
            bench_returns = benchmark_equity_curve.dropna().sort_index().pct_change(fill_method=None).dropna()
            aligned = pd.concat([returns.rename("strategy"), bench_returns.rename("benchmark")], axis=1).dropna()
            if not aligned.empty:
                excess = aligned["strategy"] - aligned["benchmark"]
                result["rolling_1y_information_ratio"] = _rolling_stat_summary(_rolling_sharpe_series(excess, 252))
                cov = aligned["strategy"].rolling(252).cov(aligned["benchmark"])
                var = aligned["benchmark"].rolling(252).var()
                beta = (cov / var.replace(0, np.nan)).dropna()
                result["rolling_1y_beta"] = _rolling_stat_summary(beta)
        return result
    except Exception:
        return {}


def _regime_breakdown(equity_curve, benchmark_equity_curve, initial_cash):
    strategy_years = _yearly_returns_from_equity(equity_curve, initial_cash)
    benchmark_years = _yearly_returns_from_equity(benchmark_equity_curve, initial_cash)
    if not strategy_years:
        return {}
    common_years = sorted(set(strategy_years) & set(benchmark_years))
    up_years = [year for year in common_years if _safe_trade_number(benchmark_years.get(year)) > 0]
    down_years = [year for year in common_years if _safe_trade_number(benchmark_years.get(year)) <= 0]
    best_year = max(strategy_years, key=lambda year: _safe_trade_number(strategy_years.get(year)))
    worst_year = min(strategy_years, key=lambda year: _safe_trade_number(strategy_years.get(year)))
    return {
        "positive_years": sum(1 for value in strategy_years.values() if _safe_trade_number(value) > 0),
        "total_years": len(strategy_years),
        "outperform_years": sum(1 for year in common_years if _safe_trade_number(strategy_years.get(year)) > _safe_trade_number(benchmark_years.get(year))),
        "benchmark_up_years": len(up_years),
        "benchmark_down_years": len(down_years),
        "avg_return_when_benchmark_up": _metric_float(_mean([_safe_trade_number(strategy_years.get(year)) for year in up_years])),
        "avg_excess_when_benchmark_up": _metric_float(_mean([_safe_trade_number(strategy_years.get(year)) - _safe_trade_number(benchmark_years.get(year)) for year in up_years])),
        "avg_return_when_benchmark_down": _metric_float(_mean([_safe_trade_number(strategy_years.get(year)) for year in down_years])),
        "avg_excess_when_benchmark_down": _metric_float(_mean([_safe_trade_number(strategy_years.get(year)) - _safe_trade_number(benchmark_years.get(year)) for year in down_years])),
        "best_year": {"year": best_year, "return": _metric_float(strategy_years.get(best_year))},
        "worst_year": {"year": worst_year, "return": _metric_float(strategy_years.get(worst_year))},
    }


def _cost_decomposition(bt_result, initial_cash, diagnostics_dict):
    final_nav = _safe_trade_number(getattr(bt_result, "final_nav", 0.0))
    commission = _safe_trade_number(diagnostics_dict.get("total_commission", 0.0))
    gross_pnl = _safe_trade_number(diagnostics_dict.get("total_gross_pnl", 0.0))
    net_pnl = final_nav - float(initial_cash or 0.0)
    return {
        "gross_pnl_before_explicit_cost": _metric_float(gross_pnl),
        "net_pnl_after_cost": _metric_float(net_pnl),
        "explicit_commission_tax": _metric_float(commission),
        "explicit_cost_pct_initial_cash": _metric_float(commission / float(initial_cash) if initial_cash else 0.0),
        "explicit_cost_pct_gross_pnl": _metric_float(commission / abs(gross_pnl) if abs(gross_pnl) > 1e-10 else 0.0),
        "slippage_impact_note": "滑点/冲击体现在成交价中；total_commission 只包含显式佣金税费。",
    }


def _strategy_guard_diagnostics(strategy):
    if strategy is None:
        return {}
    getter = getattr(strategy, "get_guard_diagnostics", None)
    if callable(getter):
        try:
            value = getter()
            return dict(value or {}) if isinstance(value, dict) else {}
        except Exception:
            return {}
    value = getattr(strategy, "guard_diagnostics", None)
    return dict(value or {}) if isinstance(value, dict) else {}


def _rolling_sharpe_series(returns, window):
    import numpy as np

    mean = returns.rolling(window).mean()
    std = returns.rolling(window).std()
    return (mean / std.replace(0, np.nan) * (252 ** 0.5)).dropna()


def _rolling_stat_summary(series):
    if series is None or len(series) == 0:
        return {}
    return {
        "latest": _metric_float(series.iloc[-1]),
        "median": _metric_float(series.median()),
        "min": _metric_float(series.min()),
        "max": _metric_float(series.max()),
        "observations": int(len(series)),
    }


def _mean(values):
    clean = [float(value) for value in values or [] if value == value]
    return sum(clean) / len(clean) if clean else 0.0


def _quantile(values, q):
    clean = sorted(float(value) for value in values or [] if value == value)
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * float(q)
    lower = int(pos)
    upper = min(lower + 1, len(clean) - 1)
    weight = pos - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def _series_mean(series):
    if series is None or not hasattr(series, "empty") or series.empty:
        return 0.0
    try:
        return float(series.dropna().mean())
    except Exception:
        return 0.0


def _safe_trade_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return number


def _trade_date_text(trade):
    fill_date = getattr(trade, "fill_date", None) or getattr(trade, "exit_time", None)
    if hasattr(fill_date, "date"):
        return fill_date.date().isoformat()
    return str(fill_date)[:10]


def _duration_days(value):
    if value is None:
        return 0.0
    if hasattr(value, "total_seconds"):
        return _metric_float(value.total_seconds() / 86400.0)
    return _metric_float(value)


def pd_timestamp(value):
    if value is None:
        return None
    try:
        import pandas as pd

        return pd.Timestamp(value)
    except Exception:
        return None


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
        matched = False
        for row in store.list_hypotheses():
            if row.get("strategy_id") != sid:
                continue
            matched = True
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
        if matched or not hasattr(store, "upsert_hypothesis"):
            return
        candidate = store.get_candidate(sid) if hasattr(store, "get_candidate") else None
        meta = dict((candidate or {}).get("research_meta") or {})
        spec = dict(meta.get("strategy_spec") or {})
        metrics = {
            "strict_backtest": strict_report,
            "backtest_sharpe": strict_report.get("metrics", {}).get("sharpe", 0.0),
            "backtest_sortino": strict_report.get("metrics", {}).get("sortino", 0.0),
            "backtest_cagr": strict_report.get("metrics", {}).get("cagr", 0.0),
            "backtest_max_drawdown_pct": strict_report.get("metrics", {}).get("max_drawdown_pct", 0.0),
            "backtest_calmar_ratio": strict_report.get("metrics", {}).get("calmar_ratio", 0.0),
            "benchmark_symbol": strict_report.get("benchmark", {}).get("symbol", ""),
        }
        store.upsert_hypothesis({
            "hypothesis_id": f"strict_backtest_{sid}",
            "strategy_id": str(sid),
            "title": str((candidate or {}).get("name") or sid),
            "source": str(meta.get("source") or "archived_rejected_strategy"),
            "source_url": str(meta.get("source_url") or ""),
            "thesis": str((candidate or {}).get("description") or f"Standalone strict backtest for {sid}."),
            "status": status,
            "stage": stage,
            "decision_reason": reason,
            "metrics": metrics,
            "evidence": {"strategy_spec": spec},
        })
    except Exception:
        return


def _diagnostics_dict(diagnostics):
    if diagnostics is None:
        return {}
    fields = (
        "fill_count",
        "total_commission",
        "total_gross_pnl",
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
        returns = equity.pct_change(fill_method=None).dropna()
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

    data_cache = {}
    cache_lock = threading.RLock()
    thread_data = threading.local()

    def _empty_walkforward_response():
        return {"metrics": {"sharpe": 0.0, "max_dd": 0.0, "cagr": 0.0, "win_rate": 0.0}, "returns": pd.Series(dtype=float)}

    def _cache_key(symbols, start, end):
        return (tuple(symbols), start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    def _thread_db_provider():
        db_provider = getattr(thread_data, "db_provider", None)
        if db_provider is not None:
            if not hasattr(db_provider, "is_connected") or db_provider.is_connected():
                return db_provider
        db_provider = DuckDBProvider()
        db_provider.connect()
        thread_data.db_provider = db_provider
        return db_provider

    def _load_uncached_data_bundle(symbols, fetch_start, fetch_end):
        db_provider = _thread_db_provider()
        data_df = db_provider.get_bars_for_symbols(symbols, fetch_start, fetch_end, "1d")
        if not data_df.empty and "timestamp" in data_df.columns and not pd.api.types.is_datetime64_any_dtype(data_df["timestamp"]):
            data_df = data_df.copy()
            data_df["timestamp"] = pd.to_datetime(data_df["timestamp"])
        if not data_df.empty and "timestamp" in data_df.columns:
            data_df = data_df.sort_values(["timestamp", "symbol"]).set_index("timestamp", drop=False)
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        return {"data": data_df, "lot_sizes": lot_sizes}

    def _load_data_bundle(symbols, fetch_start, fetch_end, cache_enabled=False):
        if not cache_enabled:
            return _load_uncached_data_bundle(symbols, fetch_start, fetch_end)

        key = _cache_key(symbols, fetch_start, fetch_end)
        with cache_lock:
            cached = data_cache.get(key)
            if cached is not None:
                return cached
            bundle = _load_uncached_data_bundle(symbols, fetch_start, fetch_end)
            if len(data_cache) >= 2:
                data_cache.clear()
            data_cache[key] = bundle
            return bundle

    def _slice_data_frame(data_df, start, end):
        if data_df.empty or "timestamp" not in data_df.columns:
            return data_df
        if isinstance(data_df.index, pd.DatetimeIndex) and data_df.index.is_monotonic_increasing:
            return data_df.loc[pd.Timestamp(start):pd.Timestamp(end)].copy()
        timestamps = data_df["timestamp"]
        if not pd.api.types.is_datetime64_any_dtype(timestamps):
            timestamps = pd.to_datetime(timestamps)
        mask = (timestamps >= pd.Timestamp(start)) & (timestamps <= pd.Timestamp(end))
        return data_df.loc[mask].copy()

    def _run_walkforward_backtest(sid, request):
        registry = StrategyRegistry()
        strategy_class = _walkforward_strategy_class(sid, registry, request.get("strategy_archive_dir"))
        if strategy_class is None:
            return _empty_walkforward_response()

        symbols = request.get("symbols") or []
        start = datetime.strptime(str(request["start"]), "%Y-%m-%d")
        end = datetime.strptime(str(request["end"]), "%Y-%m-%d")
        prefetch_enabled = bool(request.get("walkforward_prefetch_data", False))
        fetch_start_value = request.get("walkforward_start_date") if prefetch_enabled else request["start"]
        fetch_end_value = request.get("walkforward_end_date") if prefetch_enabled else request["end"]
        fetch_start = datetime.strptime(str(fetch_start_value), "%Y-%m-%d")
        fetch_end = datetime.strptime(str(fetch_end_value), "%Y-%m-%d")
        initial_cash = float(request.get("initial_cash", 100000))

        bundle = _load_data_bundle(symbols, fetch_start, fetch_end, cache_enabled=prefetch_enabled)
        data_df = _slice_data_frame(bundle["data"], start, end)
        lot_sizes = bundle["lot_sizes"]

        if data_df.empty:
            return _empty_walkforward_response()

        data_provider = DataFrameProvider(data_df)
        strategy = strategy_class(symbols=symbols)
        bt_config = {
            "backtest": {"slippage_bps": 5},
            "execution": {"commission": {
                "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                "HK": {"type": "hk_realistic"},
                "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
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
        returns = bt_result.equity_curve.pct_change(fill_method=None).dropna() if hasattr(bt_result, "equity_curve") else pd.Series(dtype=float)
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


def _walkforward_strategy_class(sid, registry, strategy_archive_dir=None):
    strategy_class = registry.get(sid)
    if strategy_class is not None:
        return strategy_class
    return _load_archived_strategy_class(sid, strategy_archive_dir)


def _load_archived_strategy_class(sid, strategy_archive_dir=None):
    archive_dir = Path(strategy_archive_dir) if strategy_archive_dir else Path(__file__).resolve().parent.parent / "features" / "rejected_strategy" / str(sid)
    strategy_file = archive_dir / "strategy.py"
    if not strategy_file.exists():
        return None
    try:
        module_name = f"quant.rejected_strategies.{str(sid).lower()}.strategy"
        spec = importlib.util.spec_from_file_location(module_name, strategy_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        target = str(sid).lower()
        for attr_name in dir(module):
            cls = getattr(module, attr_name)
            registry_name = str(getattr(cls, "_registry_name", "")).lower()
            if isinstance(cls, type) and registry_name == target:
                return cls
    except Exception:
        return None
    return None


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


def _latest_report_payload(cfg: ResearchConfig) -> dict:
    root = _research_artifact_root(cfg)
    stage_reports = {}
    latest_mtime = None
    for stage_key, filename in STAGE_REPORT_HTML.items():
        stage_path = root / latest_stage_report_html_path(stage_key)
        if stage_path.exists():
            mtime = stage_path.stat().st_mtime
            latest_mtime = mtime if latest_mtime is None else max(latest_mtime, mtime)
        stage_reports[stage_key] = {
            "available": stage_path.exists(),
            "url": f"/api/research/report/stage/{stage_key}",
            "path": str(stage_path),
            "filename": filename.as_posix(),
        }
    payload = {
        "available": any(item["available"] for item in stage_reports.values()),
        "reports_root": str(root / "reports"),
        "stage_reports": stage_reports,
    }
    metadata_path = root / LATEST_REPORT_METADATA
    if metadata_path.exists():
        try:
            with metadata_path.open("r", encoding="utf-8-sig") as f:
                payload["metadata"] = json.load(f)
        except Exception:
            payload["metadata"] = {}
    if latest_mtime is not None:
        payload["updated_at"] = datetime.fromtimestamp(latest_mtime).isoformat()
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
    except FileNotFoundError:
        return ResearchConfig()
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
    strategy_ids = _parse_idea_ids(data.get("strategy_ids") or data.get("strategy_id"))
    max_ideas = data.get("max_ideas")
    max_strategies = data.get("max_strategies") or max_ideas
    job_id = str(uuid.uuid4())[:8]

    cfg = _load_research_config()
    if sources:
        cfg.sources = sources
    if max_results is not None:
        cfg.max_results_per_source = int(max_results)
    if mode in {"fast", "fast_research", "quick", "quick_research"}:
        cfg.auto_backtest = False
        cfg.rigor_enabled = False
    elif mode in {"strict", "strict_backtest"}:
        cfg.auto_backtest = True
        cfg.rigor_enabled = False
    elif mode in {"walkforward", "walkforward_audit", "walkforward_strict_audit"}:
        cfg.auto_backtest = False
        cfg.rigor_enabled = True

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
            elif mode in {"fast", "fast_research", "quick", "quick_research"}:
                engine.run_fast_research_from_idea_bank(
                    statuses=idea_statuses,
                    idea_ids=idea_ids,
                    max_ideas=int(max_ideas) if max_ideas is not None else None,
                    result=result_obj,
                )
            elif mode in {"strict", "strict_backtest"}:
                engine.run_strict_backtest_stage(
                    strategy_ids=strategy_ids,
                    statuses=idea_statuses,
                    max_strategies=int(max_strategies) if max_strategies is not None else None,
                    result=result_obj,
                )
            elif mode in {"walkforward", "walkforward_audit", "walkforward_strict_audit"}:
                engine.run_walkforward_audit_stage(
                    strategy_ids=strategy_ids,
                    statuses=idea_statuses,
                    max_strategies=int(max_strategies) if max_strategies is not None else None,
                    result=result_obj,
                )
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
    return jsonify({
        "error": "Full research report has been removed; use /api/research/report/stage/<stage_key>.",
        "stage_reports": _latest_report_payload(_load_research_config()).get("stage_reports", {}),
    }), 410


@research_bp.route("/api/research/report/stage/<stage_key>")
def get_latest_stage_report(stage_key):
    if stage_key not in STAGE_REPORT_HTML:
        return jsonify({"error": "Unknown research report stage"}), 404
    cfg = _load_research_config()
    path = _research_artifact_root(cfg) / latest_stage_report_html_path(stage_key)
    if not path.exists():
        return jsonify({"error": "Stage research report not found"}), 404
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
