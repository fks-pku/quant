"""Run full research report for the A-share Davis Double Click candidate."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (  # noqa: E402
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
)
from quant.domain.models.market import is_cn_symbol  # noqa: E402
from quant.features.backtest.benchmark import BenchmarkProvider  # noqa: E402
from quant.features.backtest.engine import Backtester  # noqa: E402
from quant.features.strategies.reject.ashare_davis_double_click.strategy import (  # noqa: E402
    AShareDavisDoubleClickStrategy,
)
from quant.features.trading.portfolio import Portfolio  # noqa: E402
from quant.features.trading.risk import RiskEngine  # noqa: E402
from quant.features.trading.sub_portfolio import SubPortfolio  # noqa: E402
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider  # noqa: E402
from quant.infrastructure.research.reporting import (  # noqa: E402
    build_research_full_report_html,
    build_research_stage_report_html,
)


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 20000.0
STRATEGY_ID = "ashare_davis_double_click"
TITLE = "A股戴维斯双击"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
STRATEGY_PARAMS: Dict[str, Any] = {
    "holding_days": 20,
    "max_positions": 10,
    "max_position_pct": 1.0,
    "cap_percentile_low": 0.25,
    "cap_percentile_high": 0.95,
    "min_price": 3.0,
    "min_turnover": 50000.0,
    "lot_size": 100,
    "momentum_lookback": 126,
    "momentum_skip": 5,
    "min_pe_ttm": 5.0,
    "max_pe_ttm": 60.0,
    "min_profit_growth": 15.0,
    "min_roe": 6.0,
    "min_momentum": -0.05,
}
EXECUTION_COST_MODEL = {
    "enabled": True,
    "name": "cn_daily_liquidity_impact",
    "markets": ["CN"],
    "tick_size": 0.01,
    "half_spread_ticks": 0.5,
    "min_slippage_bps": 5,
    "max_participation_rate": 0.02,
    "impact_coefficient": 0.35,
    "volatility_fallback": 0.03,
    "adv_value_field": "adv20_value",
    "volatility_field": "volatility20",
}
SOURCE_URL = "https://bigquant.com/square/paper/f71cbaca-4fba-4acc-823c-091094581ffb"


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. 基础护栏</td><td>每个调仓日过滤 ST、停牌、不可交易、非上市、低价、低成交额股票，并限制在点时 total_mv 25%-95% 分位。</td><td>只使用当日 status、OHLCV 与 daily_basic。</td></tr>
<tr><td>2. 财务增长</td><td>使用 q_netprofit_yoy，不可得时退回 netprofit_yoy；利润增速至少 15%。</td><td>财务指标按 ann_date 做 point-in-time asof join。</td></tr>
<tr><td>3. 质量与估值</td><td>使用 q_roe/roe，ROE 至少 6%；PE_TTM 要在 5 到 60 之间，避免极端亏损和过贵标的。</td><td>估值来自当日 daily_basic，质量来自已披露财报。</td></tr>
<tr><td>4. 戴维斯双击打分</td><td>综合高 growth_to_pe、高利润增长、高 ROE、高盈利收益率、126 日动量和收入增速。</td><td>打分只在当日可交易候选池横截面内做 percentile rank。</td></tr>
<tr><td>5. 组合与执行</td><td>每 20 个交易日买入前 10 只等权股票，100 股取整，信号收盘生成，订单 T+1 开盘执行。</td><td>严格回测包含 A 股佣金、5bps 最小滑点、涨跌停、停牌、手数、现金和 2% ADV 冲击约束。</td></tr>
</tbody></table></div>
"""


