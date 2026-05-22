"""Audit fixed large-cap single-stock timing rules across top A-share market caps."""

from __future__ import annotations

import json
import math
import sys
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

_project_root = Path(__file__).resolve().parents[2]
_script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_script_dir))

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
from run_large_cap_single_stock_trend_stop_strict_backtest import (
    COMMISSION_CFG,
    END,
    EXECUTION_COST_MODEL,
    INITIAL_CASH,
    LargeCapSingleStockTrendStopStrategy,
    REPORT_ROOT,
    START,
)


STRATEGY_ID = "large_cap_single_stock_timing_robustness"
REPORT_DIR = REPORT_ROOT / "large_cap_single_stock_trend_stop"
DEFAULT_REFERENCE_SYMBOL = "000858"
FIXED_RULE = {
    "ma_window": 60,
    "target_exposure": 0.70,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.0,
    "trailing_stop_pct": 0.0,
}


class BuyAndHoldStrategy(DailyBarStrategy):
    def __init__(self, symbol: str, target_exposure: float):
        self.trade_symbol = str(symbol)
        self.target_exposure = min(max(float(target_exposure), 0.0), 1.0)
        self.max_position_pct = self.target_exposure
        self.max_positions = 1
        super().__init__(f"buy_and_hold_{self.trade_symbol}_{int(self.target_exposure * 100)}", [self.trade_symbol])

    @property
    def _max_keep_hint(self) -> int:
        return 5

    def _execute_rebalance(self, context: Any, trading_date) -> None:
        if self._positions.get(self.trade_symbol, 0) > 0:
            return
        price = self._get_last_price(self.trade_symbol)
        nav = float(getattr(getattr(context, "portfolio", None), "nav", 0.0) or 0.0)
        if price <= 0 or nav <= 0:
            return
        quantity = int((nav * self.target_exposure / price) // 100) * 100
        if quantity > 0:
            self.buy(self.trade_symbol, quantity, "MARKET", price)

    def _get_parameters(self) -> Dict[str, Any]:
        return {
            "symbol": self.trade_symbol,
            "target_exposure": self.target_exposure,
            "formula_key": self.name,
        }


def main() -> None:
    args = _parse_args()
    symbols = _load_top_market_cap_symbols(args.limit, args.reference_symbol)
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, market_caps = _load_shared_inputs(symbols)
    rows = []
    for index, symbol in enumerate(symbols, start=1):
        print(f"[{index}/{len(symbols)}] Running fixed timing rule for {symbol}")
        timing = _run_timing(symbol, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        hold = _run_buy_hold(symbol, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        row = _row(symbol, timing, hold, market_caps.get(symbol, {}), args.reference_symbol)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    summary = _summary(rows, args.limit, args.reference_symbol)
    json_path, html_path = _write_outputs(rows, summary)
    print(json.dumps({"summary": summary, "json_path": str(json_path), "html_path": str(html_path)}, ensure_ascii=False, indent=2))


def _load_top_market_cap_symbols(limit: int, reference_symbol: str) -> List[str]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        storage = db_provider.storage
        if not getattr(storage, "_daily_basic_available")():
            raise RuntimeError("daily_basic sidecar unavailable")
        rows = storage.conn.execute(
            """
            WITH latest AS (
                SELECT max(trade_date) AS trade_date
                FROM daily_basic.cn_daily_basic
            ),
            ranked AS (
                SELECT
                    db.symbol,
                    db.total_mv,
                    row_number() OVER (ORDER BY db.total_mv DESC NULLS LAST) AS rank
                FROM daily_basic.cn_daily_basic db
                JOIN latest ON db.trade_date = latest.trade_date
                WHERE db.total_mv IS NOT NULL
                  AND regexp_matches(db.symbol, '^[0236][0-9]{5}$')
                  AND NOT starts_with(db.symbol, '200')
            )
            SELECT symbol
            FROM ranked
            WHERE rank <= ?
            ORDER BY rank
            """,
            [int(limit)],
        ).fetchall()
    finally:
        db_provider.disconnect()
    symbols = [str(row[0]) for row in rows]
    if reference_symbol and reference_symbol not in symbols:
        symbols.append(reference_symbol)
    return symbols


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
                row_number() OVER (ORDER BY db.total_mv DESC NULLS LAST) AS rank
            FROM daily_basic.cn_daily_basic db
            JOIN latest ON db.trade_date = latest.trade_date
            WHERE db.total_mv IS NOT NULL
        )
        SELECT symbol, trade_date, total_mv, rank
        FROM ranked
        WHERE symbol IN ({placeholders})
        """,
        symbols,
    ).fetchall()
    return {
        str(row[0]): {
            "trade_date": str(row[1]),
            "total_mv": _finite_or_none(row[2]),
            "rank": int(row[3]) if row[3] is not None else None,
        }
        for row in rows
    }


def _run_timing(
    symbol: str,
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    strategy = LargeCapSingleStockTrendStopStrategy(symbol=symbol, **FIXED_RULE)
    return _run_strategy(symbol, strategy, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)


def _run_buy_hold(
    symbol: str,
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    strategy = BuyAndHoldStrategy(symbol, float(FIXED_RULE["target_exposure"]))
    return _run_strategy(symbol, strategy, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)


def _run_strategy(
    symbol: str,
    strategy: DailyBarStrategy,
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    data_provider = _DuckDBDailyDateProvider(
        [symbol],
        START,
        END,
        include_daily_basic=True,
        include_execution_liquidity_features=True,
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
        result = backtester.run(
            start=START,
            end=END,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=[symbol],
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(START, END, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        result,
        START,
        END,
        INITIAL_CASH,
        [symbol],
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _row(
    symbol: str,
    timing: Dict[str, Any],
    hold: Dict[str, Any],
    cap_meta: Dict[str, Any],
    reference_symbol: str,
) -> Dict[str, Any]:
    timing_metrics = timing.get("metrics") or {}
    hold_metrics = hold.get("metrics") or {}
    cagr = float(timing_metrics.get("cagr") or 0.0)
    hold_cagr = float(hold_metrics.get("cagr") or 0.0)
    max_dd = float(timing_metrics.get("max_drawdown_pct") or 0.0)
    hold_max_dd = float(hold_metrics.get("max_drawdown_pct") or 0.0)
    return {
        "symbol": symbol,
        "is_reference": symbol == reference_symbol,
        "latest_trade_date": cap_meta.get("trade_date"),
        "latest_total_mv_wan": cap_meta.get("total_mv"),
        "latest_total_mv_rank": cap_meta.get("rank"),
        "timing_cagr": cagr,
        "timing_total_return": float(timing_metrics.get("total_return") or 0.0),
        "timing_max_drawdown_pct": max_dd,
        "timing_sharpe": float(timing_metrics.get("sharpe") or 0.0),
        "timing_total_trades": int(timing_metrics.get("total_trades") or 0),
        "hold_cagr": hold_cagr,
        "hold_total_return": float(hold_metrics.get("total_return") or 0.0),
        "hold_max_drawdown_pct": hold_max_dd,
        "hold_sharpe": float(hold_metrics.get("sharpe") or 0.0),
        "hold_total_trades": int(hold_metrics.get("total_trades") or 0),
        "cagr_delta": cagr - hold_cagr,
        "max_drawdown_improvement": max_dd - hold_max_dd,
        "beats_hold_cagr": cagr > hold_cagr,
        "improves_hold_drawdown": max_dd > hold_max_dd,
        "meets_goal": cagr > 0.10 and max_dd >= -0.30,
    }


def _summary(rows: List[Dict[str, Any]], limit: int, reference_symbol: str) -> Dict[str, Any]:
    tested_rows = [row for row in rows if row["symbol"] != reference_symbol]
    cagr_values = [float(row["timing_cagr"]) for row in tested_rows]
    dd_values = [float(row["timing_max_drawdown_pct"]) for row in tested_rows]
    return {
        "strategy_id": STRATEGY_ID,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "top_market_cap_limit": int(limit),
        "reference_symbol": reference_symbol,
        "fixed_rule": dict(FIXED_RULE),
        "tested_other_symbols": len(tested_rows),
        "reference_included": any(row["symbol"] == reference_symbol for row in rows),
        "meets_goal_count": sum(1 for row in tested_rows if row["meets_goal"]),
        "beats_hold_cagr_count": sum(1 for row in tested_rows if row["beats_hold_cagr"]),
        "improves_hold_drawdown_count": sum(1 for row in tested_rows if row["improves_hold_drawdown"]),
        "median_timing_cagr": median(cagr_values) if cagr_values else 0.0,
        "median_timing_max_drawdown_pct": median(dd_values) if dd_values else 0.0,
        "best_by_goal_score": _best_row(tested_rows),
    }


def _best_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    return max(
        rows,
        key=lambda row: (
            row["meets_goal"],
            float(row["timing_cagr"]) / max(abs(float(row["timing_max_drawdown_pct"])), 1e-9),
            float(row["timing_sharpe"]),
        ),
    )


def _write_outputs(rows: List[Dict[str, Any]], summary: Dict[str, Any]) -> Tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"robustness_fixed_rule_top{summary['top_market_cap_limit']}.json"
    html_path = REPORT_DIR / f"robustness_fixed_rule_top{summary['top_market_cap_limit']}.html"
    payload = {"summary": summary, "rows": rows}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path.write_text(_render_html(rows, summary), encoding="utf-8")
    return json_path, html_path


def _render_html(rows: List[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    body = "\n".join(
        "<tr>"
        f"<td>{row['symbol']}{' *' if row['is_reference'] else ''}</td>"
        f"<td>{row.get('latest_total_mv_rank') or ''}</td>"
        f"<td>{float(row.get('latest_total_mv_wan') or 0.0):,.0f}</td>"
        f"<td>{float(row['timing_cagr']):.2%}</td>"
        f"<td>{float(row['timing_max_drawdown_pct']):.2%}</td>"
        f"<td>{float(row['timing_sharpe']):.2f}</td>"
        f"<td>{int(row['timing_total_trades'])}</td>"
        f"<td>{float(row['hold_cagr']):.2%}</td>"
        f"<td>{float(row['hold_max_drawdown_pct']):.2%}</td>"
        f"<td>{float(row['cagr_delta']):.2%}</td>"
        f"<td>{float(row['max_drawdown_improvement']):.2%}</td>"
        f"<td>{'通过' if row['meets_goal'] else '未通过'}</td>"
        "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>大市值固定择时规则稳健性审计</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:32px;color:#18212f;background:#f7f8fa}}
main{{max-width:1280px;margin:auto;background:white;padding:24px;border:1px solid #d8dee8}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #d8dee8;padding:7px 8px;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
th{{background:#eef2f7}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}}
.card{{border:1px solid #d8dee8;padding:12px;background:#fbfcfe}}
.label{{font-size:12px;color:#647084}}
.value{{font-size:20px;font-weight:700;margin-top:4px}}
</style>
</head>
<body><main>
<h1>大市值固定择时规则稳健性审计</h1>
<p>规则固定为 MA60 + 5% 止损 + 70% 仓位；不按单个股票重新调参。* 为参考标的五粮液，不计入 other symbols 统计。</p>
<div class="summary">
<div class="card"><div class="label">其它大市值样本</div><div class="value">{summary['tested_other_symbols']}</div></div>
<div class="card"><div class="label">达到 CAGR&gt;10% 且 MaxDD&gt;=-30%</div><div class="value">{summary['meets_goal_count']}</div></div>
<div class="card"><div class="label">CAGR 跑赢同仓位持有</div><div class="value">{summary['beats_hold_cagr_count']}</div></div>
<div class="card"><div class="label">回撤优于同仓位持有</div><div class="value">{summary['improves_hold_drawdown_count']}</div></div>
</div>
<table>
<thead><tr><th>Symbol</th><th>市值排名</th><th>total_mv(万元)</th><th>择时 CAGR</th><th>择时 MaxDD</th><th>择时 Sharpe</th><th>择时 Trades</th><th>持有 CAGR</th><th>持有 MaxDD</th><th>CAGR 差</th><th>回撤改善</th><th>目标</th></tr></thead>
<tbody>{body}</tbody>
</table>
</main></body></html>
"""


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30, help="Latest market-cap rank limit for the cross-symbol audit.")
    parser.add_argument("--reference-symbol", default=DEFAULT_REFERENCE_SYMBOL, help="Reference symbol to append if outside top-N.")
    return parser.parse_args()


if __name__ == "__main__":
    main()
