"""Diagnostic: trace BollingerMeanReversion around 2021 NAV inflection."""
import sys
import json
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import numpy as np

from quant.features.backtest.engine import Backtester
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.features.strategies.registry import StrategyRegistry
from quant.features.backtest.walkforward import DataFrameProvider

warnings.filterwarnings("ignore")

SYMBOLS = [
    "000300","000513","000683","000977","000987","000001","000002",
    "000032","000060","000063","000155","000333","000519","000651",
    "000975","001286","001391","399001","000301","000596","000725",
    "000739","000783","001213","000538","000733","000768","001221",
    "000400","000703","000709","000887","000100","000537","000598",
    "000921","000933","000967","000039","000426","000568","000629",
    "000878","000893","000898","000937","000963","000591","000729",
    "000831","000883","000997","000016","000157","000728","001389",
    "000009","000088","000166","000429","000630","000776","000792",
    "000825","000876","399006","000034","000415","000425","000563",
    "000617","000623","000657","000786","000932","000983","000062",
    "000338","000528","000582","000708","000951","000021","000661",
    "000750","000830","000895","000999","000905","000408","000423",
    "000539","000723","000738","000785","000807","000958","399673",
    "000027","000050","000559","000625","000858","000938","000959",
    "000960",
]

START = "2020-01-01"
END = "2024-12-31"
CASH = 1_000_000


def main():
    start = datetime.strptime(START, "%Y-%m-%d")
    end = datetime.strptime(END, "%Y-%m-%d")

    provider = DuckDBProvider()
    provider.connect()
    all_data = []
    loaded = []
    for s in sorted(set(SYMBOLS)):
        bars = provider.get_bars(s, start, end, "1d")
        if not bars.empty:
            all_data.append(bars)
            loaded.append(s)
    provider.disconnect()
    data = pd.concat(all_data, ignore_index=True)
    print(f"Loaded {len(data)} bars for {len(loaded)} symbols")

    storage = DuckDBStorage()
    lot_sizes = {s: storage.get_lot_size(s) for s in loaded}
    storage.close()

    config = {
        "backtest": {"slippage_bps": 5},
        "execution": {"commission": {
            "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
            "HK": {"type": "hk_realistic"},
            "CN": {"type": "cn_realistic"},
        }},
        "data": {"default_timeframe": "1d"},
        "risk": {
            "max_position_pct": 0.20, "max_sector_pct": 1.0,
            "max_daily_loss_pct": 0.10, "max_leverage": 2.0, "max_orders_minute": 100,
        },
    }

    strategy = StrategyRegistry().get("BollingerMeanReversion")(symbols=loaded)
    data_provider = DataFrameProvider(data)
    backtester = Backtester(config, lot_sizes=lot_sizes)
    result = backtester.run(
        start=start, end=end, strategies=[strategy],
        initial_cash=CASH, data_provider=data_provider, symbols=loaded,
    )

    ec = result.equity_curve
    trades = result.trades

    print(f"\nTotal trades: {len(trades)}")
    print(f"Final NAV: {result.final_nav:,.0f} | Return: {result.total_return*100:.2f}%")

    ec_df = ec.reset_index()
    ec_df.columns = ["date", "nav"]
    ec_df["date"] = pd.to_datetime(ec_df["date"])
    ec_df["daily_ret"] = ec_df["nav"].pct_change()
    ec_df["prev_nav"] = ec_df["nav"].shift(1)
    ec_df["nav_change"] = ec_df["nav"] - ec_df["prev_nav"]

    max_jump = ec_df.loc[ec_df["nav_change"].abs().idxmax()]
    print(f"\nLargest single-day NAV change:")
    print(f"  Date: {max_jump['date'].date()}")
    print(f"  NAV: {max_jump['prev_nav']:,.0f} -> {max_jump['nav']:,.0f} (change: {max_jump['nav_change']:,.0f})")
    print(f"  Daily return: {max_jump['daily_ret']*100:.2f}%")

    ec_df["week"] = ec_df["date"].dt.isocalendar().year.astype(str) + "-" + ec_df["date"].dt.isocalendar().week.astype(str).str.zfill(2)
    weekly = ec_df.groupby("week").agg(
        start_nav=("nav", "first"), end_nav=("nav", "last"),
        start_date=("date", "first"), end_date=("date", "last"),
    )
    weekly["change"] = weekly["end_nav"] - weekly["start_nav"]
    weekly["pct"] = (weekly["end_nav"] / weekly["start_nav"] - 1) * 100
    top_weeks = weekly.reindex(weekly["change"].abs().sort_values(ascending=False).index).head(10)
    print(f"\nTop 10 weeks by absolute NAV change:")
    for _, row in top_weeks.iterrows():
        print(f"  {row['start_date'].date()} ~ {row['end_date'].date()}: "
              f"{row['start_nav']:,.0f} -> {row['end_nav']:,.0f} ({row['pct']:+.2f}%)")

    print(f"\n--- Trades around largest NAV jump ({max_jump['date'].date()}) ---")
    jump_date = max_jump["date"]
    nearby = [t for t in trades if abs((pd.Timestamp(t.fill_date) - jump_date).days) <= 10]
    for t in sorted(nearby, key=lambda x: x.fill_date):
        print(f"  {pd.Timestamp(t.fill_date).date()} {t.side:4s} {t.symbol} "
              f"qty={t.quantity:.0f} @ {t.fill_price:.2f} pnl={t.pnl:,.2f}")

    print(f"\n--- Position count over time ---")
    trades_df = pd.DataFrame([
        {"date": pd.Timestamp(t.fill_date).date(), "symbol": t.symbol, "side": t.side, "qty": t.quantity}
        for t in trades
    ])
    if not trades_df.empty:
        trades_df["date"] = pd.to_datetime(trades_df["date"])
        buys = trades_df[trades_df["side"] == "BUY"].groupby("date").size()
        sells = trades_df[trades_df["side"] == "SELL"].groupby("date").size()
        active_days = buys.index.union(sells.index)
        for d in sorted(active_days):
            b = buys.get(d, 0)
            s = sells.get(d, 0)
            if b + s >= 5:
                nav_row = ec_df[ec_df["date"] == d]
                nav_val = nav_row["nav"].values[0] if len(nav_row) > 0 else "?"
                print(f"  {d.date()}: {b} buys, {s} sells | NAV={nav_val}")

    print(f"\n--- Open positions at end ---")
    for pos in result.open_positions:
        print(f"  {pos['symbol']}: qty={pos['quantity']:.0f} entry={pos['entry_price']:.2f} "
              f"current={pos['current_price']:.2f} pnl={pos['unrealized_pnl']:,.0f}")

    print(f"\n--- NAV by year ---")
    ec_df["year"] = ec_df["date"].dt.year
    for year, grp in ec_df.groupby("year"):
        first_nav = grp["nav"].iloc[0]
        last_nav = grp["nav"].iloc[-1]
        ret = (last_nav / first_nav - 1) * 100
        print(f"  {year}: {first_nav:,.0f} -> {last_nav:,.0f} ({ret:+.2f}%)")


if __name__ == "__main__":
    main()