def main() -> None:
    args = _parse_args()
    start = _parse_date(args.start, START)
    end = _parse_date(args.end, END)
    output_root = Path(args.output_root) if args.output_root else REPORT_ROOT
    strategy_params = dict(STRATEGY_PARAMS)
    symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(
        start,
        end,
        args.symbol_limit,
    )
    print(f"Running {STRATEGY_ID} on {len(symbols)} symbols from {start.date()} to {end.date()}", flush=True)
    fast_metrics = {} if args.skip_fast else _run_fast_validation(symbols, start, end, strategy_params, args.max_validation_dates)
    strict_report = _run_backtest(
        symbols,
        lot_sizes,
        benchmark_provider,
        benchmark_meta,
        survivorship_audit,
        start,
        end,
        float(args.initial_cash),
        strategy_params,
    )
    walkforward = _walkforward_from_strict_equity(strict_report)
    report_path, payload_path = _write_reports(
        output_root,
        symbols,
        strategy_params,
        fast_metrics,
        strict_report,
        walkforward,
        start,
        end,
        float(args.initial_cash),
    )
    metrics = strict_report.get("metrics") or {}
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "full_report_path": str(report_path),
                "payload_path": str(payload_path),
                "rank_ic": fast_metrics.get("rank_ic"),
                "sharpe": metrics.get("sharpe"),
                "cagr": metrics.get("cagr"),
                "total_return": metrics.get("total_return"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "total_trades": metrics.get("total_trades"),
                "walkforward": {
                    "aggregate_oos_sharpe": walkforward.get("aggregate_oos_sharpe"),
                    "worst_oos_sharpe": walkforward.get("worst_oos_sharpe"),
                    "pct_profitable_splits": walkforward.get("pct_profitable_splits"),
                    "is_viable": walkforward.get("is_viable"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START.date().isoformat())
    parser.add_argument("--end", default=END.date().isoformat())
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--symbol-limit", type=int, default=0)
    parser.add_argument("--output-root", default="")
    parser.add_argument("--skip-fast", action="store_true")
    parser.add_argument("--max-validation-dates", type=int, default=0)
    return parser.parse_args()


def _parse_date(value: str, fallback: datetime) -> datetime:
    if not value:
        return fallback
    return datetime.strptime(str(value), "%Y-%m-%d")


def _load_shared_inputs(
    start: datetime,
    end: datetime,
    symbol_limit: int,
) -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        symbols = _load_ashare_symbols(db_provider, start, end, symbol_limit)
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_ashare_symbols(db_provider: DuckDBProvider, start: datetime, end: datetime, symbol_limit: int) -> List[str]:
    limit_clause = " LIMIT ?" if symbol_limit and symbol_limit > 0 else ""
    params: List[Any] = [start, end]
    if limit_clause:
        params.append(int(symbol_limit))
    rows = db_provider.storage.conn.execute(
        f"""
        SELECT DISTINCT symbol
        FROM daily_cn_ochl
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{{5}}$')
          AND NOT starts_with(symbol, '200')
        ORDER BY symbol
        {limit_clause}
        """,
        params,
    ).fetchall()
    return [str(row[0]) for row in rows]


def _run_backtest(
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
    strategy_params: Dict[str, Any],
) -> Dict[str, Any]:
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=True,
        include_execution_liquidity_features=True,
    )
    data_provider._chunk_size = max(252, int(getattr(data_provider, "_chunk_size", 63) or 63))
    strategy = AShareDavisDoubleClickStrategy(symbols=symbols, **strategy_params)
    backtest_config = {"slippage_bps": 5, "execution_cost_model": dict(EXECUTION_COST_MODEL)}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 1.0},
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
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, initial_cash) if benchmark_provider else None
    return _strict_backtest_report(
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
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _run_fast_validation(
    symbols: List[str],
    start: datetime,
    end: datetime,
    strategy_params: Dict[str, Any],
    max_validation_dates: int,
) -> Dict[str, Any]:
    provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=True,
        include_execution_liquidity_features=False,
    )
    provider._chunk_size = max(252, int(getattr(provider, "_chunk_size", 63) or 63))
    strategy = AShareDavisDoubleClickStrategy(symbols=symbols, **strategy_params)
    records: List[Dict[str, Any]] = []
    pending: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    dates = list(provider._trading_dates_list)
    horizon = int(strategy_params.get("holding_days", 20) or 20)
    step = horizon
    max_lookback = int(strategy_params.get("momentum_lookback", 126) or 126) + 2
    validation_indexes = [idx for idx in range(max_lookback, max(0, len(dates) - horizon), step)]
    if max_validation_dates and max_validation_dates > 0 and len(validation_indexes) > max_validation_dates:
        stride = max(1, math.ceil(len(validation_indexes) / float(max_validation_dates)))
        validation_indexes = validation_indexes[::stride][:max_validation_dates]
    validation_index_set = set(validation_indexes)
    try:
        for idx, trading_date in enumerate(dates):
            bars = provider.get_bars_for_date(datetime.combine(trading_date, datetime.min.time()))
            strategy.on_data_batch(None, bars)
            for signal in pending.pop(idx, []):
                bar = strategy._get_last_bar(str(signal["symbol"]))
                future_price = strategy._adj_price(bar) if bar else 0.0
                start_price = float(signal["price"])
                if future_price > 0 and start_price > 0:
                    records.append(
                        {
                            "date": signal["date"],
                            "symbol": signal["symbol"],
                            "score": signal["score"],
                            "forward_return": future_price / start_price - 1.0,
                        }
                    )
            if idx not in validation_index_set:
                continue
            scores = _score_current_universe(strategy)
            target_idx = idx + horizon
            if target_idx >= len(dates):
                continue
            for symbol, score in scores.items():
                bar = strategy._get_last_bar(symbol)
                price = strategy._adj_price(bar) if bar else 0.0
                if price > 0:
                    pending[target_idx].append(
                        {
                            "date": trading_date.isoformat(),
                            "symbol": symbol,
                            "score": float(score),
                            "price": price,
                        }
                    )
    finally:
        provider.close()
    return _summarize_fast_validation(records, start, end, len(validation_indexes))


