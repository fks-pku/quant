"""Run strict backtests for large-cap ETF trend timing candidates."""

from __future__ import annotations

import json
import math
import sys
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
    _strict_execution_cost_model,
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
INITIAL_CASH = 500000.0
STRATEGY_ID = "large_cap_etf_trend_timing"
TITLE = "大市值 ETF 趋势切现金"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}


SCENARIOS: List[Dict[str, Any]] = [
    {"name": "hs300_ma60", "risk_symbol": "510300", "cash_symbol": "511880", "ma_window": 60},
    {"name": "hs300_ma120", "risk_symbol": "510300", "cash_symbol": "511880", "ma_window": 120},
    {"name": "a50_ma60", "risk_symbol": "510050", "cash_symbol": "511880", "ma_window": 60},
    {"name": "dividend_ma60", "risk_symbol": "510880", "cash_symbol": "511880", "ma_window": 60},
    {"name": "csi500_ma60", "risk_symbol": "510500", "cash_symbol": "511880", "ma_window": 60},
    {"name": "创业板_ma60", "risk_symbol": "159915", "cash_symbol": "511880", "ma_window": 60},
    {"name": "nasdaq100_ma60", "risk_symbol": "513100", "cash_symbol": "511880", "ma_window": 60},
    {"name": "hsi_ma60", "risk_symbol": "159920", "cash_symbol": "511880", "ma_window": 60},
]


DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>本次约束</th></tr></thead><tbody>
<tr><td>1. 大市值 ETF 标的</td><td>只测试沪深 300、上证 50、红利、创业板、纳指 100 等高流动性大盘/大市值指数 ETF；防守腿为货币 ETF。</td><td>风险腿与现金腿均为交易所基金，使用 CN fund 佣金路径。</td></tr>
<tr><td>2. 趋势开关</td><td>收盘后计算风险 ETF 的后复权收盘价是否高于 N 日均线；高于均线则持有风险 ETF，否则持有现金 ETF。</td><td>N=60/120；只用当日及历史收盘，订单 T+1 执行。</td></tr>
<tr><td>3. 仓位构建</td><td>每次只持有一个 ETF，目标仓位 98%，按 100 份手数向下取整；状态改变时先卖出旧 ETF，再买入新 ETF。</td><td>不使用杠杆，不做未来成分股或未来财务字段。</td></tr>
<tr><td>4. 严格执行</td><td>项目 Backtester 执行涨跌停、停牌、手数、T+1、真实基金佣金、5bps 基础滑点和 ETF 专用冲击成本。</td><td>目标：CAGR &gt; 10%，MaxDD 不超过 30%。</td></tr>
</tbody></table></div>
"""


class LargeCapEtfTrendTimingStrategy(DailyBarStrategy):
    def __init__(
        self,
        risk_symbol: str,
        cash_symbol: str = "511880",
        ma_window: int = 60,
        target_exposure: float = 0.98,
        lot_size: int = 100,
    ):
        self.risk_symbol = str(risk_symbol)
        self.cash_symbol = str(cash_symbol)
        self.ma_window = max(5, int(ma_window))
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.lot_size = max(1, int(lot_size))
        self._last_target = ""
        super().__init__(STRATEGY_ID, list(dict.fromkeys([self.risk_symbol, self.cash_symbol])), holding_days=1)

    @property
    def _max_keep_hint(self) -> int:
        return self.ma_window + 5

    def _execute_rebalance(self, context: Any, trading_date) -> None:
        target = self._select_target()
        if not target:
            return
        if self._positions.get(target, 0) > 0 and all(
            quantity <= 0 or symbol == target
            for symbol, quantity in self._positions.items()
        ):
            self._last_target = target
            return
        for symbol, quantity in list(self._positions.items()):
            if quantity > 0 and symbol != target:
                price = self._get_last_price(symbol)
                self.sell(symbol, int(quantity), "MARKET", price if price > 0 else None)
        target_price = self._get_last_price(target)
        if target_price <= 0:
            return
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if nav <= 0:
            return
        target_quantity = self._round_lot(nav * self.target_exposure / target_price)
        current_quantity = int(self._positions.get(target, 0) or 0)
        if current_quantity <= 0 and target_quantity > 0:
            self.buy(target, target_quantity, "MARKET", target_price)
        self._last_target = target

    def _select_target(self) -> str:
        closes = [price for price in self._get_closes(self.risk_symbol) if price > 0 and math.isfinite(price)]
        if len(closes) < self.ma_window:
            return self.cash_symbol
        moving_average = sum(closes[-self.ma_window :]) / float(self.ma_window)
        return self.risk_symbol if closes[-1] > moving_average else self.cash_symbol

    def _round_lot(self, quantity: float) -> int:
        return int(quantity // self.lot_size) * self.lot_size

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "risk_symbol": self.risk_symbol,
            "cash_symbol": self.cash_symbol,
            "ma_window": self.ma_window,
            "target_exposure": self.target_exposure,
            "lot_size": self.lot_size,
            "formula_key": STRATEGY_ID,
        }

    def _get_state_fields(self) -> Dict[str, Any]:
        return {"last_target": self._last_target}


def main() -> None:
    all_symbols = sorted({symbol for scenario in SCENARIOS for symbol in (scenario["risk_symbol"], scenario["cash_symbol"])})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows = []
    strict_reports = {}
    for scenario in SCENARIOS:
        print(f"Running {scenario['name']} risk={scenario['risk_symbol']} cash={scenario['cash_symbol']}")
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        row = {
            "scenario": scenario["name"],
            "risk_symbol": scenario["risk_symbol"],
            "cash_symbol": scenario["cash_symbol"],
            "ma_window": scenario["ma_window"],
            "sharpe": metrics.get("sharpe"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "total_trades": metrics.get("total_trades"),
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


def _load_shared_inputs(symbols: List[str]) -> Tuple[Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _run_one(
    scenario: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    symbols = [scenario["risk_symbol"], scenario["cash_symbol"]]
    execution_cost_model = _strict_execution_cost_model(
        STRATEGY_ID,
        {
            "name": TITLE,
            "description": "large-cap ETF trend timing with cash ETF defensive leg",
            "parameters": {"symbols": symbols, "cash_symbol": scenario["cash_symbol"]},
        },
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        START,
        END,
        include_daily_basic=False,
        include_execution_liquidity_features=True,
    )
    strategy = LargeCapEtfTrendTimingStrategy(
        risk_symbol=str(scenario["risk_symbol"]),
        cash_symbol=str(scenario["cash_symbol"]),
        ma_window=int(scenario["ma_window"]),
    )
    backtest_config = {"slippage_bps": 5, "execution_cost_model": execution_cost_model}
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
                    "method": "项目 Backtester；T+1、ETF/基金佣金、100 份手数、5bps 基础滑点、cn_etf_liquidity_impact 冲击成本。",
                }
            },
        },
        "evidence": {
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "scenario": best["scenario"],
                "risk_symbol": best["risk_symbol"],
                "cash_symbol": best["cash_symbol"],
                "ma_window": best["ma_window"],
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30},
            }
        },
    }


def _insert_detail_section(html: str, rows: List[Dict[str, Any]]) -> str:
    grid_rows = "\n".join(
        "<tr>"
        f"<td>{row['scenario']}</td>"
        f"<td>{row['risk_symbol']}</td>"
        f"<td>{int(row['ma_window'])}</td>"
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
        "<thead><tr><th>场景</th><th>风险腿</th><th>均线</th><th>CAGR</th><th>MaxDD</th><th>Sharpe</th><th>Trades</th><th>目标</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
    )
    marker = "<h2>2. 严格回测证据</h2>\n<h3>回测 Equity Curve</h3>"
    replacement = f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}{grid}<h3>回测 Equity Curve</h3>"
    return html.replace(marker, replacement, 1)


if __name__ == "__main__":
    main()
