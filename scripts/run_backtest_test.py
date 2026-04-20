"""Quick end-to-end backtest test."""
import sys
sys.path.insert(0, "D:/vk/quant")

from datetime import datetime
from pathlib import Path
import pandas as pd
import time

from quant.core.backtester import Backtester
from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.data.storage_duckdb import DuckDBStorage
from quant.strategies.simple_momentum.strategy import SimpleMomentum
from quant.core.walkforward import _DataFrameProvider

symbols = ["HK.00700", "HK.09988", "HK.01810", "HK.09618", "HK.03690"]
start = datetime(2024, 1, 1)
end = datetime(2024, 12, 31)

print(f"Loading data for {symbols}...")
provider = DuckDBProvider(db_path="D:/vk/quant/data/duckdb/quant.duckdb")
provider.connect()

all_data = []
for symbol in symbols:
    bars = provider.get_bars(symbol, start, end, "1d")
    print(f"  {symbol}: {len(bars)} bars")
    if not bars.empty:
        all_data.append(bars)
provider.disconnect()

data = pd.concat(all_data, ignore_index=True)
print(f"Total: {len(data)} bars for {data['symbol'].nunique()} symbols")

storage = DuckDBStorage("D:/vk/quant/data/duckdb/quant.duckdb")
lot_sizes = {}
for symbol in symbols:
    lot_sizes[symbol] = storage.get_lot_size(symbol)
storage.close()
print(f"Lot sizes: {lot_sizes}")

print("\nBuilding _DataFrameProvider with _bar_map index...")
t0 = time.perf_counter()
data_provider = _DataFrameProvider(data)
t1 = time.perf_counter()
print(f"  Index built in {t1-t0:.3f}s ({len(data_provider._bar_map)} keys)")

warnings = data_provider.validate()
if warnings:
    print("\nData quality warnings:")
    for w in warnings:
        print(f"  - {w}")

config = {
    "backtest": {"slippage_bps": 5},
    "execution": {
        "commission": {
            "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
            "HK": {"type": "hk_realistic"},
        }
    },
    "data": {"default_timeframe": "1d"},
    "risk": {
        "max_position_pct": 0.20,
        "max_sector_pct": 1.0,
        "max_daily_loss_pct": 0.10,
        "max_leverage": 2.0,
        "max_orders_minute": 100,
    },
}

strategy = SimpleMomentum(symbols=symbols)
backtester = Backtester(config, lot_sizes=lot_sizes)

print("\nRunning backtest...")
t2 = time.perf_counter()
result = backtester.run(
    start=start,
    end=end,
    strategies=[strategy],
    initial_cash=1000000,
    data_provider=data_provider,
    symbols=data['symbol'].unique().tolist(),
)
t3 = time.perf_counter()
print(f"  Backtest completed in {t3-t2:.3f}s")

diag = result.diagnostics
print(f"\n{'='*50}")
print(f"BACKTEST RESULTS: SimpleMomentum (5 HSTECH stocks, 2024)")
print(f"{'='*50}")
print(f"Final NAV:        ${result.final_nav:,.2f}")
print(f"Total Return:     {result.total_return*100:.2f}%")
print(f"Sharpe Ratio:     {result.sharpe_ratio:.2f}")
print(f"Max Drawdown:     {result.max_drawdown_pct*100:.2f}%")
print(f"Win Rate:         {result.win_rate*100:.1f}%")
print(f"Total Trades:     {len(result.trades)}")
print(f"{'='*50}")
print(f"\nDiagnostics:")
print(f"  Suspended days:       {diag.suspended_days}")
print(f"  Volume-limited:       {diag.volume_limited_trades}")
print(f"  Lot-adjusted:         {diag.lot_adjusted_trades}")
print(f"  Avg fill delay:       {diag.avg_fill_delay_days:.1f} days")
print(f"  Total costs:          ${diag.total_commission:,.2f}")
print(f"  Cost drag:            {diag.cost_drag_pct:.1f}%")

if result.trades:
    print(f"\nFirst 5 trades:")
    for t in result.trades[:5]:
        print(f"  {t.symbol} {t.side} qty={t.quantity} entry={t.entry_price:.2f} exit={t.exit_price:.2f} pnl={t.pnl:.2f}")
        if t.cost_breakdown:
            print(f"    costs: {t.cost_breakdown}")
        if t.signal_date and t.fill_date:
            delay = (t.fill_date.date() - t.signal_date.date()).days if hasattr(t.fill_date, 'date') else 0
            print(f"    signal={t.signal_date.date() if hasattr(t.signal_date, 'date') else t.signal_date} fill={t.fill_date.date() if hasattr(t.fill_date, 'date') else t.fill_date} delay={delay}d")
