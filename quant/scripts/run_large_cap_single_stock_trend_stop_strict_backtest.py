"""Run strict backtests for large-cap single-stock trend stop candidates."""

from __future__ import annotations

import json
import math
import sys
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies.daily_bar import DailyBarStrategy
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.reporting import build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 10_000.0
STRATEGY_ID = "large_cap_single_stock_trend_stop"
TITLE = "大市值单股趋势止损限仓"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
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


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "wuliangye_ma60_stop5_exp70",
        "symbol": "000858",
        "display_name": "五粮液",
        "ma_window": 60,
        "target_exposure": 0.70,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
    },
    {
        "name": "wuliangye_ma60_stop6_exp70",
        "symbol": "000858",
        "display_name": "五粮液",
        "ma_window": 60,
        "target_exposure": 0.70,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
    },
    {
        "name": "wuliangye_ma60_stop6_exp75",
        "symbol": "000858",
        "display_name": "五粮液",
        "ma_window": 60,
        "target_exposure": 0.75,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
    },
    {
        "name": "wuliangye_ma60_stop6_exp80",
        "symbol": "000858",
        "display_name": "五粮液",
        "ma_window": 60,
        "target_exposure": 0.80,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
    },
    {
        "name": "wuliangye_ma60_stop6_exp85",
        "symbol": "000858",
        "display_name": "五粮液",
        "ma_window": 60,
        "target_exposure": 0.85,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.0,
        "trailing_stop_pct": 0.0,
    },
    {
        "name": "naura_ma120_stop10_take15_trail6_exp80",
        "symbol": "002371",
        "display_name": "北方华创",
        "ma_window": 120,
        "target_exposure": 0.80,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.15,
        "trailing_stop_pct": 0.06,
    },
    {
        "name": "naura_ma120_stop10_take15_trail6_exp85",
        "symbol": "002371",
        "display_name": "北方华创",
        "ma_window": 120,
        "target_exposure": 0.85,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.15,
        "trailing_stop_pct": 0.06,
    },
    {
        "name": "naura_ma160_stop10_take20_trail8_exp80",
        "symbol": "002371",
        "display_name": "北方华创",
        "ma_window": 160,
        "target_exposure": 0.80,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.20,
        "trailing_stop_pct": 0.08,
    },
]


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次约束</th></tr></thead><tbody>
<tr><td>1. 大市值标的</td><td>只测试高成交额、高总市值 A 股单标的候选；报告记录最新 total_mv 与全 A 排名。</td><td>五粮液、北方华创；不使用未来成分股。</td></tr>
<tr><td>2. 趋势入场</td><td>收盘后用后复权收盘价计算 N 日均线；价格在均线上方且无持仓时，次日开盘买入。</td><td>N=60/120/160；信号只使用当日及历史数据。</td></tr>
<tr><td>3. 回撤控制</td><td>持仓后每日检查均线破位、固定止损，以及达到盈利阈值后的移动止盈。</td><td>目标仓位 80%-85%，不用满仓追求最高 return。</td></tr>
<tr><td>4. 严格执行</td><td>项目 Backtester 执行 T+1、真实股票佣金税费、100 股手数、涨跌停/停牌约束、5bps 基础滑点与 A 股流动性冲击成本。</td><td>目标：CAGR &gt; 10%，MaxDD 不超过 30%。</td></tr>
</tbody></table></div>
"""


class LargeCapSingleStockTrendStopStrategy(DailyBarStrategy):
    def __init__(
        self,
        symbol: str,
        ma_window: int,
        target_exposure: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        trailing_stop_pct: float,
        lot_size: int = 100,
        exit_buffer: float = 1.0,
    ):
        self.trade_symbol = str(symbol)
        self.ma_window = max(20, int(ma_window))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.stop_loss_pct = max(0.0, float(stop_loss_pct))
        self.take_profit_pct = max(0.0, float(take_profit_pct))
        self.trailing_stop_pct = max(0.0, float(trailing_stop_pct))
        self.lot_size = max(1, int(lot_size))
        self.exit_buffer = min(max(float(exit_buffer), 0.80), 1.05)
        self.max_position_pct = self.target_exposure
        self.max_positions = 1
        self._entry_prices: Dict[str, float] = {}
        self._peak_prices: Dict[str, float] = {}
        self._last_signal: Dict[str, Any] = {}
        super().__init__(STRATEGY_ID, [self.trade_symbol], holding_days=1)

    @property
    def _max_keep_hint(self) -> int:
        return self.ma_window + 5

    def _execute_rebalance(self, context: Any, trading_date) -> None:
        symbol = self.trade_symbol
        bar = self._get_last_bar(symbol)
        if not bar:
            return
        price = self._get_last_price(symbol)
        trend_on, adjusted_close, moving_average = self._trend_state(symbol)
        current_quantity = int(self._positions.get(symbol, 0) or 0)
        exit_reason = ""
        if current_quantity > 0:
            exit_reason = self._exit_reason(symbol, price, trend_on, adjusted_close, moving_average)
            if exit_reason:
                self.sell(symbol, current_quantity, "MARKET", price if price > 0 else None)
        elif trend_on and self._tradable_bar(bar):
            target_quantity = self._round_lot(self._portfolio_nav(context) * self.target_exposure / max(price, 1e-9))
            if target_quantity > 0:
                self.buy(symbol, target_quantity, "MARKET", price)
        self._last_signal = {
            "date": str(trading_date),
            "symbol": symbol,
            "price": price,
            "adjusted_close": adjusted_close,
            "moving_average": moving_average,
            "trend_on": trend_on,
            "position": current_quantity,
            "exit_reason": exit_reason,
        }

    def _trend_state(self, symbol: str) -> Tuple[bool, float, float]:
        closes = [price for price in self._get_closes(symbol) if price > 0 and math.isfinite(price)]
        if len(closes) < self.ma_window:
            return False, 0.0, 0.0
        moving_average = sum(closes[-self.ma_window :]) / float(self.ma_window)
        adjusted_close = closes[-1]
        return adjusted_close > moving_average, adjusted_close, moving_average

    def _exit_reason(
        self,
        symbol: str,
        price: float,
        trend_on: bool,
        adjusted_close: float,
        moving_average: float,
    ) -> str:
        entry_price = self._effective_entry_price(symbol)
        if price > 0:
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, price), price)
        if adjusted_close > 0 and moving_average > 0 and adjusted_close < moving_average * self.exit_buffer:
            return "trend_break"
        if not trend_on:
            return "trend_off"
        if price <= 0 or entry_price <= 0:
            return ""
        if self.stop_loss_pct > 0 and price <= entry_price * (1.0 - self.stop_loss_pct):
            return "stop_loss"
        peak = self._peak_prices.get(symbol, price)
        if (
            self.take_profit_pct > 0
            and self.trailing_stop_pct > 0
            and peak >= entry_price * (1.0 + self.take_profit_pct)
            and price <= peak * (1.0 - self.trailing_stop_pct)
        ):
            return "trailing_take_profit"
        return ""

    def on_fill(self, context: Any, fill: Any) -> None:
        symbol = str(getattr(fill, "symbol", "") or "")
        previous_quantity = float(self._positions.get(symbol, 0) or 0)
        super().on_fill(context, fill)
        if symbol != self.trade_symbol:
            return
        current_quantity = float(self._positions.get(symbol, 0) or 0)
        side = str(getattr(fill, "side", "") or "").upper()
        fill_quantity = float(getattr(fill, "quantity", 0) or 0)
        fill_price = self._fill_price(fill)
        if side == "BUY" and fill_quantity > 0 and fill_price > 0:
            if previous_quantity > 0 and symbol in self._entry_prices:
                total_quantity = previous_quantity + fill_quantity
                self._entry_prices[symbol] = (
                    self._entry_prices[symbol] * previous_quantity + fill_price * fill_quantity
                ) / total_quantity
            else:
                self._entry_prices[symbol] = fill_price
            self._peak_prices[symbol] = max(self._peak_prices.get(symbol, fill_price), fill_price)
        elif side == "SELL" and current_quantity <= 0:
            self._entry_prices.pop(symbol, None)
            self._peak_prices.pop(symbol, None)

    def _effective_entry_price(self, symbol: str) -> float:
        portfolio = getattr(getattr(self, "context", None), "portfolio", None)
        get_position = getattr(portfolio, "get_position", None)
        if callable(get_position):
            try:
                position = get_position(symbol)
            except Exception:
                position = None
            for field in ("avg_cost", "average_cost", "entry_price"):
                value = getattr(position, field, None)
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if number > 0 and math.isfinite(number):
                    return number
        return self._entry_prices.get(symbol, 0.0)

    def _tradable_bar(self, bar: Any) -> bool:
        if self._bool_field(bar, "is_st") or self._bool_field(bar, "status_is_suspended"):
            return False
        if self._bool_field(bar, "_suspended"):
            return False
        if self._field(bar, "tradable", True) is False:
            return False
        if self._field(bar, "is_listed", True) is False:
            return False
        list_status = str(self._field(bar, "list_status", "L") or "L").upper()
        if list_status not in {"L", "上市", "LISTED"}:
            return False
        return self._get_last_price(self.trade_symbol) > 0

    def _portfolio_nav(self, context: Any) -> float:
        return float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self.trade_symbol,
            "ma_window": self.ma_window,
            "target_exposure": self.target_exposure,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "trailing_stop_pct": self.trailing_stop_pct,
            "lot_size": self.lot_size,
            "exit_buffer": self.exit_buffer,
            "formula_key": STRATEGY_ID,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {"last_signal": dict(self._last_signal)}

    @staticmethod
    def _fill_price(fill: Any) -> float:
        for field in ("fill_price", "price", "entry_price"):
            value = getattr(fill, field, 0.0)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0 and math.isfinite(number):
                return number
        return 0.0

    @staticmethod
    def _field(bar: Any, field: str, default: Any = None) -> Any:
        return bar.get(field, default) if isinstance(bar, dict) else getattr(bar, field, default)

    @classmethod
    def _bool_field(cls, bar: Any, field: str) -> bool:
        value = cls._field(bar, field, False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "t", "yes", "y"}
        return bool(value)


def main() -> None:
    args = _parse_args()
    scenarios = _selected_scenarios(args.names)
    symbols = sorted({str(scenario["symbol"]) for scenario in scenarios})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, market_caps = _load_shared_inputs(symbols)
    rows = []
    strict_reports = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} symbol={scenario['symbol']}")
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        cap_meta = market_caps.get(str(scenario["symbol"]), {})
        row = {
            "scenario": scenario["name"],
            "symbol": scenario["symbol"],
            "display_name": scenario.get("display_name", ""),
            "ma_window": scenario["ma_window"],
            "target_exposure": scenario["target_exposure"],
            "stop_loss_pct": scenario["stop_loss_pct"],
            "take_profit_pct": scenario["take_profit_pct"],
            "trailing_stop_pct": scenario["trailing_stop_pct"],
            "latest_trade_date": cap_meta.get("trade_date"),
            "latest_total_mv_wan": cap_meta.get("total_mv"),
            "latest_total_mv_rank": cap_meta.get("rank"),
            "sharpe": metrics.get("sharpe"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "total_trades": metrics.get("total_trades"),
            "round_trip_trades": metrics.get("round_trip_trades"),
            "meets_goal": _meets_goal(metrics),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    best = _select_best(rows)
    report_path, result_path = _write_outputs(rows, strict_reports, best)
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": best,
                "report_path": str(report_path),
                "result_path": str(result_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _load_shared_inputs(
    symbols: List[str],
) -> Tuple[Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
        market_caps = _load_latest_market_caps(db_provider, symbols)
    finally:
        db_provider.disconnect()
    return lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, market_caps


def _load_latest_market_caps(db_provider: DuckDBProvider, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    storage = db_provider.storage
    if not getattr(storage, "_daily_basic_available")():
        return {}
    placeholders = ", ".join("?" for _ in symbols)
    rows = storage.conn.execute(
        f"""
        WITH latest AS (
            SELECT max(trade_date) AS trade_date
            FROM daily_basic.cn_daily_basic
        ),
        ranked AS (
            SELECT
                db.symbol,
                db.trade_date,
                db.total_mv,
                db.circ_mv,
                row_number() OVER (ORDER BY db.total_mv DESC NULLS LAST) AS rank
            FROM daily_basic.cn_daily_basic db
            JOIN latest ON db.trade_date = latest.trade_date
            WHERE db.total_mv IS NOT NULL
        )
        SELECT symbol, trade_date, total_mv, circ_mv, rank
        FROM ranked
        WHERE symbol IN ({placeholders})
        """,
        symbols,
    ).fetchall()
    result = {}
    for row in rows:
        result[str(row[0])] = {
            "trade_date": str(row[1]),
            "total_mv": _finite_or_none(row[2]),
            "circ_mv": _finite_or_none(row[3]),
            "rank": int(row[4]) if row[4] is not None else None,
        }
    return result


def _run_one(
    scenario: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    symbol = str(scenario["symbol"])
    symbols = [symbol]
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=True,
        include_execution_liquidity_features=True,
    )
    strategy = LargeCapSingleStockTrendStopStrategy(
        symbol=symbol,
        ma_window=int(scenario["ma_window"]),
        target_exposure=float(scenario["target_exposure"]),
        stop_loss_pct=float(scenario["stop_loss_pct"]),
        take_profit_pct=float(scenario["take_profit_pct"]),
        trailing_stop_pct=float(scenario["trailing_stop_pct"]),
    )
    backtest_config = {"slippage_bps": 5, "execution_cost_model": dict(EXECUTION_COST_MODEL)}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
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
            start=START,
            end=END,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(START, END, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
        START,
        END,
        INITIAL_CASH,
        symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _meets_goal(metrics: Dict[str, Any]) -> bool:
    return float(metrics.get("cagr") or 0.0) > 0.10 and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30


def _select_best(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    viable = [row for row in rows if row["meets_goal"]]
    candidates = viable or rows
    return max(
        candidates,
        key=lambda row: (
            float(row.get("cagr") or 0.0) / max(abs(float(row.get("max_drawdown_pct") or 0.0)), 1e-9),
            float(row.get("sharpe") or 0.0),
        ),
    )


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result_path = strategy_dir / "grid_result.json"
    result_path.write_text(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "start": START.date().isoformat(),
                "end": END.date().isoformat(),
                "initial_cash": INITIAL_CASH,
                "rows": rows,
                "best": best,
                "strict_reports": strict_reports,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    row = _hypothesis_row(best, strict_reports[str(best["scenario"])])
    result = {"run_id": f"{STRATEGY_ID}_strict_grid", "backtested": len(rows), "rejected": 0, "errors": []}
    html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=datetime.now(timezone.utc).isoformat())
    html = _insert_detail_section(html, rows)
    report_path = strategy_dir / "strict_backtest_report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path, result_path


def _hypothesis_row(best: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    verdict = "pass" if _meets_goal(metrics) else "warn"
    return {
        "strategy_id": STRATEGY_ID,
        "title": f"{TITLE} - {best['scenario']}",
        "status": "needs_walkforward_validation" if verdict == "pass" else "needs_more_research",
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "strict_backtest": {
                    "label": "严格回测",
                    "verdict": verdict,
                    "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
                    "method": "项目 Backtester；T+1、真实股票佣金税费、100 股手数、5bps 基础滑点、cn_daily_liquidity_impact 冲击成本。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "scenario": best["scenario"],
                "symbol": best["symbol"],
                "display_name": best.get("display_name"),
                "ma_window": best["ma_window"],
                "target_exposure": best["target_exposure"],
                "stop_loss_pct": best["stop_loss_pct"],
                "take_profit_pct": best["take_profit_pct"],
                "trailing_stop_pct": best["trailing_stop_pct"],
                "latest_total_mv_wan": best.get("latest_total_mv_wan"),
                "latest_total_mv_rank": best.get("latest_total_mv_rank"),
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30},
            }
        },
    }


def _insert_detail_section(html: str, rows: List[Dict[str, Any]]) -> str:
    grid_rows = "\n".join(
        "<tr>"
        f"<td>{row['scenario']}</td>"
        f"<td>{row['symbol']} {row.get('display_name', '')}</td>"
        f"<td>{int(row['ma_window'])}</td>"
        f"<td>{float(row['target_exposure']):.0%}</td>"
        f"<td>{float(row.get('latest_total_mv_wan') or 0.0):,.0f}</td>"
        f"<td>{row.get('latest_total_mv_rank') or ''}</td>"
        f"<td>{float(row.get('cagr') or 0.0):.2%}</td>"
        f"<td>{float(row.get('max_drawdown_pct') or 0.0):.2%}</td>"
        f"<td>{float(row.get('sharpe') or 0.0):.2f}</td>"
        f"<td>{int(row.get('total_trades') or 0)}</td>"
        f"<td>{'通过' if row['meets_goal'] else '未通过'}</td>"
        "</tr>"
        for row in rows
    )
    grid = (
        "<h3>目标网格结果</h3><div class=\"table-wrap\"><table>"
        "<thead><tr><th>场景</th><th>标的</th><th>均线</th><th>仓位</th><th>最新 total_mv(万元)</th><th>市值排名</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Trades</th><th>目标</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
    )
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}{grid}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--names", nargs="*", default=None, help="Scenario names to run. Defaults to all scenarios.")
    return parser.parse_args()


def _selected_scenarios(names: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not names:
        return list(SCENARIOS)
    wanted = set(names)
    selected = [scenario for scenario in SCENARIOS if scenario["name"] in wanted]
    missing = sorted(wanted - {scenario["name"] for scenario in selected})
    if missing:
        raise SystemExit(f"Unknown scenario names: {', '.join(missing)}")
    return selected


if __name__ == "__main__":
    main()
