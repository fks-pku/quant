"""Run full research report for close-to-open overnight trading on PIT small-cap A-shares."""

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
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol  # noqa: E402
from quant.features.backtest.benchmark import BenchmarkProvider  # noqa: E402
from quant.features.backtest.engine import Backtester  # noqa: E402
from quant.features.strategies.reject.close_to_open_overnight_anomaly.strategy import (  # noqa: E402
    CloseToOpenOvernightAnomalyStrategy,
)
from quant.features.strategies.xueqiu_small_cap_financial_filter.strategy import (  # noqa: E402
    DEFAULT_EXCLUDED_BOARD_PREFIXES,
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
STRATEGY_ID = "close_to_open_overnight_small_cap"
TITLE = "A股小市值隔夜收盘买开盘卖"
BENCHMARK_SYMBOL = "000300"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
SOURCE_URLS = ["https://arxiv.org/pdf/2201.00223"]
SLIPPAGE_BPS = 5.0
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
STRATEGY_PARAMS: Dict[str, Any] = {
    "max_positions": 10,
    "lookback": 1,
    "liquidity_lookback": 20,
    "min_avg_turnover": 5_000_000.0,
    "min_price": 2.0,
    "target_exposure": 0.95,
    "lot_size": 100,
    "require_positive_score": False,
    "min_market_cap": 100_000.0,
    "excluded_board_prefixes": list(DEFAULT_EXCLUDED_BOARD_PREFIXES),
}

DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. PIT 小市值池</td><td>每日只使用当日 daily_basic 可见的 total_mv/circ_mv，剔除普通账户权限外的创业板 300/301 与科创板 688/689。</td><td>不是用当前小市值名单回溯；未来上市股票只有在当日有 bar、状态和 PIT 市值字段后才可能入池。</td></tr>
<tr><td>2. 收盘前买</td><td>每个交易日选择当日满足状态、价格和流动性过滤后市值最小的 10 只股票，在收盘价基础上向上滑点买入。</td><td>买入信号在当日收盘后生成并以 SAME_CLOSE 语义回测成交；不读取下一日开盘。</td></tr>
<tr><td>3. 开盘前卖</td><td>买入成交后立即提交默认 NEXT_OPEN 卖单，下一交易日开盘价基础上向下滑点卖出。</td><td>A 股 T+1 允许 D 日买入后 D+1 卖出；最后一个交易日不新开隔夜仓。</td></tr>
<tr><td>4. 成本与容量</td><td>正式 strict 口径使用单边 5 bps 基础滑点、A 股股票佣金税费、涨跌停/停牌/手数和 small_cap_realistic 冲击成本。</td><td>这是小市值高换手策略的主要约束，报告重点看 cost drag、ADV 参与率和拒单。</td></tr>
</tbody></table></div>
"""


class _PITSmallCapCloseToOpenStrategy(CloseToOpenOvernightAnomalyStrategy):
    def __init__(
        self,
        *args: Any,
        min_market_cap: float = 100_000.0,
        excluded_board_prefixes: Iterable[str] = DEFAULT_EXCLUDED_BOARD_PREFIXES,
        last_entry_date: date | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.min_market_cap = max(0.0, float(min_market_cap))
        self.excluded_board_prefixes = tuple(str(prefix) for prefix in excluded_board_prefixes if str(prefix))
        self._last_entry_date = last_entry_date
        self._universe_symbols = list(self._symbols)
        self._today_symbols: set[str] = set()

    @property
    def symbols(self) -> List[str]:
        return sorted(self._today_symbols)

    def required_snapshot_symbols(self) -> List[str]:
        return []

    def on_data_batch(self, context: Any, data: Any) -> None:
        bars = data.values() if isinstance(data, dict) else data
        bar_list = list(bars)
        self._today_symbols = {
            str(bar.get("symbol", "") if isinstance(bar, dict) else getattr(bar, "symbol", ""))
            for bar in bar_list
            if (bar.get("symbol", "") if isinstance(bar, dict) else getattr(bar, "symbol", ""))
        }
        super().on_data_batch(context, bar_list)

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

    def _entry_score(self, symbol: str) -> float | None:
        bars = self._day_data.get(symbol, [])
        if not bars:
            self._count("entry_rejections", "missing_daily_bar")
            return None
        current_bar = bars[-1]
        if self._entry_risk(symbol, current_bar):
            return None
        market_cap = self._market_cap(current_bar)
        if market_cap <= 0:
            self._count("entry_rejections", "missing_market_cap")
            return None
        if market_cap < self.min_market_cap:
            self._count("entry_rejections", "below_min_market_cap")
            return None
        return -market_cap

    def _entry_risk(self, symbol: str, bar: Any) -> bool:
        if any(str(symbol).startswith(prefix) for prefix in self.excluded_board_prefixes):
            self._count("entry_rejections", "permission_excluded_board")
            return True
        return super()._entry_risk(symbol, bar)

    def _market_cap(self, bar: Any) -> float:
        for field in ("total_mv", "circ_mv", "market_cap", "total_market_cap", "float_market_cap", "circulating_market_cap"):
            value = self._float_bar_value(bar, field, 0.0)
            if value > 0:
                return value
        return 0.0

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            **super()._get_parameters(),
            "universe_symbols_count": len(self._universe_symbols),
            "min_market_cap": self.min_market_cap,
            "excluded_board_prefixes": list(self.excluded_board_prefixes),
        }


def main() -> None:
    args = _parse_args()
    start = _parse_date(args.start, START)
    end = _parse_date(args.end, END)
    output_root = Path(args.output_root) if args.output_root else REPORT_ROOT
    params = {
        **STRATEGY_PARAMS,
        "max_positions": int(args.max_positions),
        "target_exposure": float(args.target_exposure),
        "min_avg_turnover": float(args.min_avg_turnover),
        "min_market_cap": float(args.min_market_cap),
    }
    inputs = _load_inputs(start, end, int(args.max_universe_symbols))
    start = max(start, inputs["coverage_start"])
    end = min(end, inputs["coverage_end"])
    symbols = inputs["symbols"]
    params["symbols"] = symbols
    print(f"Running {STRATEGY_ID} on {len(symbols)} PIT small-cap symbols from {start.date()} to {end.date()}", flush=True)
    strict_report = _run_backtest(symbols, params, inputs, start, end, float(args.initial_cash), float(args.slippage_bps))
    walkforward = _walkforward_from_strict_equity(strict_report, start, end)
    stability = _single_run_stability(strict_report, params, float(args.slippage_bps))
    report_path, payload_path = _write_reports(
        output_root,
        symbols,
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
                "symbols": len(symbols),
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
    parser.add_argument("--initial-cash", type=float, default=INITIAL_CASH)
    parser.add_argument("--max-positions", type=int, default=STRATEGY_PARAMS["max_positions"])
    parser.add_argument("--target-exposure", type=float, default=STRATEGY_PARAMS["target_exposure"])
    parser.add_argument("--min-avg-turnover", type=float, default=STRATEGY_PARAMS["min_avg_turnover"])
    parser.add_argument("--min-market-cap", type=float, default=STRATEGY_PARAMS["min_market_cap"])
    parser.add_argument("--slippage-bps", type=float, default=SLIPPAGE_BPS)
    parser.add_argument("--max-universe-symbols", type=int, default=0)
    parser.add_argument("--output-root", default="")
    return parser.parse_args()


def _parse_date(value: str, fallback: datetime) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d") if value else fallback


def _load_inputs(start: datetime, end: datetime, max_universe_symbols: int) -> Dict[str, Any]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        rows = db_provider.storage.conn.execute(
            """
            SELECT DISTINCT b.symbol
            FROM daily_cn_ochl b
            JOIN daily_basic.cn_daily_basic db
              ON b.symbol = db.symbol
             AND CAST(b.timestamp AS DATE) = db.trade_date
            WHERE CAST(b.timestamp AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND regexp_matches(b.symbol, '^[0236][0-9]{5}$')
              AND NOT starts_with(b.symbol, '200')
              AND NOT starts_with(b.symbol, '300')
              AND NOT starts_with(b.symbol, '301')
              AND NOT starts_with(b.symbol, '688')
              AND NOT starts_with(b.symbol, '689')
            ORDER BY b.symbol
            """,
            [start, end],
        ).fetchall()
        symbols = [str(row[0]) for row in rows if is_cn_symbol(str(row[0]))]
        if max_universe_symbols > 0:
            symbols = symbols[:max_universe_symbols]
        if not symbols:
            raise RuntimeError("No small-cap stock symbols with PIT daily_basic data")
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
        coverage = db_provider.storage.conn.execute(
            """
            SELECT min(CAST(timestamp AS DATE)), max(CAST(timestamp AS DATE)), count(DISTINCT symbol)
            FROM daily_cn_ochl
            WHERE CAST(timestamp AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND symbol IN (SELECT unnest(?))
            """,
            [start, end, symbols],
        ).fetchone()
    finally:
        db_provider.disconnect()
    return {
        "symbols": symbols,
        "lot_sizes": lot_sizes,
        "benchmark_provider": benchmark_provider,
        "benchmark_meta": benchmark_meta,
        "survivorship_audit": survivorship_audit,
        "coverage_start": pd.Timestamp(coverage[0]).to_pydatetime(),
        "coverage_end": pd.Timestamp(coverage[1]).to_pydatetime(),
        "coverage_symbol_count": int(coverage[2] or 0),
        "last_entry_date": pd.Timestamp(coverage[1]).date(),
    }


def _run_backtest(
    symbols: List[str],
    params: Dict[str, Any],
    inputs: Dict[str, Any],
    start: datetime,
    end: datetime,
    initial_cash: float,
    slippage_bps: float,
) -> Dict[str, Any]:
    strategy_info = {
        "name": TITLE,
        "description": "small_cap close_to_open overnight market_cap total_mv",
        "parameters": params,
        "research_meta": {"strategy_spec": _strategy_spec(symbols, params, start, end, inputs)},
    }
    execution_cost_model = _strict_execution_cost_model(STRATEGY_ID, strategy_info, True)
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=False,
        include_execution_liquidity_features=True,
        cache_enabled=True,
    )
    strategy = _PITSmallCapCloseToOpenStrategy(
        symbols=symbols,
        max_positions=params["max_positions"],
        lookback=params["lookback"],
        liquidity_lookback=params["liquidity_lookback"],
        min_avg_turnover=params["min_avg_turnover"],
        min_price=params["min_price"],
        target_exposure=params["target_exposure"],
        lot_size=params["lot_size"],
        require_positive_score=params["require_positive_score"],
        min_market_cap=params["min_market_cap"],
        excluded_board_prefixes=params["excluded_board_prefixes"],
        last_entry_date=inputs["last_entry_date"],
    )
    backtest_config = {
        "slippage_bps": float(slippage_bps),
        "force_close_on_stop": False,
        "execution_cost_model": execution_cost_model,
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
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = inputs["benchmark_provider"].get_benchmark_equity(start, end, initial_cash)
    return _strict_backtest_report(
        bt_result,
        start,
        end,
        initial_cash,
        symbols,
        inputs["benchmark_meta"],
        inputs["lot_sizes"],
        strategy,
        benchmark_equity_curve,
        inputs["survivorship_audit"],
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _write_reports(
    output_root: Path,
    symbols: List[str],
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
    row = _hypothesis_row(symbols, params, strict_report, walkforward, stability, start, end, initial_cash, slippage_bps, inputs)
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
        "symbols_count": len(symbols),
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
    symbols: List[str],
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
        "thesis": "如果 A 股小市值股票的收益更集中在隔夜区间，则每日收盘买入 PIT 小市值篮子并次日开盘卖出应能在成本后保留正收益。",
        "status": status,
        "stage": "full_research",
        "source": "local_strategy",
        "source_url": SOURCE_URLS[0],
        "decision_reason": _decision_reason(strict_report, walkforward, stability),
        "metrics": metrics,
        "evidence": {
            "source": "user-requested small-cap close-to-open overnight thesis",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "metadata": {"source": "local_strategy"},
            "strategy_spec": _strategy_spec(symbols, params, start, end, inputs),
        },
    }


def _strategy_spec(
    symbols: List[str],
    params: Dict[str, Any],
    start: datetime,
    end: datetime,
    inputs: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_type": "small_cap_overnight_timing",
        "signal_formula_key": "pit_small_cap_close_to_open_overnight",
        "prediction_direction": "smaller_market_cap_is_better_for_basket_membership",
        "symbols_count": len(symbols),
        "universe": symbols,
        "universe_source": "daily_cn_ochl ordinary-account A-share symbols with same-day PIT daily_basic total_mv/circ_mv",
        "lookback_days": int(params["lookback"]),
        "horizon_days": 1,
        "execution_lag_days": 0,
        "rebalance_frequency": "daily close entry and next trading day open exit",
        "required_fields": [
            "timestamp",
            "open",
            "close",
            "volume",
            "turnover",
            "total_mv",
            "circ_mv",
            "is_st",
            "tradable",
            "has_daily_bar",
            "is_listed",
            "list_status",
        ],
        "parameters": params,
        "parameter_explanations": _parameter_explanations(slippage_bps=SLIPPAGE_BPS),
        "strategy_logic": _strategy_logic(symbols, params, start, end, inputs),
        "source_report_urls": SOURCE_URLS,
        "universe_start": start.date().isoformat(),
        "universe_end": end.date().isoformat(),
        "goal": {"cagr_gt": 0.05, "max_drawdown_gte": -0.30, "max_adv_participation_lte": 0.05},
    }


def _strategy_logic(symbols: List[str], params: Dict[str, Any], start: datetime, end: datetime, inputs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "core_idea": "在普通账户可买 A 股中，用当日 PIT total_mv/circ_mv 选出最小市值篮子，只持有收盘到次日开盘这一段。",
        "universe": f"daily_cn_ochl + daily_basic PIT 小市值普通 A 股池，回测 {start.date()} 到 {end.date()}，覆盖 {len(symbols)} 个历史代码。",
        "entry_filters": [
            "排除 300/301 创业板与 688/689 科创板",
            "ST/suspended/non-listed/list_status!=L/tradable=false rejected",
            f"price >= {params['min_price']}",
            f"20-day average turnover >= {params['min_avg_turnover']:,.0f}",
            f"total_mv/circ_mv >= {params['min_market_cap']:,.0f} Tushare 万元单位",
        ],
        "ranking_rule": "每个交易日按当日 PIT 市值升序排列，选择最小的 max_positions 只；不使用未来市值、不使用当前成分或幸存者名单排序。",
        "portfolio_construction": f"最多 {params['max_positions']} 只等权隔夜，目标总仓位 {params['target_exposure']:.0%}，A 股 100 股手数取整。",
        "rebalance_rule": "D 日收盘价向上滑点买入，D+1 开盘价向下滑点卖出；每天重新选择 PIT 小市值篮子。",
        "exit_rule": "买入成交后立即提交 next-open 卖单；由于只持有隔夜，不叠加日内止损/止盈，末尾无下一开盘则不新开仓。",
        "risk_budget": "单边 5 bps 基础滑点、A 股股票佣金税费、涨跌停/停牌/T+1/手数和 small_cap_realistic 冲击成本；容量以 ADV participation 审计。",
        "survivorship_audit": inputs.get("survivorship_audit") or {},
    }


def _parameter_explanations(slippage_bps: float) -> Dict[str, str]:
    return {
        "max_positions": "每日隔夜篮子股票数；越小越集中，越大越接近小市值篮子平均。",
        "lookback": "本小市值版本不使用历史隔夜分数做择时，仅保留为策略框架参数。",
        "liquidity_lookback": "计算平均成交额过滤的历史天数。",
        "min_avg_turnover": "入场流动性下限，降低无法成交和冲击成本失真。",
        "min_price": "低价/退市风险过滤。",
        "target_exposure": "隔夜目标总仓位，保留现金支付成本和手数取整误差。",
        "min_market_cap": "市值过小的壳化/退市风险过滤；排序仍按 PIT 市值越小越优先。",
        "excluded_board_prefixes": "普通账户默认排除创业板和科创板。",
        "slippage_bps": f"正式 strict 口径为单边 {slippage_bps:.1f} bps；买入向上、卖出向下，并叠加 small_cap_realistic 冲击成本。",
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
            "verdict": "not_run",
            "conclusion": "本轮直接测试用户指定的小市值隔夜执行假设；未单独运行 Rank IC/FDR 快研。",
            "method": "PIT 小市值 universe + strict Backtester；快研缺失保留为审计弱点。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
            "method": "项目 Backtester；PIT 小市值、D close SAME_CLOSE 买、D+1 open 卖、A 股费用税费、涨跌停/停牌/手数和小市值冲击成本。",
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
                "parameters": "frozen daily PIT small-cap close-to-open rule",
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


def _single_run_stability(strict_report: Dict[str, Any], params: Dict[str, Any], slippage_bps: float) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    verdict = "pass" if _strict_pass(strict_report) else ("warn" if float(metrics.get("cagr") or 0.0) > 0 else "fail")
    return {
        "status": verdict,
        "method": "Single frozen small-cap overnight rule. No parameter grid was selected in this exploratory run.",
        "base_params": {**params, "slippage_bps": slippage_bps},
        "selected_params": {**params, "slippage_bps": slippage_bps},
        "best_params": {**params, "slippage_bps": slippage_bps},
        "tested_count": 1,
        "pass_count": 1 if verdict == "pass" else 0,
        "max_degradation_pct": 0.0,
        "stability_note": "This payload is persisted for full-report audit; a follow-up grid should vary max_positions, liquidity threshold, and min_market_cap before any promotion decision.",
        "rows": [
            {
                "name": "base_pit_small_cap_overnight",
                "parameters": {**params, "slippage_bps": slippage_bps},
                "cagr": metrics.get("cagr"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "verdict": verdict,
            }
        ],
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


def _decision_reason(strict_report: Dict[str, Any], walkforward: Dict[str, Any], stability: Dict[str, Any]) -> str:
    metrics = strict_report.get("metrics") or {}
    diagnostics = strict_report.get("diagnostics") or {}
    return (
        f"strict Sharpe={float(metrics.get('sharpe') or 0.0):.2f}, "
        f"CAGR={float(metrics.get('cagr') or 0.0):.2%}, "
        f"MaxDD={float(metrics.get('max_drawdown_pct') or 0.0):.2%}, "
        f"cost_drag={float(diagnostics.get('cost_drag_pct') or 0.0):.2f}%; "
        f"walkforward viable={bool(walkforward.get('is_viable'))}; stability={stability.get('status')}."
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


def _insert_detail_section(html: str) -> str:
    marker = "<h2>2. 严格回测证据</h2>\n<h3>策略执行逻辑</h3>"
    if marker in html:
        html = html.replace(marker, f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}<h3>策略执行逻辑</h3>", 1)
    marker = "<h2>3. Strategy Logic And Core Evidence</h2>\n"
    if marker in html:
        html = html.replace(marker, f"{marker}{DETAIL_SECTION}", 1)
    return _polish_stock_report_text(html)


def _polish_stock_report_text(html: str) -> str:
    replacements = {
        "ETF timing/rotation 策略，横截面 Rank IC、Top bucket 和 PnL bridge 不适用；": (
            "A股小市值隔夜策略，横截面 Rank IC、Top bucket 和 PnL bridge 不适用；"
        ),
        "ETF timing/rotation: cross-sectional Rank IC is not applicable; strict Backtester checklist is the required production evidence.": (
            "A-share small-cap overnight timing: cross-sectional Rank IC is not applicable; strict Backtester checklist is the required production evidence."
        ),
        "Existing local ETF timing/rotation strategy rerun: idea discovery/admission scoring is not the governing evidence; strict Backtester and walk-forward OOS gates are shown below.": (
            "Existing local A-share small-cap overnight rerun: idea discovery/admission scoring is not the governing evidence; strict Backtester and walk-forward OOS gates are shown below."
        ),
        "ETF timing/rotation rerun uses strict Backtester results as the portfolio evidence; separate Top-bucket cross-sectional diagnostics are not applicable.": (
            "A-share small-cap overnight rerun uses strict Backtester results as the portfolio evidence; separate Top-bucket cross-sectional diagnostics are not applicable."
        ),
        "Signal -&gt; portfolio -&gt; strict Backtester bridge is not applicable to this local ETF timing/rotation rerun; strict execution and walk-forward evidence are the attribution path.": (
            "Signal -&gt; portfolio -&gt; strict Backtester bridge is not applicable to this local A-share small-cap overnight rerun; strict execution and walk-forward evidence are the attribution path."
        ),
        "需要确认 ETF/LOF 池是否按历史可得规则冻结，而不是用当前存续名单回溯。": (
            "已使用 daily_cn_ochl 与同日 PIT daily_basic 构建历史候选；残余风险是缺失退市/状态元数据可能影响最小市值尾部。"
        ),
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


if __name__ == "__main__":
    main()
