"""Demo: backtest Tencent (HK.00700) using the unified Futu->DuckDB pipeline.

Usage:
    python scripts/futu_backtest_demo.py              # use DuckDB data
    python scripts/futu_backtest_demo.py --download   # force re-fetch from Futu

Requires: Futu OpenD running at 127.0.0.1:11111
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from datetime import datetime, timedelta, date as date_type
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.core.backtester import Backtester, BacktestResultExporter
from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.strategies.base import Strategy
from quant.strategies.registry import strategy as reg_strategy
from quant.core.walkforward import DataFrameProvider

SYMBOL = "HK.00700"
DB_PATH = str(Path(os.path.join(os.path.dirname(__file__), "..", "data", "duckdb", "quant.duckdb")))
OUTPUT_DIR = Path(os.path.join(os.path.dirname(__file__), "..", "data"))


def ensure_data(force=False):
    """Ensure DuckDB has HK.00700 data. If not, fetch via prepare_data pipeline."""
    provider = DuckDBProvider(db_path=DB_PATH)
    provider.connect()
    existing = provider.get_available_range(SYMBOL, "1d")
    provider.disconnect()

    if existing and not force:
        print(f"Data exists: {existing['start'].date()} ~ {existing['end'].date()}")
        return

    print("Fetching data from Futu -> DuckDB...")
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from prepare_data import prepare
    end = datetime.now()
    start = end - timedelta(days=365 * 10)
    prepare([SYMBOL], start, end, "1d", DB_PATH, force=True)


@reg_strategy("TencentMomentum")
class TencentMomentum(Strategy):
    """SMA crossover momentum strategy for HK.00700."""

    def __init__(
        self,
        symbol: str = SYMBOL,
        fast_period: int = 20,
        slow_period: int = 60,
        position_pct: float = 0.95,
        atr_period: int = 14,
    ):
        super().__init__("TencentMomentum")
        self._symbol = symbol
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_pct = position_pct
        self.atr_period = atr_period
        self._bars: List[Dict] = []
        self._has_position = False

    @property
    def symbols(self) -> List[str]:
        return [self._symbol]

    def on_start(self, context) -> None:
        super().on_start(context)

    def on_data(self, context, data: Any) -> None:
        if isinstance(data, dict):
            symbol = data.get("symbol", "")
        else:
            return
        if symbol != self._symbol:
            return
        self._bars.append(data)

    def _compute_sma(self, period: int) -> Optional[float]:
        if len(self._bars) < period:
            return None
        closes = [b.get("close", 0) for b in self._bars[-period:]]
        return sum(closes) / len(closes)

    def _get_last_price(self) -> float:
        if self._bars:
            return float(self._bars[-1].get("close", 0))
        return 0.0

    def on_after_trading(self, context, trading_date: date_type) -> None:
        fast_sma = self._compute_sma(self.fast_period)
        slow_sma = self._compute_sma(self.slow_period)

        if fast_sma is None or slow_sma is None:
            return

        price = self._get_last_price()
        if price <= 0:
            return

        if fast_sma > slow_sma and not self._has_position:
            nav = context.portfolio.nav
            quantity = int((nav * self.position_pct) / price)
            if quantity > 0:
                self.buy(self._symbol, quantity)
                self._has_position = True
                self.logger.info(
                    f"GOLDEN CROSS BUY: fast={fast_sma:.2f} > slow={slow_sma:.2f}, "
                    f"qty={quantity}, price={price:.2f}"
                )
        elif fast_sma < slow_sma and self._has_position:
            pos = context.portfolio.get_position(self._symbol)
            if pos and pos.quantity > 0:
                self.sell(self._symbol, int(pos.quantity))
                self._has_position = False
                self.logger.info(
                    f"DEATH CROSS SELL: fast={fast_sma:.2f} < slow={slow_sma:.2f}, "
                    f"qty={int(pos.quantity)}, price={price:.2f}"
                )

    def on_fill(self, context, fill: Any) -> None:
        super().on_fill(context, fill)

    def on_stop(self, context) -> None:
        pos = context.portfolio.get_position(self._symbol)
        if pos and pos.quantity > 0:
            price = self._get_last_price()
            self.sell(self._symbol, int(pos.quantity), "MARKET", price if price > 0 else None)
            self._has_position = False
        self._bars.clear()


def run_backtest(df):
    print("\n" + "=" * 60)
    print("RUNNING BACKTEST: SMA Crossover on HK.00700")
    print("=" * 60)

    strategy = TencentMomentum(
        symbol=SYMBOL,
        fast_period=20,
        slow_period=60,
        position_pct=0.95,
    )

    initial_cash = 1000000.0

    config = {
        "backtest": {"slippage_bps": 10},
        "execution": {
            "commission": {
                "HK": {"type": "percent", "percent": 0.001, "min_per_order": 2.0}
            }
        },
        "data": {"default_timeframe": "1d"},
    }

    provider = DataFrameProvider(df)
    backtester = Backtester(config)

    start = df["timestamp"].min()
    end = df["timestamp"].max()

    print(f"Period: {start.date()} ~ {end.date()}")
    print(f"Initial cash: HK${initial_cash:,.0f}")
    print(f"Strategy: SMA Crossover (fast=20, slow=60)")
    print()

    result = backtester.run(
        start=start,
        end=end,
        strategies=[strategy],
        initial_cash=initial_cash,
        data_provider=provider,
        symbols=[SYMBOL],
    )

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Final NAV:        HK${result.final_nav:>14,.2f}")
    print(f"  Total Return:     {result.total_return:>14.2%}")
    print(f"  Sharpe Ratio:     {result.sharpe_ratio:>14.4f}")
    print(f"  Sortino Ratio:    {result.sortino_ratio:>14.4f}")
    print(f"  Max Drawdown:     {result.max_drawdown_pct:>14.2%}")
    print(f"  Win Rate:         {result.win_rate:>14.2%}")
    print(f"  Profit Factor:    {result.profit_factor:>14.4f}")
    print(f"  Avg Trade Dur:    {result.avg_trade_duration}")
    print(f"  Total Trades:     {len(result.trades):>14d}")
    print("=" * 60)

    if result.trades:
        print("\nRecent trades (last 10):")
        for t in result.trades[-10:]:
            pnl_str = f"+{t.pnl:,.2f}" if t.pnl >= 0 else f"{t.pnl:,.2f}"
            print(f"  {t.entry_time.date()} -> {t.exit_time.date()} | "
                  f"Entry: {t.entry_price:.2f} Exit: {t.exit_price:.2f} | "
                  f"PnL: HK${pnl_str}")

    output_prefix = str(OUTPUT_DIR / "HK_00700_backtest")
    BacktestResultExporter.to_csv(result, output_prefix)
    print(f"\nResults exported to {output_prefix}_*.csv")

    equity_path = OUTPUT_DIR / "HK_00700_equity.csv"
    result.equity_curve.to_csv(str(equity_path))
    print(f"Equity curve saved to {equity_path}")

    buy_hold_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    print(f"\nBenchmark: Buy & Hold return = {buy_hold_return:.2%}")

    return result


def main():
    force_download = "--download" in sys.argv

    print("=" * 60)
    print("STEP 1: ENSURE DATA IN DUCKDB")
    print("=" * 60)
    ensure_data(force=force_download)

    provider = DuckDBProvider(db_path=DB_PATH)
    provider.connect()

    end = datetime.now()
    start = end - timedelta(days=365 * 10)
    df = provider.get_bars(SYMBOL, start, end, "1d")
    provider.disconnect()

    if df.empty:
        print("ERROR: No data for HK.00700 in DuckDB")
        sys.exit(1)

    print(f"Loaded {len(df)} bars: {df['timestamp'].min().date()} ~ {df['timestamp'].max().date()}")
    print(f"Close range: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

    run_backtest(df)


if __name__ == "__main__":
    main()