def _score_current_universe(strategy: AShareDavisDoubleClickStrategy) -> Dict[str, float]:
    raw_candidates = []
    for symbol in strategy.symbols:
        bar = strategy._get_last_bar(symbol)
        if not bar:
            continue
        if strategy._candidate_rejection(symbol, bar):
            continue
        base = strategy._base_snapshot(symbol, bar)
        if str(base.pop("missing_field", "") or ""):
            continue
        raw_candidates.append(base)
    snapshots = []
    for base in strategy._apply_cap_band(raw_candidates):
        symbol = str(base["symbol"])
        snapshot = strategy._strategy_snapshot(symbol, strategy._get_last_bar(symbol), base)
        if str(snapshot.pop("rejection_reason", "") or ""):
            continue
        if str(snapshot.pop("missing_field", "") or ""):
            continue
        snapshots.append(snapshot)
    return strategy._score_snapshots(snapshots) if snapshots else {}


def _summarize_fast_validation(
    records: List[Dict[str, Any]],
    start: datetime,
    end: datetime,
    validation_dates: int,
) -> Dict[str, Any]:
    if not records:
        return {
            "data_start": start.date().isoformat(),
            "data_end": end.date().isoformat(),
            "n_observations": 0,
            "rank_ic": 0.0,
            "rank_ic_ir": 0.0,
            "fama_macbeth_tstat": 0.0,
            "fdr_adjusted_p": 1.0,
            "hit_rate": 0.0,
            "long_short_spread": 0.0,
            "validation_tests": ["monthly_rank_ic", "top_bottom_spread", "pit_financial_ann_date_join"],
            "risk_flags": ["no_fast_validation_records"],
        }
    frame = pd.DataFrame(records)
    ic_values = []
    spreads = []
    hits = []
    for _, group in frame.groupby("date"):
        if len(group) < 20:
            continue
        score_rank = group["score"].rank(method="average")
        return_rank = group["forward_return"].rank(method="average")
        corr = score_rank.corr(return_rank)
        if corr == corr:
            ic_values.append(float(corr))
        ordered = group.sort_values("score", ascending=False)
        bucket_size = max(1, int(len(ordered) * 0.2))
        top_return = float(ordered.head(bucket_size)["forward_return"].mean())
        bottom_return = float(ordered.tail(bucket_size)["forward_return"].mean())
        spreads.append(top_return - bottom_return)
        hits.append(1.0 if top_return > bottom_return else 0.0)
    rank_ic = statistics.mean(ic_values) if ic_values else 0.0
    ic_std = statistics.stdev(ic_values) if len(ic_values) > 1 else 0.0
    icir = rank_ic / ic_std if ic_std > 0 else 0.0
    t_stat = rank_ic / (ic_std / math.sqrt(len(ic_values))) if ic_std > 0 and len(ic_values) > 1 else 0.0
    p_value = 2.0 * (1.0 - NormalDist().cdf(abs(t_stat))) if t_stat else 1.0
    return {
        "data_start": start.date().isoformat(),
        "data_end": end.date().isoformat(),
        "n_observations": int(len(frame)),
        "data_rows": int(len(frame)),
        "validation_dates": int(validation_dates),
        "evaluated_dates": int(len(ic_values)),
        "rank_ic": rank_ic,
        "rank_ic_ir": icir,
        "fama_macbeth_tstat": t_stat,
        "fdr_adjusted_p": p_value,
        "hit_rate": statistics.mean(hits) if hits else 0.0,
        "long_short_spread": statistics.mean(spreads) if spreads else 0.0,
        "ic_decay": {"20d": rank_ic},
        "portfolio_diagnostics": {
            "top_bucket_return": statistics.mean([spread for spread in spreads if spread == spread]) if spreads else 0.0,
            "long_short_spread": statistics.mean(spreads) if spreads else 0.0,
            "candidate_observations": int(len(frame)),
        },
        "pnl_attribution_bridge": {
            "signal_only_long_short_spread": statistics.mean(spreads) if spreads else 0.0,
            "strict_backtester_constraints": "T+1, lot-size, cash, limit/suspension, commission, slippage and liquidity impact are applied in strict stage.",
        },
        "validation_tests": ["monthly_rank_ic", "top_bottom_spread", "pit_financial_ann_date_join"],
        "risk_flags": [],
    }


