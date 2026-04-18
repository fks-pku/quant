"""
Download 10 years of Tencent (HK.00700) daily K-line data from Futu OpenAPI,
save locally as parquet, then run a SimpleMomentum backtest.

Usage:
    python scripts/futu_backtest_demo.py

Requires: Futu OpenD running at 127.0.0.1:11111
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "system"))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(os.path.join(os.path.dirname(__file__), "..", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_PATH = DATA_DIR / "HK_00700_10y_daily.parquet"
CSV_PATH = DATA_DIR / "HK_00700_10y_daily.csv"
SYMBOL = "HK.00700"


def download_data():
    from futu import OpenQuoteContext

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * 10)

    print(f"Connecting to Futu OpenD at 127.0.0.1:11111...")
    quote_ctx = OpenQuoteContext(host="127.0.0.1", port=11111)

    all_data = []
    chunk_start = start_date
    chunk_size = timedelta(days=365)

    print(f"Downloading {SYMBOL} daily K-line data from {start_date.date()} to {end_date.date()}...")

    while chunk_start < end_date:
        chunk_end = min(chunk_start + chunk_size, end_date)
        try:
            page_req_key = None
            while True:
                ret, data, page_req_key = quote_ctx.request_history_kline(
                    code=SYMBOL,
                    start=chunk_start.strftime("%Y-%m-%d"),
                    end=chunk_end.strftime("%Y-%m-%d"),
                    ktype="K_DAY",
                    autype="qfq",
                    max_count=1000,
                    page_req_key=page_req_key,
                )
                if ret == 0 and data is not None and not data.empty:
                    all_data.append(data)
                if not page_req_key:
                    break
            fetched = sum(len(d) for d in all_data)
            print(f"  Fetched bars for {chunk_start.date()} ~ {chunk_end.date()} (total: {fetched})")
        except Exception as e:
            print(f"  Error fetching {chunk_start.date()} ~ {chunk_end.date()}: {e}")

        chunk_start = chunk_end + timedelta(days=1)

    quote_ctx.close()

    if not all_data:
        print("ERROR: No data downloaded. Is Futu OpenD running?")
        return None

    df = pd.concat(all_data, ignore_index=True)
    df = df.drop_duplicates(subset=["time_key"], keep="first")
    df = df.sort_values("time_key").reset_index(drop=True)

    df["timestamp"] = pd.to_datetime(df["time_key"])
    df["symbol"] = SYMBOL

    out_df = df[["timestamp", "symbol", "open", "high", "low", "close", "volume"]].copy()
    out_df = out_df.sort_values("timestamp").reset_index(drop=True)

    try:
        out_df.to_parquet(PARQUET_PATH, engine="pyarrow", compression="snappy")
        print(f"\nSaved {len(out_df)} bars to {PARQUET_PATH}")
    except ImportError:
        out_df.to_csv(CSV_PATH, index=False)
        print(f"\nSaved {len(out_df)} bars to {CSV_PATH}")
    print(f"Date range: {out_df['timestamp'].min().date()} ~ {out_df['timestamp'].max().date()}")
    print(f"Columns: {list(out_df.columns)}")

    return out_df


def load_data():
    if PARQUET_PATH.exists():
        print(f"Loading cached data from {PARQUET_PATH}...")
        df = pd.read_parquet(PARQUET_PATH)
        print(f"Loaded {len(df)} bars: {df['timestamp'].min().date()} ~ {df['timestamp'].max().date()}")
        return df
    if CSV_PATH.exists():
        print(f"Loading cached data from {CSV_PATH}...")
        df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
        print(f"Loaded {len(df)} bars: {df['timestamp'].min().date()} ~ {df['timestamp'].max().date()}")
        return df
    return None


def run_backtest(df):
    from quant.core.backtester import Backtester, BacktestResultExporter
    from quant.strategies.base import Strategy
    from quant.strategies.registry import strategy as reg_strategy
    from quant.core.walkforward import _DataFrameProvider
    from datetime import date as date_type
    from typing import Any, Dict, List, Optional

    @reg_strategy("TencentMomentum")
    class TencentMomentum(Strategy):
        """Single-stock SMA crossover momentum strategy for HK.00700.

        - Buy when fast SMA > slow SMA (golden cross) and we have no position
        - Sell when fast SMA < slow SMA (death cross) and we hold a position
        - Risk-managed position sizing based on ATR
        """

        def __init__(
            self,
            symbol: str = "HK.00700",
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

    provider = _DataFrameProvider(df)
    backtester = Backtester(config)

    start = df["timestamp"].min()
    end = df["timestamp"].max()

    print(f"Period: {start.date()} ~ {end.date()}")
    print(f"Initial cash: HK${initial_cash:,.0f}")
    print(f"Strategy: SMA Crossover (fast=20, slow=60)")
    print(f"Commission: 0.1% (min HK$2), Slippage: 10 bps")
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

    output_prefix = str(DATA_DIR / "HK_00700_backtest")
    BacktestResultExporter.to_csv(result, output_prefix)
    print(f"\nResults exported to {output_prefix}_*.csv")

    equity_path = DATA_DIR / "HK_00700_equity.csv"
    result.equity_curve.to_csv(str(equity_path))
    print(f"Equity curve saved to {equity_path}")

    buy_hold_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    print(f"\nBenchmark: Buy & Hold return = {buy_hold_return:.2%}")

    return result


def main():
    force_download = "--download" in sys.argv

    if force_download or not PARQUET_PATH.exists() and not CSV_PATH.exists():
        print("=" * 60)
        print("STEP 1: DOWNLOAD DATA FROM FUTU")
        print("=" * 60)
        df = download_data()
        if df is None:
            sys.exit(1)
    else:
        print("=" * 60)
        print("STEP 1: LOAD CACHED DATA")
        print("=" * 60)
        df = load_data()
        if df is None:
            print("Cache not found, downloading...")
            df = download_data()
            if df is None:
                sys.exit(1)

    print("\nData sample (first 5 rows):")
    print(df.head().to_string(index=False))
    print(f"\nData stats:")
    print(f"  Rows: {len(df)}")
    print(f"  Close range: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    print(f"  Avg daily volume: {df['volume'].mean():,.0f}")

    result = run_backtest(df)

    return result


if __name__ == "__main__":
    main()
