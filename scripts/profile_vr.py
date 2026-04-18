"""Profile VolatilityRegime backtest to find the exact bottleneck."""
import sys
sys.path.insert(0, "D:/vk/quant/system")

import time
from datetime import datetime
import pandas as pd
from collections import Counter

from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.core.walkforward import _DataFrameProvider
from quant.core.backtester import Backtester
from quant.strategies.volatility_regime.strategy import VolatilityRegime

all_syms = ['HK.01024','HK.06618','HK.02013','HK.02057','HK.00700','HK.09988',
            'HK.03690','HK.09618','HK.02015','HK.09888','HK.09961','HK.02382',
            'HK.00981','HK.09626','HK.09901','HK.02018','HK.01810','HK.00285',
            'HK.06690','HK.00268','HK.00772']

start = datetime(2020, 1, 1)
end = datetime(2024, 12, 31)

# Load data via DuckDB query directly (API server holds the file lock)
t0 = time.perf_counter()
import duckdb
conn = duckdb.connect("D:/vk/quant/data/duckdb/quant.duckdb", read_only=True)
data_df = conn.execute("""
    SELECT timestamp, symbol, open, high, low, close, volume 
    FROM daily_hk 
    WHERE timestamp >= '2020-01-01' AND timestamp <= '2024-12-31'
    ORDER BY timestamp, symbol
""").fetchdf()
conn.close()
t1 = time.perf_counter()
print(f"Data load: {t1-t0:.3f}s ({len(data_df)} bars)")

# Build provider
t2 = time.perf_counter()
dp = _DataFrameProvider(data_df)
t3 = time.perf_counter()
print(f"Provider build: {t3-t2:.3f}s")

# Patch get_bars to count calls
original_get_bars = dp.get_bars
call_count = Counter()
total_get_bars_time = [0.0]

def tracked_get_bars(symbol, start, end, timeframe):
    call_count[symbol] += 1
    t = time.perf_counter()
    result = original_get_bars(symbol, start, end, timeframe)
    total_get_bars_time[0] += time.perf_counter() - t
    return result

dp.get_bars = tracked_get_bars

# Run backtest
config = {
    "backtest": {"slippage_bps": 5},
    "execution": {"commission": {
        "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
        "HK": {"type": "percent", "percent": 0.001, "min_per_order": 2.0},
    }},
    "data": {"default_timeframe": "1d"},
    "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0, "max_orders_minute": 100},
}

strategy = VolatilityRegime(symbols=all_syms)
backtester = Backtester(config)

print(f"\nRunning backtest (VolatilityRegime, 21 symbols, 2020-2024)...")
t4 = time.perf_counter()
result = backtester.run(
    start=start, end=end,
    strategies=[strategy],
    initial_cash=100000,
    data_provider=dp,
    symbols=data_df['symbol'].unique().tolist(),
)
t5 = time.perf_counter()

print(f"\n{'='*50}")
print(f"Backtest run: {t5-t4:.3f}s")
print(f"  get_bars() calls: {sum(call_count.values())}")
print(f"  get_bars() total time: {total_get_bars_time[0]:.3f}s")
print(f"  get_bars() per call avg: {total_get_bars_time[0]/max(1,sum(call_count.values()))*1000:.2f}ms")
print(f"\nResult: NAV={result.final_nav:,.2f} Return={result.total_return*100:.2f}% Sharpe={result.sharpe_ratio:.2f}")
print(f"Trades: {len(result.trades)}")
print(f"\nTop 5 most-queried symbols:")
for sym, cnt in call_count.most_common(5):
    print(f"  {sym}: {cnt} calls")