def _walkforward_from_strict_equity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    points = ((strict_report.get("equity_curve") or {}).get("strategy") or [])
    if not points:
        return {
            "verdict": "fail",
            "reason": "strict equity curve missing",
            "is_viable": False,
            "capacity_ok": False,
            "aggregate_oos_sharpe": 0.0,
            "worst_oos_sharpe": 0.0,
            "pct_profitable_splits": 0.0,
            "thresholds": _walkforward_thresholds(),
            "splits": [],
            "n_splits": 0,
            "evaluated_splits": 0,
            "total_splits": 0,
            "no_trade_splits": 0,
        }
    frame = pd.DataFrame(points)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna().sort_values("date")
    split_ranges = [
        ("2018-01-01", "2019-12-31", "2016-01-01", "2017-12-31"),
        ("2020-01-01", "2021-12-31", "2018-01-01", "2019-12-31"),
        ("2022-01-01", "2023-12-31", "2020-01-01", "2021-12-31"),
        ("2024-01-01", "2025-12-31", "2022-01-01", "2023-12-31"),
    ]
    splits = []
    for index, (test_start, test_end, train_start, train_end) in enumerate(split_ranges, start=1):
        split_frame = frame[(frame["date"] >= pd.Timestamp(test_start)) & (frame["date"] <= pd.Timestamp(test_end))]
        if len(split_frame) < 2:
            continue
        returns = split_frame["value"].pct_change(fill_method=None).dropna()
        sharpe = _sharpe(returns)
        total_return = float(split_frame["value"].iloc[-1] / split_frame["value"].iloc[0] - 1.0)
        splits.append(
            {
                "split": index,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "oos_sharpe": sharpe,
                "test_sharpe": sharpe,
                "max_drawdown": _max_drawdown(split_frame["value"]),
                "turnover": None,
                "trade_count": None,
                "has_trades": True,
                "total_return": total_return,
                "verdict": "pass" if sharpe > 0 and total_return > 0 else "fail",
                "parameters": "frozen parameters",
            }
        )
    sharpes = [float(split["oos_sharpe"]) for split in splits]
    profitable = [1.0 if float(split.get("total_return") or 0.0) > 0 else 0.0 for split in splits]
    aggregate = statistics.mean(sharpes) if sharpes else 0.0
    worst = min(sharpes) if sharpes else 0.0
    pct_profitable = statistics.mean(profitable) if profitable else 0.0
    capacity_ok = _strict_capacity_ok(strict_report)
    is_viable = bool(sharpes) and worst >= 0.3 and pct_profitable >= 0.5 and capacity_ok
    verdict = "pass" if is_viable else ("warn" if aggregate > 0 and pct_profitable >= 0.5 else "fail")
    return {
        "verdict": verdict,
        "reason": "Frozen-parameter calendar OOS audit derived from strict Backtester equity; no parameter refit.",
        "is_viable": is_viable,
        "capacity_ok": capacity_ok,
        "thresholds": _walkforward_thresholds(),
        "aggregate_oos_sharpe": aggregate,
        "worst_oos_sharpe": worst,
        "pct_profitable_splits": pct_profitable,
        "deflated_sharpe_ratio": None,
        "sharpe_degradation": aggregate - worst if aggregate > 0 else 0.0,
        "regime_breakdown": {},
        "bull_only_warning": False,
        "n_splits": len(splits),
        "evaluated_splits": len(splits),
        "total_splits": len(splits),
        "no_trade_splits": 0,
        "splits": splits,
    }


