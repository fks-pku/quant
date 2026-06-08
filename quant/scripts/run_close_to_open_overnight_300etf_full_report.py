"""Run full research report for the close-to-open overnight anomaly on CSI300 ETF."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (  # noqa: E402
    _DuckDBDailyDateProvider,
    _load_lot_sizes,
    _strict_backtest_report,
)
from quant.domain.models.market import is_cn_symbol  # noqa: E402
from quant.features.backtest.benchmark import BenchmarkProvider  # noqa: E402
from quant.features.backtest.engine import Backtester  # noqa: E402
from quant.features.strategies.reject.close_to_open_overnight_anomaly.strategy import (  # noqa: E402
    CloseToOpenOvernightAnomalyStrategy,
)
from quant.features.trading.portfolio import Portfolio  # noqa: E402
from quant.features.trading.risk import RiskEngine  # noqa: E402
from quant.features.trading.sub_portfolio import SubPortfolio  # noqa: E402
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider  # noqa: E402
from quant.infrastructure.research.reporting import (  # noqa: E402
    build_research_full_report_html,
    build_research_stage_report_html,
)


START = datetime(2016, 1, 4)
END = datetime(2026, 6, 1)
INITIAL_CASH = 10_000.0
TRADE_SYMBOL = "510300"
INDEX_REFERENCE_SYMBOL = "000300"
STRATEGY_ID = "close_to_open_overnight_anomaly_300etf"
TITLE = "沪深300ETF隔夜收盘买开盘卖"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
SOURCE_URLS = ["https://arxiv.org/pdf/2201.00223"]
SLIPPAGE_BPS = 5.0
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
STRATEGY_PARAMS: Dict[str, Any] = {
    "symbols": [TRADE_SYMBOL],
    "max_positions": 1,
    "lookback": 1,
    "liquidity_lookback": 20,
    "min_avg_turnover": 20_000_000.0,
    "min_price": 0.5,
    "target_exposure": 0.98,
    "lot_size": 100,
    "require_positive_score": False,
}

DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. 标的</td><td>使用 510300 作为可交易的沪深300 ETF 代理，benchmark 也使用 510300 buy-and-hold。</td><td>000300 指数只作为参考指数；不把指数本身当作可下单资产。</td></tr>
<tr><td>2. 收盘前买</td><td>每个有可用日线且满足流动性过滤的交易日，在当日收盘价基础上向上滑点买入。</td><td>买入信号在当日收盘后生成并以 SAME_CLOSE 语义回测成交；不读取下一日开盘。</td></tr>
<tr><td>3. 开盘前卖</td><td>买入成交后立即挂出默认 NEXT_OPEN 卖单，下一交易日开盘价基础上向下滑点卖出。</td><td>A 股 ETF T+1 允许 D 日买入后 D+1 卖出；末尾无下一交易日的订单按过期处理。</td></tr>
<tr><td>4. 成本</td><td>正式 strict 口径使用单边 5 bps 滑点和 ETF 佣金 1 bp，无股票印花税。</td><td>滑点方向固定为买入不利、卖出不利；Stability 只展示成本情景敏感性，不做参数寻优。</td></tr>
</tbody></table></div>
"""


