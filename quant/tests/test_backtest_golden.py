import json
import os
from datetime import datetime

import pandas as pd
import pytest

from quant.features.backtest.engine import Backtester
from quant.features.backtest.data_provider import DataFrameProvider
from quant.features.strategies.dual_ma_crossover.strategy import DualMACrossover
from quant.tests.conftest import make_backtester, make_us_bars

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")

US_CONFIG = {
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999,
             "max_orders_minute": 999, "max_sector_pct": 1.0},
}


def _read_golden(filename):
    path = os.path.join(GOLDEN_DIR, filename)
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, index_col=0, parse_dates=True)


def _write_golden(filename, df):
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    path = os.path.join(GOLDEN_DIR, filename)
    df.to_csv(path)


def _build_synthetic_data():
    import hashlib

    def _det_hash(s, max_val):
        return int(hashlib.md5(s.encode()).hexdigest(), 16) % max_val

    symbols = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
    start = datetime(2024, 1, 2)
    n_days = 252

    start_prices = {"AAPL": 185.0, "GOOGL": 140.0, "MSFT": 375.0, "AMZN": 150.0, "TSLA": 250.0}
    daily_drift = {"AAPL": 0.0005, "GOOGL": 0.0003, "MSFT": 0.0006, "AMZN": 0.0004, "TSLA": 0.0001}

    base = make_us_bars(symbols, start, n_days, start_prices=start_prices)

    new_rows = []
    for sym in symbols:
        sym_data = base[base["symbol"] == sym].copy()
        cumret = 1.0
        for i, (idx, row) in enumerate(sym_data.iterrows()):
            drift = daily_drift.get(sym, 0.0003)
            cumret *= (1.0 + drift)
            row_dict = row.to_dict()
            row_dict["close"] = round(start_prices[sym] * cumret, 4)
            noise = _det_hash(f"{sym}_{i}", 10) / 1000
            row_dict["open"] = round(row_dict["close"] * (1.0 - noise), 4)
            row_dict["high"] = round(row_dict["close"] * 1.005, 4)
            row_dict["low"] = round(row_dict["close"] * 0.995, 4)
            row_dict["adj_open"] = row_dict["open"]
            row_dict["adj_high"] = row_dict["high"]
            row_dict["adj_low"] = row_dict["low"]
            row_dict["adj_close"] = row_dict["close"]
            row_dict["adj_factor"] = 1.0
            new_rows.append(row_dict)

    return pd.DataFrame(new_rows)


GOLDEN_EQUITY = "golden_dual_ma_equity.csv"
GOLDEN_TRADES = "golden_dual_ma_trades.json"


@pytest.fixture(scope="module")
def golden_dual_ma_run():
    data = _build_synthetic_data()
    bt = make_backtester(US_CONFIG)
    provider = DataFrameProvider(data)
    symbols = sorted(data["symbol"].unique().tolist())
    strat = DualMACrossover(fast_period=5, slow_period=20, max_position_pct=0.95)
    return bt.run(
        start=data["timestamp"].min(), end=data["timestamp"].max(),
        strategies=[strat], initial_cash=1_000_000,
        data_provider=provider, symbols=symbols,
    )


class TestGoldenDualMACrossover:
    def test_equity_curve_matches(self, golden_dual_ma_run):
        result = golden_dual_ma_run
        equity = result.equity_curve

        existing = _read_golden(GOLDEN_EQUITY)
        if existing is None:
            _write_golden(GOLDEN_EQUITY, equity.to_frame("nav"))
            pytest.skip("Golden file created — re-run to verify")

        assert len(equity) == len(existing), (
            f"Equity curve length changed: {len(equity)} vs {len(existing)}"
        )
        for i in range(len(equity)):
            assert equity.iloc[i] == pytest.approx(existing.iloc[i, 0], rel=1e-6), (
                f"Equity curve diverges at index {i}: {equity.iloc[i]} vs {existing.iloc[i, 0]}"
            )

    def test_trade_count_matches(self, golden_dual_ma_run):
        result = golden_dual_ma_run
        trade_summary = {
            "count": len(result.trades),
            "buy_count": len([t for t in result.trades if t.side == "BUY"]),
            "sell_count": len([t for t in result.trades if t.side == "SELL"]),
            "total_commission": result.diagnostics.total_commission,
            "total_gross_pnl": result.diagnostics.total_gross_pnl,
            "final_nav": result.final_nav,
        }

        existing = _read_golden(GOLDEN_TRADES)
        if existing is None:
            _write_golden(GOLDEN_TRADES, pd.DataFrame([trade_summary]))
            pytest.skip("Golden trade summary created — re-run to verify")
            return

        expected = existing.iloc[0].to_dict()
        assert trade_summary["count"] == int(expected["count"]), (
            f"Trade count: {trade_summary['count']} vs {expected['count']}"
        )
        assert trade_summary["final_nav"] == pytest.approx(float(expected["final_nav"]), rel=1e-4), (
            f"Final NAV: {trade_summary['final_nav']} vs {expected['final_nav']}"
        )

    def test_invariants_hold(self, golden_dual_ma_run):
        result = golden_dual_ma_run
        from quant.tests.test_backtest_fuzz import _verify_all_invariants
        _verify_all_invariants(result, initial_cash=1_000_000)