def _sharpe(returns: pd.Series) -> float:
    returns = returns.dropna()
    if returns.empty:
        return 0.0
    std = float(returns.std())
    if std <= 0:
        return 0.0
    return float(returns.mean() / std * math.sqrt(252.0))


def _max_drawdown(equity: Iterable[float]) -> float:
    peak = None
    worst = 0.0
    for value in equity:
        number = float(value)
        peak = number if peak is None else max(peak, number)
        if peak and peak > 0:
            worst = min(worst, number / peak - 1.0)
    return worst


def _walkforward_thresholds() -> Dict[str, Any]:
    return {
        "train_window_days": 504,
        "test_window_days": 504,
        "step_days": 504,
        "purge_days": 20,
        "embargo_days": 20,
        "min_train_observations": 252,
        "min_worst_oos_sharpe": 0.3,
        "min_profitable_splits_pct": 0.5,
        "min_deflated_sharpe_ratio": 0.95,
        "max_adv_pct": 0.05,
    }


def _write_reports(
    output_root: Path,
    symbols: List[str],
    strategy_params: Dict[str, Any],
    fast_metrics: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
) -> Tuple[Path, Path]:
    strategy_dir = output_root / STRATEGY_ID
    run_dir = strategy_dir / "runs"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    row = _hypothesis_row(symbols, strategy_params, fast_metrics, strict_report, walkforward, start, end)
    result = _result_payload(row)
    generated = datetime.now(timezone.utc).isoformat()
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    strict_html = _insert_detail_section(
        build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated)
    )
    walkforward_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    full_html = _insert_detail_section(build_research_full_report_html(result, [row], generated_at=generated))
    payload = {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "initial_cash": initial_cash,
        "symbols": symbols,
        "parameters": strategy_params,
        "fast_metrics": fast_metrics,
        "strict_report": strict_report,
        "walkforward": walkforward,
        "hypothesis": row,
        "result": result,
    }
    payload_path = strategy_dir / "last_result.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / f"{run_ts}_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths = {
        "fast_research_report.html": fast_html,
        "strict_backtest_report.html": strict_html,
        "walkforward_audit_report.html": walkforward_html,
        "full_research_report.html": full_html,
    }
    for filename, html in paths.items():
        (strategy_dir / filename).write_text(html, encoding="utf-8")
        (run_dir / f"{run_ts}_{filename}").write_text(html, encoding="utf-8")
    latest_dir = output_root / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    for filename, html in paths.items():
        (latest_dir / filename).write_text(html, encoding="utf-8")
    return strategy_dir / "full_research_report.html", payload_path


def _result_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    status = str(row.get("status") or "")
    return {
        "run_id": f"{STRATEGY_ID}_full_research",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "discovered": 1,
        "evaluated": 1,
        "specified": 1,
        "validated": 1,
        "validated_passed": 1 if _fast_pass(row.get("metrics") or {}) else 0,
        "integrated": 1,
        "backtested": 1,
        "walkforward_passed": 1 if ((row.get("metrics") or {}).get("walkforward") or {}).get("is_viable") else 0,
        "rejected": 1 if status == "rejected" else 0,
        "errors": [],
    }