class _ReportCloseToOpenOvernightStrategy(CloseToOpenOvernightAnomalyStrategy):
    def __init__(self, *args: Any, last_entry_date: date | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_entry_date = last_entry_date

    def on_after_trading(self, context: Any, trading_date: date) -> None:
        if self._last_entry_date is not None and trading_date >= self._last_entry_date:
            self._pending_entry_symbols.intersection_update(
                symbol for symbol, quantity in self._positions.items() if quantity > 0
            )
            for symbol, quantity in list(self._positions.items()):
                if quantity <= 0:
                    continue
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
            self._diagnostics["last_selected"] = []
            return
        super().on_after_trading(context, trading_date)


def main() -> None:
    args = _parse_args()
    start = _parse_date(args.start, START)
    end = _parse_date(args.end, END)
    output_root = Path(args.output_root) if args.output_root else REPORT_ROOT
    trade_symbol = str(args.symbol or TRADE_SYMBOL)
    index_symbol = str(args.index_reference_symbol or INDEX_REFERENCE_SYMBOL)
    params = {
        **STRATEGY_PARAMS,
        "symbols": [trade_symbol],
        "target_exposure": float(args.target_exposure),
        "min_avg_turnover": float(args.min_avg_turnover),
    }
    inputs = _load_inputs(trade_symbol, index_symbol, start, end)
    start = max(start, inputs["coverage_start"])
    end = min(end, inputs["coverage_end"])
    params["symbols"] = [trade_symbol]
    params["lot_size"] = int(inputs["lot_sizes"].get(trade_symbol, params["lot_size"]))
    print(f"Running {STRATEGY_ID} on {trade_symbol} from {start.date()} to {end.date()}", flush=True)
    strict_report = _run_backtest(
        trade_symbol,
        params,
        inputs,
        start,
        end,
        float(args.initial_cash),
        float(args.slippage_bps),
    )
    walkforward = _walkforward_from_strict_equity(strict_report, start, end)
    stability = _stability_from_bar_replay(
        inputs["trade_bars"],
        start,
        end,
        float(args.initial_cash),
        params,
        float(args.slippage_bps),
    )
    report_path, payload_path = _write_reports(
        output_root,
        trade_symbol,
        index_symbol,
        params,
        strict_report,
        walkforward,
        stability,
        start,
        end,
        float(args.initial_cash),
        float(args.slippage_bps),
        inputs,
    )
    metrics = strict_report.get("metrics") or {}
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "full_report_path": str(report_path),
                "payload_path": str(payload_path),
                "symbol": trade_symbol,
                "benchmark_symbol": (strict_report.get("benchmark") or {}).get("symbol"),
                "period": strict_report.get("period"),
                "sharpe": metrics.get("sharpe"),
                "cagr": metrics.get("cagr"),
                "total_return": metrics.get("total_return"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "total_trades": metrics.get("total_trades"),
                "cost_drag_pct": (strict_report.get("diagnostics") or {}).get("cost_drag_pct"),
                "walkforward_verdict": walkforward.get("verdict"),
                "stability_status": stability.get("status"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START.date().isoformat())
    parser.add_argument("--end", default=END.date().isoformat())
    parser.add_argument("--symbol", default=TRADE_SYMBOL)
    parser.add_argument("--index-reference-symbol", default=INDEX_REFERENCE_SYMBOL)
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--target-exposure", type=float, default=STRATEGY_PARAMS["target_exposure"])
    parser.add_argument("--min-avg-turnover", type=float, default=STRATEGY_PARAMS["min_avg_turnover"])
    parser.add_argument("--slippage-bps", type=float, default=SLIPPAGE_BPS)
    parser.add_argument("--output-root", default="")
    return parser.parse_args()


def _parse_date(value: str, fallback: datetime) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d") if value else fallback


def _load_inputs(trade_symbol: str, index_symbol: str, start: datetime, end: datetime) -> Dict[str, Any]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        trade_bars = db_provider.get_bars(trade_symbol, start, end, "1d")
        if trade_bars.empty:
            raise RuntimeError(f"No local daily bars for {trade_symbol}")
        index_bars = db_provider.get_bars(index_symbol, start, end, "1d")
        lot_sizes = _load_lot_sizes(db_provider, [trade_symbol], is_cn_symbol)
        lot_sizes[trade_symbol] = int(lot_sizes.get(trade_symbol) or 100)
    finally:
        db_provider.disconnect()
    trade_bars = trade_bars.copy()
    trade_bars["timestamp"] = pd.to_datetime(trade_bars["timestamp"])
    trade_bars = trade_bars.sort_values("timestamp")
    price_column = "adj_close" if "adj_close" in trade_bars.columns and not trade_bars["adj_close"].isna().all() else "close"
    benchmark_provider = BenchmarkProvider(trade_bars, price_column=price_column)
    coverage_start = pd.Timestamp(trade_bars["timestamp"].min()).to_pydatetime()
    coverage_end = pd.Timestamp(trade_bars["timestamp"].max()).to_pydatetime()
    benchmark_meta = {
        "symbol": trade_symbol,
        "coverage_start": str(coverage_start.date()),
        "coverage_end": str(coverage_end.date()),
        "rows": int(len(trade_bars)),
        "fallback_used": False,
        "price_column": price_column,
    }
    index_meta = _bar_meta(index_symbol, index_bars)
    survivorship_audit = {
        "scope": "single_etf_proxy",
        "trade_symbol": trade_symbol,
        "benchmark_symbol": trade_symbol,
        "index_reference_symbol": index_symbol,
        "note": "No constituent universe selection is used; 510300 is the tradable ETF proxy for CSI300 exposure.",
    }
    return {
        "trade_bars": trade_bars,
        "index_bars": index_bars,
        "benchmark_provider": benchmark_provider,
        "benchmark_meta": benchmark_meta,
        "index_meta": index_meta,
        "lot_sizes": lot_sizes,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "survivorship_audit": survivorship_audit,
        "last_entry_date": coverage_end.date(),
    }


def _bar_meta(symbol: str, bars: pd.DataFrame) -> Dict[str, Any]:
    if bars is None or bars.empty:
        return {"symbol": symbol, "coverage_start": "", "coverage_end": "", "rows": 0}
    timestamps = pd.to_datetime(bars["timestamp"], errors="coerce").dropna()
    return {
        "symbol": symbol,
        "coverage_start": str(timestamps.min().date()) if not timestamps.empty else "",
        "coverage_end": str(timestamps.max().date()) if not timestamps.empty else "",
        "rows": int(len(bars)),
    }


def _run_backtest(
    trade_symbol: str,
    params: Dict[str, Any],
    inputs: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
    slippage_bps: float,
) -> Dict[str, Any]:
    data_provider = _DuckDBDailyDateProvider(
        [trade_symbol],
        start,
        end,
        include_daily_basic=False,
        include_financial_indicators=False,
        include_execution_liquidity_features=True,
    )
    strategy = _ReportCloseToOpenOvernightStrategy(
        **params,
        last_entry_date=inputs["last_entry_date"],
    )
    backtest_config = {
        "slippage_bps": float(slippage_bps),
        "force_close_on_stop": False,
    }
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {
            "max_position_pct": 1.0,
            "max_sector_pct": 1.0,
            "max_daily_loss_pct": 0.20,
            "max_leverage": 1.0,
        },
    }
    backtester = Backtester(
        bt_config,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
        lot_sizes=inputs["lot_sizes"],
        benchmark_provider=inputs["benchmark_provider"],
    )
    try:
        bt_result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=initial_cash,
            data_provider=data_provider,
            symbols=[trade_symbol],
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = inputs["benchmark_provider"].get_benchmark_equity(start, end, initial_cash)
    return _strict_backtest_report(
        bt_result,
        start,
        end,
        initial_cash,
        [trade_symbol],
        inputs["benchmark_meta"],
        inputs["lot_sizes"],
        strategy,
        benchmark_equity_curve,
        inputs["survivorship_audit"],
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _write_reports(
    output_root: Path,
    trade_symbol: str,
    index_symbol: str,
    params: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    stability: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
    slippage_bps: float,
    inputs: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = output_root / STRATEGY_ID
    run_dir = strategy_dir / "runs"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    row = _hypothesis_row(
        trade_symbol,
        index_symbol,
        params,
        strict_report,
        walkforward,
        stability,
        start,
        end,
        initial_cash,
        slippage_bps,
        inputs,
    )
    result = _result_payload(row)
    generated = datetime.now(timezone.utc).isoformat()
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    strict_html = _insert_detail_section(build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated))
    walkforward_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    full_html = _insert_detail_section(build_research_full_report_html(result, [row], generated_at=generated))
    payload = {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "initial_cash": initial_cash,
        "trade_symbol": trade_symbol,
        "index_reference_symbol": index_symbol,
        "parameters": params,
        "slippage_bps": slippage_bps,
        "commission": COMMISSION_CFG,
        "strict_report": strict_report,
        "walkforward": walkforward,
        "stability": stability,
        "hypothesis": row,
        "result": result,
    }
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload_path = strategy_dir / "last_result.json"
    walkforward_path = strategy_dir / "walkforward_result.json"
    stability_path = strategy_dir / "stability_result.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    walkforward_path.write_text(json.dumps(walkforward, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    stability_path.write_text(json.dumps(stability, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / f"{run_ts}_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / f"{run_ts}_walkforward_result.json").write_text(json.dumps(walkforward, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (run_dir / f"{run_ts}_stability_result.json").write_text(json.dumps(stability, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    reports = {
        "fast_research_report.html": fast_html,
        "strict_backtest_report.html": strict_html,
        "walkforward_audit_report.html": walkforward_html,
        "full_research_report.html": full_html,
    }
    for filename, html in reports.items():
        (strategy_dir / filename).write_text(html, encoding="utf-8")
        (run_dir / f"{run_ts}_{filename}").write_text(html, encoding="utf-8")
    latest_dir = output_root / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    for filename, html in reports.items():
        (latest_dir / filename).write_text(html, encoding="utf-8")
    (latest_dir / "last_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return strategy_dir / "full_research_report.html", payload_path


def _hypothesis_row(
    trade_symbol: str,
    index_symbol: str,
    params: Dict[str, Any],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    stability: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
    slippage_bps: float,
    inputs: Dict[str, Any],
) -> Dict[str, Any]:
    metrics = {
        "strict_backtest": strict_report,
        "walkforward": walkforward,
        "parameter_sensitivity": stability,
    }
    metrics["research_stage_conclusions"] = _stage_conclusions(strict_report, walkforward, stability)
    status = _row_status(strict_report, walkforward, stability)
    return {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "thesis": "隔夜收益可能主要发生在收盘到次日开盘区间；用可交易 300ETF 检验收盘买、开盘卖在成本后的净表现。",
        "status": status,
        "stage": "full_research",
        "source": "local_strategy",
        "source_url": SOURCE_URLS[0],
        "decision_reason": _decision_reason(strict_report, walkforward, stability),
        "metrics": metrics,
        "evidence": {
            "source": "user-provided close-to-open overnight anomaly thesis",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "metadata": {"source": "local_strategy"},
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "etf_timing_overnight_anomaly",
                "signal_formula_key": "close_to_open_overnight_return",
                "prediction_direction": "higher_is_better",
                "symbols_count": 1,
                "universe": [trade_symbol],
                "universe_source": f"{trade_symbol} local CN ETF daily bars; {index_symbol} is reference index only",
                "lookback_days": int(params["lookback"]),
                "horizon_days": 1,
                "execution_lag_days": 0,
                "rebalance_frequency": "daily close entry and next trading day open exit",
                "required_fields": ["timestamp", "open", "close", "volume", "turnover"],
                "parameters": params,
                "parameter_explanations": _parameter_explanations(slippage_bps),
                "strategy_logic": _strategy_logic(trade_symbol, index_symbol, start, end, initial_cash, slippage_bps, inputs),
                "source_report_urls": SOURCE_URLS,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {"cagr_gt": 0.05, "max_drawdown_gte": -0.30, "max_adv_participation_lte": 0.05},
            },
        },
    }


def _strategy_logic(
    trade_symbol: str,
    index_symbol: str,
    start: datetime,
    end: datetime,
    initial_cash: float,
    slippage_bps: float,
    inputs: Dict[str, Any],
) -> Dict[str, Any]:
    index_meta = inputs.get("index_meta") or {}
    return {
        "core_idea": "若沪深300风险溢价集中在隔夜区间，则每天收盘前持有 300ETF、次日开盘退出应捕捉该收益段。",
        "universe": f"{trade_symbol} 单 ETF 代理交易，回测窗口 {start.date()} 到 {end.date()}，初始资金 {initial_cash:,.0f} CNY。",
        "entry_filters": [
            "daily bar exists",
            f"close >= {STRATEGY_PARAMS['min_price']}",
            f"20-day average turnover >= {STRATEGY_PARAMS['min_avg_turnover']:,.0f}",
            "tradable and not suspended",
        ],
        "ranking_rule": "单标的无横截面排序；require_positive_score=False，每个合格交易日都尝试隔夜持有。",
        "portfolio_construction": f"目标仓位 {STRATEGY_PARAMS['target_exposure']:.0%}，按 ETF 100 份手数取整，现金保留作滑点与佣金缓冲。",
        "rebalance_rule": f"D 日收盘价向上滑点 {slippage_bps:.1f} bps 买入，D+1 开盘价向下滑点 {slippage_bps:.1f} bps 卖出。",
        "exit_rule": "买入成交后立即提交 NEXT_OPEN 卖单；最后一个交易日不再新开隔夜仓，避免无下一日开盘的强制平仓假象。",
        "risk_budget": "单 ETF long-only、无杠杆、无做空；容量只按日线成交额估算，实盘仍需集合竞价盘口复核。",
        "index_reference": f"{index_symbol} rows={index_meta.get('rows', 0)} coverage={index_meta.get('coverage_start', '')}-{index_meta.get('coverage_end', '')}",
        "parameter_explanations": _parameter_explanations(slippage_bps),
    }


def _parameter_explanations(slippage_bps: float) -> Dict[str, str]:
    return {
        "symbols": "510300 是本次可交易 300ETF 代理；000300 指数只作参考，不用于下单。",
        "max_positions": "单标的策略，最多持有 1 个 ETF。",
        "lookback": "仅用于用已知历史隔夜收益形成候选分数；不作为正分数过滤或参数寻优。",
        "liquidity_lookback": "计算平均成交额过滤的历史天数。",
        "min_avg_turnover": "过滤极端低流动性日期；510300 正常情况下应满足。",
        "target_exposure": "每晚目标资金暴露，保留现金支付成本和手数取整误差。",
        "lot_size": "A 股 ETF 买入按 100 份取整。",
        "require_positive_score": "False 表示不做择时过滤，直接检验论文式日频隔夜持有命题。",
        "slippage_bps": f"正式 strict 口径为单边 {slippage_bps:.1f} bps；买入向上、卖出向下。",
    }


def _stage_conclusions(
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    stability: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    return {
        "fast_research": {
            "label": "快研究",
            "verdict": "not_applicable",
            "conclusion": "本策略是单标的时间序列执行假设，不适用横截面 Rank IC 快研门槛。",
            "method": "直接进入可交易 ETF strict Backtester；Stability 使用成本情景审计。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
            "method": "项目 Backtester；510300、D close SAME_CLOSE 买、D+1 open 卖、100 份手数、ETF 佣金和固定不利滑点。",
        },
        "walkforward_strict_audit": {
            "label": "Walk-forward strict audit",
            "verdict": str(walkforward.get("verdict") or "fail"),
            "conclusion": (
                f"冻结规则日历 OOS：aggregate={float(walkforward.get('aggregate_oos_sharpe') or 0.0):.2f}，"
                f"worst={float(walkforward.get('worst_oos_sharpe') or 0.0):.2f}，"
                f"盈利 split={float(walkforward.get('pct_profitable_splits') or 0.0):.0%}。"
            ),
            "method": "从 strict Backtester equity curve 切分日历 OOS；不做参数重估。",
        },
        "final_decision": {
            "label": "Final Decision",
            "verdict": _row_status(strict_report, walkforward, stability),
            "conclusion": _decision_reason(strict_report, walkforward, stability),
            "method": "正式结论按 strict checklist；walk-forward 和 stability 作为审计证据。",
        },
    }


def _walkforward_from_strict_equity(strict_report: Dict[str, Any], start: datetime, end: datetime) -> Dict[str, Any]:
    points = ((strict_report.get("equity_curve") or {}).get("strategy") or [])
    if not points:
        return _empty_walkforward("strict equity curve missing")
    frame = pd.DataFrame(points)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna().sort_values("date")
    split_ranges = [
        ("2018-01-01", "2019-12-31", start.date().isoformat(), "2017-12-31"),
        ("2020-01-01", "2021-12-31", "2018-01-01", "2019-12-31"),
        ("2022-01-01", "2023-12-31", "2020-01-01", "2021-12-31"),
        ("2024-01-01", end.date().isoformat(), "2022-01-01", "2023-12-31"),
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
                "trade_count": None,
                "has_trades": True,
                "total_return": total_return,
                "verdict": "pass" if sharpe > 0 and total_return > 0 else "fail",
                "parameters": "frozen daily close-to-open rule",
            }
        )
    if not splits:
        return _empty_walkforward("no OOS split had enough equity points")
    sharpes = [float(split["oos_sharpe"]) for split in splits]
    profitable = [1.0 if float(split.get("total_return") or 0.0) > 0 else 0.0 for split in splits]
    aggregate = statistics.mean(sharpes)
    worst = min(sharpes)
    pct_profitable = statistics.mean(profitable)
    capacity_ok = _strict_capacity_ok(strict_report)
    is_viable = worst >= 0.3 and pct_profitable >= 0.5 and capacity_ok
    verdict = "pass" if is_viable else ("warn" if aggregate > 0 and pct_profitable >= 0.5 else "fail")
    return {
        "verdict": verdict,
        "reason": "Frozen-rule calendar OOS audit derived from strict Backtester equity; no parameter refit.",
        "is_viable": is_viable,
        "capacity_ok": capacity_ok,
        "thresholds": _walkforward_thresholds(),
        "aggregate_oos_sharpe": aggregate,
        "worst_oos_sharpe": worst,
        "pct_profitable_splits": pct_profitable,
        "deflated_sharpe_ratio": None,
        "sharpe_degradation": aggregate - worst if aggregate > 0 else 0.0,
        "regime_breakdown": strict_report.get("regime_breakdown") or {},
        "bull_only_warning": False,
        "n_splits": len(splits),
        "evaluated_splits": len(splits),
        "total_splits": len(splits),
        "no_trade_splits": 0,
        "splits": splits,
    }


def _stability_from_bar_replay(
    bars: pd.DataFrame,
    start: datetime,
    end: datetime,
    initial_cash: float,
    params: Dict[str, Any],
    base_slippage_bps: float,
) -> Dict[str, Any]:
    scenarios = [0.0, 3.0, base_slippage_bps, 10.0]
    seen = set()
    rows = []
    for slippage in scenarios:
        if slippage in seen:
            continue
        seen.add(slippage)
        metrics = _bar_replay_metrics(bars, start, end, initial_cash, slippage, params)
        rows.append(
            {
                "name": f"slippage_{slippage:g}bps",
                "parameters": {**params, "slippage_bps": slippage},
                "cagr": metrics["cagr"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "sharpe": metrics["sharpe"],
                "total_return": metrics["total_return"],
                "verdict": "pass" if metrics["cagr"] > 0 and metrics["max_drawdown_pct"] >= -0.30 else "fail",
            }
        )
    base_row = next((row for row in rows if float((row.get("parameters") or {}).get("slippage_bps")) == base_slippage_bps), rows[-1])
    base_cagr = float(base_row.get("cagr") or 0.0)
    worst_cagr = min(float(row.get("cagr") or 0.0) for row in rows)
    degradation = 100.0 if base_cagr <= 0 else max(0.0, (base_cagr - worst_cagr) / abs(base_cagr) * 100.0)
    pass_count = sum(1 for row in rows if row.get("verdict") == "pass")
    status = "pass" if pass_count >= 3 and degradation <= 30.0 else ("warn" if pass_count > 0 else "fail")
    return {
        "status": status,
        "method": "Bar-level cost scenario replay around the locked close-to-open rule; strict Backtester base remains authoritative.",
        "base_params": {**params, "slippage_bps": base_slippage_bps},
        "selected_params": {**params, "slippage_bps": base_slippage_bps},
        "best_params": max(rows, key=lambda row: float(row.get("cagr") or -999.0)).get("parameters"),
        "tested_count": len(rows),
        "pass_count": pass_count,
        "max_degradation_pct": degradation,
        "stability_note": "Cost sensitivity is the main robustness axis for a daily overnight ETF strategy; no parameter optimization was used.",
        "rows": rows,
    }


def _bar_replay_metrics(
    bars: pd.DataFrame,
    start: datetime,
    end: datetime,
    initial_cash: float,
    slippage_bps: float,
    params: Dict[str, Any],
) -> Dict[str, float]:
    frame = bars.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= end)].sort_values("timestamp")
    cash = float(initial_cash)
    lot_size = int(params.get("lot_size") or 100)
    exposure = float(params.get("target_exposure") or 0.98)
    values = []
    dates = []
    fee_rate = float(COMMISSION_CFG["CN"]["fund_percent"])
    records = frame.to_dict("records")
    for idx in range(1, max(1, len(records) - 1)):
        today = records[idx]
        tomorrow = records[idx + 1]
        close_price = _positive_float(today.get("adj_close")) or _positive_float(today.get("close"))
        next_open = _positive_float(tomorrow.get("adj_open")) or _positive_float(tomorrow.get("open"))
        if close_price <= 0 or next_open <= 0:
            values.append(cash)
            dates.append(pd.Timestamp(today["timestamp"]))
            continue
        buy_price = close_price * (1.0 + slippage_bps / 10000.0)
        target_value = cash * exposure
        quantity = int(target_value / buy_price) // lot_size * lot_size
        if quantity < lot_size:
            values.append(cash)
            dates.append(pd.Timestamp(today["timestamp"]))
            continue
        buy_commission = buy_price * quantity * fee_rate
        if buy_price * quantity + buy_commission > cash:
            quantity = int((cash / (buy_price * (1.0 + fee_rate))) // lot_size) * lot_size
        if quantity < lot_size:
            values.append(cash)
            dates.append(pd.Timestamp(today["timestamp"]))
            continue
        buy_commission = buy_price * quantity * fee_rate
        cash -= buy_price * quantity + buy_commission
        sell_price = next_open * (1.0 - slippage_bps / 10000.0)
        sell_commission = sell_price * quantity * fee_rate
        cash += sell_price * quantity - sell_commission
        values.append(cash)
        dates.append(pd.Timestamp(tomorrow["timestamp"]))
    if not values:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    series = pd.Series(values, index=pd.to_datetime(dates)).sort_index()
    returns = series.pct_change(fill_method=None).dropna()
    total_return = float(series.iloc[-1] / initial_cash - 1.0)
    days = max(1, (end - start).days)
    cagr = (float(series.iloc[-1]) / initial_cash) ** (365.25 / days) - 1.0 if series.iloc[-1] > 0 else -1.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": _sharpe(returns),
        "max_drawdown_pct": _max_drawdown(series),
    }


def _empty_walkforward(reason: str) -> Dict[str, Any]:
    return {
        "verdict": "fail",
        "reason": reason,
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


def _walkforward_thresholds() -> Dict[str, Any]:
    return {
        "train_window_days": 504,
        "test_window_days": 504,
        "step_days": 504,
        "purge_days": 1,
        "embargo_days": 0,
        "min_train_observations": 252,
        "min_worst_oos_sharpe": 0.3,
        "min_profitable_splits_pct": 0.5,
        "min_deflated_sharpe_ratio": 0.95,
        "max_adv_pct": 0.05,
    }


def _sharpe(returns: pd.Series) -> float:
    returns = returns.dropna()
    if returns.empty:
        return 0.0
    std = float(returns.std())
    return float(returns.mean() / std * math.sqrt(252.0)) if std > 0 else 0.0


def _max_drawdown(equity: Iterable[float]) -> float:
    peak = None
    worst = 0.0
    for value in equity:
        number = float(value)
        peak = number if peak is None else max(peak, number)
        if peak and peak > 0:
            worst = min(worst, number / peak - 1.0)
    return worst


def _result_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    status = str(row.get("status") or "")
    wf = ((row.get("metrics") or {}).get("walkforward") or {})
    return {
        "run_id": f"{STRATEGY_ID}_full_research",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "discovered": 1,
        "evaluated": 1,
        "specified": 1,
        "validated": 0,
        "validated_passed": 0,
        "integrated": 1,
        "backtested": 1,
        "walkforward_passed": 1 if wf.get("is_viable") else 0,
        "rejected": 1 if status == "rejected" else 0,
        "errors": [],
    }


def _row_status(strict_report: Dict[str, Any], walkforward: Dict[str, Any], stability: Dict[str, Any]) -> str:
    if _strict_pass(strict_report) and bool(walkforward.get("is_viable")) and stability.get("status") == "pass":
        return "candidate"
    metrics = strict_report.get("metrics") or {}
    if float(metrics.get("cagr") or 0.0) <= 0 or float(metrics.get("sharpe") or 0.0) <= 0:
        return "rejected"
    return "needs_more_validation"


def _decision_reason(
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    stability: Dict[str, Any],
) -> str:
    metrics = strict_report.get("metrics") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    return (
        f"strict Sharpe={float(metrics.get('sharpe') or 0.0):.2f}, "
        f"CAGR={float(metrics.get('cagr') or 0.0):.2%}, "
        f"MaxDD={float(metrics.get('max_drawdown_pct') or 0.0):.2%}, "
        f"cost_drag={float(diagnostics.get('cost_drag_pct') or 0.0):.2f}%; "
        f"walkforward viable={bool(walkforward.get('is_viable'))}; "
        f"stability={stability.get('status')}."
    )


def _strict_pass(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    return (
        float(metrics.get("cagr") or 0.0) >= 0.05
        and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30
        and int(metrics.get("total_trades") or 0) > 50
        and _strict_capacity_ok(strict_report)
    )


def _strict_capacity_ok(strict_report: Dict[str, Any]) -> bool:
    capacity = strict_report.get("capacity") or {}
    try:
        return float(capacity.get("max_adv_participation")) <= 0.05
    except (TypeError, ValueError):
        return False


def _positive_float(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _insert_detail_section(html: str) -> str:
    marker = "<h2>2. 严格回测证据</h2>\n<h3>策略执行逻辑</h3>"
    if marker in html:
        return html.replace(marker, f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}<h3>策略执行逻辑</h3>", 1)
    marker = "<h2>3. Strategy Logic And Core Evidence</h2>\n"
    if marker in html:
        return html.replace(marker, f"{marker}{DETAIL_SECTION}", 1)
    return html


if __name__ == "__main__":
    main()
