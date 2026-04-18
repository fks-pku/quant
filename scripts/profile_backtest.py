"""Profile the API backtest path to find bottlenecks."""
import sys
sys.path.insert(0, "D:/vk/quant/system")

import time
from datetime import datetime
import pandas as pd

from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.core.walkforward import _DataFrameProvider
from quant.core.backtester import Backtester
from quant.strategies.simple_momentum.strategy import SimpleMomentum

t_total = time.perf_counter()

# === Step 1: Load symbols (same as API _init_default_symbols) ===
t0 = time.perf_counter()
from quant.data.storage_duckdb import DuckDBStorage
db = DuckDBStorage("D:/vk/quant/data/duckdb/quant.duckdb")
all_syms = db.get_symbols('daily', 'hk') + db.get_symbols('daily', 'us')
db.close()
t1 = time.perf_counter()
print(f"[Step 1] Get symbols: {t1-t0:.3f}s  ({len(all_syms)} symbols: {all_syms})")

# === Step 2: Load data from DuckDB (21 separate queries) ===
start_date = '2020-01-01'
end_date = '2024-12-31'
start = datetime.strptime(start_date, '%Y-%m-%d')
end = datetime.strptime(end_date, '%Y-%m-%d')

t2 = time.perf_counter()
provider = DuckDBProvider(db_path="D:/vk/quant/data/duckdb/quant.duckdb")
provider.connect()

all_data = []
for symbol in all_syms:
    ts = time.perf_counter()
    bars = provider.get_bars(symbol, start, end, "1d")
    te = time.perf_counter()
    if not bars.empty:
        all_data.append(bars)
        print(f"  {symbol}: {len(bars)} bars in {te-ts:.3f}s")
    else:
        print(f"  {symbol}: NO DATA")
provider.disconnect()
t3 = time.perf_counter()
data_df = pd.concat(all_data, ignore_index=True)
print(f"\n[Step 2] DuckDB load + concat: {t3-t2:.3f}s  ({len(data_df)} total bars)")

# === Step 3: Build _DataFrameProvider + _bar_map index ===
t4 = time.perf_counter()
data_provider = _DataFrameProvider(data_df)
t5 = time.perf_counter()
print(f"[Step 3] _DataFrameProvider + index: {t5-t4:.3f}s  ({len(data_provider._bar_map)} keys)")

# === Step 4: Run backtest ===
strategy = SimpleMomentum(symbols=all_syms)
config = {
    "backtest": {"slippage_bps": 5},
    "execution": {"commission": {
        "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
        "HK": {"type": "percent", "percent": 0.001, "min_per_order": 2.0},
    }},
    "data": {"default_timeframe": "1d"},
    "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0, "max_orders_minute": 100},
}

t6 = time.perf_counter()
backtester = Backtester(config)
result = backtester.run(
    start=start,
    end=end,
    strategies=[strategy],
    initial_cash=100000,
    data_provider=data_provider,
    symbols=data_df['symbol'].unique().tolist(),
)
t7 = time.perf_counter()
print(f"[Step 4] Backtest run: {t7-t7:.3f}s")  # fix
print(f"[Step 4] Backtest run: {t7-t6:.3f}s  ({len(result.trades)} trades)")

# === Step 5: Serialize results ===
t8 = time.perf_counter()
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
t9 = time.perf_counter()
print(f"[Step 5] Serialize results: {t9-t8:.3f}s  ({len(equity_serializable)} equity points)")

t_end = time.perf_counter()
print(f"\n{'='*50}")
print(f"TOTAL: {t_end-t_total:.3f}s")
print(f"  Result: NAV={result.final_nav:,.2f} Return={result.total_return*100:.2f}% Sharpe={result.sharpe_ratio:.2f}")
print(f"  Diagnostics: suspended={result.diagnostics.suspended_days} lot_adj={result.diagnostics.lot_adjusted_trades} cost=${result.diagnostics.total_commission:,.2f}")

# === Extra: Count calendar days vs trading days ===
n_calendar = (end - start).days + 1
trading_dates = set()
for sym in all_syms:
    sym_bars = data_df[data_df['symbol'] == sym]
    for ts in sym_bars['timestamp']:
        trading_dates.add(ts.date() if hasattr(ts, 'date') else ts)
n_trading = len(trading_dates)
print(f"\n  Calendar days in range: {n_calendar}")
print(f"  Trading days: {n_trading}")
print(f"  Wasted weekend iterations: {n_calendar - n_trading}")
