import sys, time
sys.path.insert(0, "D:/vk/quant/system")

t0 = time.perf_counter()

print("Importing...", flush=True)
t1 = time.perf_counter()
from quant.core.backtester import Backtester
from quant.strategies.volatility_regime.strategy import VolatilityRegime
from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.core.walkforward import _DataFrameProvider
t2 = time.perf_counter()
print(f"  Imports: {t2-t1:.3f}s")

print("Connecting DuckDB...", flush=True)
t3 = time.perf_counter()
db = DuckDBProvider(db_path="D:/vk/quant/data/duckdb/quant.duckdb")
db.connect()
t4 = time.perf_counter()
print(f"  DuckDB connect: {t4-t3:.3f}s")

from datetime import datetime
import pandas as pd

symbols = ['HK.01024','HK.06618','HK.02013','HK.02057','HK.00700','HK.09988',
           'HK.03690','HK.09618','HK.02015','HK.09888','HK.09961','HK.02382',
           'HK.00981','HK.09626','HK.09901','HK.02018','HK.01810','HK.00285',
           'HK.06690','HK.00268','HK.00772']
start = datetime(2020, 1, 1)
end = datetime(2024, 12, 31)

print("Loading data...", flush=True)
t5 = time.perf_counter()
all_data = []
for s in symbols:
    bars = db.get_bars(s, start, end, "1d")
    if not bars.empty:
        all_data.append(bars)
db.disconnect()
data_df = pd.concat(all_data, ignore_index=True)
t6 = time.perf_counter()
print(f"  Data load: {t6-t5:.3f}s ({len(data_df)} bars)")

print("Building provider...", flush=True)
t7 = time.perf_counter()
dp = _DataFrameProvider(data_df)
t8 = time.perf_counter()
print(f"  Provider: {t8-t7:.3f}s ({len(dp._bar_map)} keys, {len(dp.trading_dates)} dates)")

print("Running backtest...", flush=True)
t9 = time.perf_counter()
config = {
    "backtest": {"slippage_bps": 5},
    "execution": {"commission": {
        "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
        "HK": {"type": "percent", "percent": 0.001, "min_per_order": 2.0},
    }},
    "data": {"default_timeframe": "1d"},
    "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0, "max_orders_minute": 100},
}
strategy = VolatilityRegime(symbols=symbols)
backtester = Backtester(config)
result = backtester.run(
    start=start, end=end,
    strategies=[strategy],
    initial_cash=100000,
    data_provider=dp,
    symbols=data_df['symbol'].unique().tolist(),
)
t10 = time.perf_counter()
print(f"  Backtest: {t10-t9:.3f}s")

print("Serializing...", flush=True)
t11 = time.perf_counter()
equity_list = result.equity_curve.reset_index().values.tolist()
equity_serializable = [[str(r[0]), float(r[1])] for r in equity_list]
trades_list = []
for t in result.trades:
    trades_list.append({
        "entry_time": str(t.entry_time),
        "exit_time": str(t.exit_time),
        "symbol": t.symbol,
        "side": t.side,
        "entry_price": float(t.entry_price),
        "exit_price": float(t.exit_price),
        "quantity": int(t.quantity),
        "pnl": float(t.pnl),
    })
t12 = time.perf_counter()
print(f"  Serialize: {t12-t11:.3f}s ({len(equity_serializable)} points, {len(trades_list)} trades)")

print(f"\nTOTAL: {t12-t0:.3f}s")