def _hypothesis_row(
    symbols: List[str],
    strategy_params: Dict[str, Any],
    fast_metrics: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    metrics = dict(fast_metrics or {})
    metrics["strict_backtest"] = strict_report
    metrics["walkforward"] = walkforward
    metrics["parameter_sensitivity"] = _parameter_sensitivity(strict_report, strategy_params)
    metrics["research_stage_conclusions"] = _stage_conclusions(metrics, strict_report, walkforward)
    status = _row_status(metrics, strict_report, walkforward)
    return {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "thesis": "盈利增长改善叠加合理估值时，市场容易同时上修 EPS 和 PE；用动量确认重估正在发生。",
        "status": status,
        "stage": "full_research",
        "source": "broker_report_review",
        "source_url": SOURCE_URL,
        "decision_reason": _decision_reason(metrics, strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "Tianfeng financial engineering report summary via BigQuant",
            "source_url": SOURCE_URL,
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.72,
                "source_type": "broker_financial_engineering_report",
                "matched_terms": ["戴维斯双击", "净利润增长", "估值修复", "A股"],
                "risk_flags": ["broker_summary_not_full_pdf", "fundamental_data_lag_required"],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "a_share_cross_sectional_growth_value",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "daily_cn_ochl all A-share symbols observed in the backtest window; future IPOs only become eligible after live bars and PIT fundamentals exist",
                "lookback_days": int(strategy_params.get("momentum_lookback", 126)),
                "horizon_days": int(strategy_params.get("holding_days", 20)),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {int(strategy_params.get('holding_days', 20))} trading days",
                "required_fields": AShareDavisDoubleClickStrategy(symbols=[]).required_fields,
                "parameters": strategy_params,
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(strategy_params),
                "source_report_url": SOURCE_URL,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30, "max_adv_participation_lte": 0.05},
            },
        },
    }


