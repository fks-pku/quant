"""Trace exact order lifecycle for 000792 around 2021-08-10 to confirm duplicate root cause."""
import sys
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd

from quant.features.backtest.engine import Backtester
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.features.strategies.registry import StrategyRegistry
from quant.features.backtest.walkforward import DataFrameProvider

warnings.filterwarnings("ignore")

SYMBOLS_000792 = ["000792"]

START = "2021-07-01"
END = "2021-09-30"
CASH = 1_000_000


def main():
    start = datetime.strptime(START, "%Y-%m-%d")
    end = datetime.strptime(END, "%Y-%m-%d")

    provider = DuckDBProvider()
    provider.connect()
    all_data = []
    loaded = []
    for s in SYMBOLS_000792:
        bars = provider.get_bars(s, start, end, "1d")
        if not bars.empty:
            all_data.append(bars)
            loaded.append(s)
    provider.disconnect()
    data = pd.concat(all_data, ignore_index=True)
    print(f"Loaded {len(data)} bars for {loaded}")

    if not data.empty:
        data_sorted = data.sort_values('timestamp')
        print(f"\n000792 bars around Aug 2021:")
        for _, row in data_sorted.iterrows():
            ts = row.get('timestamp', '')
            vol = row.get('volume', 0)
            close = row.get('close', 0)
            open_p = row.get('open', 0)
            print(f"  {ts} | O={open_p:.2f} C={close:.2f} Vol={vol:,.0f}")

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

    orig_on_after_trading = strategy.on_after_trading
    orig_on_fill = strategy.on_fill
    orig_buy = strategy.buy
    orig_sell = strategy.sell

    def traced_on_after_trading(context, trading_date):
        print(f"\n=== on_after_trading({trading_date}) ===")
        print(f"  _positions: {dict(strategy._positions)}")
        print(f"  _entry_dates: {strategy._entry_dates}")
        orig_on_after_trading(context, trading_date)
        om = getattr(context, 'order_manager', None)
        if om and hasattr(om, '_pending_orders'):
            for o in om._pending_orders:
                print(f"  -> PENDING: {o['side']} {o['symbol']} qty={o['quantity']}")

    def traced_on_fill(context, fill):
        print(f"\n=== on_fill: {fill.side} {fill.symbol} qty={fill.quantity} @ {fill.fill_price:.2f} ===")
        orig_on_fill(context, fill)
        print(f"  _positions after fill: {dict(strategy._positions)}")

    strategy.on_after_trading = traced_on_after_trading
    strategy.on_fill = traced_on_fill

    backtester = Backtester(config, lot_sizes=lot_sizes)
    result = backtester.run(
        start=start, end=end, strategies=[strategy],
        initial_cash=CASH, data_provider=data_provider, symbols=loaded,
    )

    print(f"\n\n=== SUMMARY ===")
    print(f"Total trades: {len(result.trades)}")
    for t in result.trades:
        print(f"  {pd.Timestamp(t.fill_date).date()} {t.side:4s} {t.symbol} "
              f"qty={t.quantity:.0f} @ {t.fill_price:.2f} pnl={t.pnl:,.2f}")


if __name__ == "__main__":
    main()