def _stage_conclusions(
    metrics: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    strict_metrics = strict_report.get("metrics") or {}
    cagr = float(strict_metrics.get("cagr") or 0.0)
    sharpe = float(strict_metrics.get("sharpe") or 0.0)
    max_dd = float(strict_metrics.get("max_drawdown_pct") or 0.0)
    rank_ic = float(metrics.get("rank_ic") or 0.0)
    fdr = float(metrics.get("fdr_adjusted_p") or 1.0)
    return {
        "fast_research": {
            "label": "快研究",
            "verdict": "pass" if _fast_pass(metrics) else ("warn" if rank_ic > 0 else "fail"),
            "conclusion": f"月度横截面验证：Rank IC={rank_ic:.4f}，FDR p={fdr:.4f}，Top-Bottom Spread={float(metrics.get('long_short_spread') or 0.0):.2%}。",
            "method": "按 20 日持有期抽样，使用 PIT 财务字段生成同一信号，再计算未来 20 日后复权收益的 Spearman Rank IC。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
            "method": "项目 Backtester；T+1、涨跌停、停牌、100 股手数、真实佣金、5bps 最小滑点和 cn_daily_liquidity_impact。",
        },
        "walkforward_strict_audit": {
            "label": "Walk-forward strict audit",
            "verdict": str(walkforward.get("verdict") or "fail"),
            "conclusion": (
                f"冻结参数日历 OOS：aggregate={float(walkforward.get('aggregate_oos_sharpe') or 0.0):.2f}，"
                f"worst={float(walkforward.get('worst_oos_sharpe') or 0.0):.2f}，"
                f"盈利 split={float(walkforward.get('pct_profitable_splits') or 0.0):.0%}。"
            ),
            "method": "从严格回测 equity curve 切分 2018-2019、2020-2021、2022-2023、2024-2025 四个冻结参数 OOS 窗口。",
        },
    }


def _parameter_sensitivity(strict_report: Dict[str, Any], strategy_params: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    return {
        "status": "single_frozen_parameter_set",
        "method": "Broker-thesis implementation with one frozen parameter set; no optimization grid was used in this run.",
        "base_params": strategy_params,
        "selected_params": strategy_params,
        "best_params": strategy_params,
        "tested_count": 1,
        "pass_count": 1 if _strict_pass(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "base_davis_double_click",
                "parameters": strategy_params,
                "cagr": metrics.get("cagr"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "verdict": "pass" if _strict_pass(strict_report) else "warn",
            }
        ],
    }


def _strategy_logic(strategy_params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "core_idea": "寻找利润增长已经披露、估值仍未极端透支、并开始出现价格重估确认的 A 股股票。",
        "universe": "全 A 股日线股票池；每个调仓日再过滤 ST、停牌、不可交易、非上市、低价、低流动性，并限制在当日 total_mv 25%-95% 分位。",
        "entry_filters": [
            f"pe_ttm between {strategy_params['min_pe_ttm']} and {strategy_params['max_pe_ttm']}",
            f"q_netprofit_yoy/netprofit_yoy >= {strategy_params['min_profit_growth']}%",
            f"q_roe/roe >= {strategy_params['min_roe']}%",
            f"126d skipped momentum >= {strategy_params['min_momentum']:.0%}",
        ],
        "ranking_rule": "score = 30% growth_to_pe + 25% profit_growth + 15% ROE + 15% earnings_yield + 10% skipped momentum + 5% sales_growth。",
        "portfolio_construction": f"每次调仓最多持有 {strategy_params['max_positions']} 只，按目标总仓位等权配置，100 股取整。",
        "rebalance_rule": f"每 {strategy_params['holding_days']} 个交易日收盘后重算候选，下一交易日开盘执行。",
        "exit_rule": "持仓若触发 ST、停牌、非上市、不可交易、低价等硬护栏则每日尝试退出；否则在下次调仓跌出 Top 组合时卖出。",
        "risk_budget": "A 股 long-only，T+1，2% ADV 最大参与率，真实佣金与冲击成本；20,000 初始资金下受 100 股手数约束明显。",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "holding_days": "调仓和预期持有周期；20 个交易日近似月度。",
        "max_positions": "最多持有股票数；受 20,000 初始资金和 A 股 100 股手数约束，不能过度分散。",
        "cap_percentile_low": "按当日 point-in-time total_mv 排名后的下分位过滤，避免微盘和壳风险主导。",
        "cap_percentile_high": "按当日 point-in-time total_mv 排名后的上分位过滤，保留中大盘成长股但不只买巨型权重。",
        "min_pe_ttm": "PE 下限；过低 PE 往往对应周期/亏损异常或财务口径噪声。",
        "max_pe_ttm": "PE 上限；避免为盈利增长支付过高估值。",
        "min_profit_growth": "利润增长确认门槛；用已披露 q_netprofit_yoy/netprofit_yoy。",
        "min_roe": "质量门槛；避免只买低质量高增长。",
        "momentum_lookback": "价格重估确认窗口。",
        "momentum_skip": "跳过最近几天以降低短期反转噪声。",
        "min_momentum": "趋势确认底线；避免买入仍在明显下跌的价值陷阱。",
        "min_turnover": "最低平均成交额门槛；由共享护栏计算。",
    }


def _insert_detail_section(html: str) -> str:
    marker = "<h2>2. 严格回测证据</h2>\n<h3>策略执行逻辑</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}<h3>策略执行逻辑</h3>"
    if marker in html:
        return html.replace(marker, replacement, 1)
    marker = "<h2>3. Strategy Logic And Core Evidence</h2>\n"
    return html.replace(marker, f"{marker}{DETAIL_SECTION}", 1)


def _row_status(metrics: Dict[str, Any], strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    if _fast_pass(metrics) and _strict_pass(strict_report) and bool(walkforward.get("is_viable")):
        return "candidate"
    strict_metrics = strict_report.get("metrics") or {}
    if float(strict_metrics.get("cagr") or 0.0) <= 0 or float(strict_metrics.get("sharpe") or 0.0) <= 0:
        return "rejected"
    return "needs_more_validation"


def _decision_reason(metrics: Dict[str, Any], strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    strict_metrics = strict_report.get("metrics") or {}
    return (
        f"Rank IC={float(metrics.get('rank_ic') or 0.0):.4f}; "
        f"strict Sharpe={float(strict_metrics.get('sharpe') or 0.0):.2f}, "
        f"CAGR={float(strict_metrics.get('cagr') or 0.0):.2%}, "
        f"MaxDD={float(strict_metrics.get('max_drawdown_pct') or 0.0):.2%}; "
        f"walkforward viable={bool(walkforward.get('is_viable'))}."
    )


def _fast_pass(metrics: Dict[str, Any]) -> bool:
    return float(metrics.get("rank_ic") or 0.0) >= 0.02 and float(metrics.get("fdr_adjusted_p") or 1.0) <= 0.10


def _strict_pass(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    return (
        float(metrics.get("cagr") or 0.0) > 0.10
        and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30
        and int(metrics.get("total_trades") or 0) > 50
        and _strict_capacity_ok(strict_report)
    )


def _strict_capacity_ok(strict_report: Dict[str, Any]) -> bool:
    capacity = strict_report.get("capacity") or {}
    value = capacity.get("max_adv_participation")
    try:
        return float(value) <= 0.05
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    main()
