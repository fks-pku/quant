"""Run DualMACrossover on real CSI300 data."""
from datetime import datetime
import pandas as pd
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.registry import StrategyRegistry

db = DuckDBStorage()
symbols = ["000300"]
data = db.get_bars("000300", datetime(2020, 1, 1), datetime(2025, 5, 1))
print(f"Loaded {len(data)} bars for 000300")

provider = DataFrameProvider(data)

config = {
    "backtest": {"slippage_bps": 5},
    "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
    "risk": {
        "max_position_pct": 1.0,
        "max_daily_loss_pct": 1.0,
        "max_leverage": 999,
        "max_orders_minute": 999,
    },
}

strategy = StrategyRegistry.create(
    "DualMACrossover", symbols=symbols, fast_period=5, slow_period=20
)
bt = Backtester(config)
result = bt.run(
    start=data["timestamp"].min(),
    end=data["timestamp"].max(),
    strategies=[strategy],
    initial_cash=10000,
    data_provider=provider,
    symbols=symbols,
)

print("=" * 60)
print("DualMACrossover on CSI300 (000300)")
print("=" * 60)
print(f"Period: {data['timestamp'].min().date()} ~ {data['timestamp'].max().date()}")
print(f"Params: fast=5, slow=20, buffer=1%")
print(f"Initial Cash: 10,000 CNY")
print("---")
print(f"Final NAV:    {result.final_nav:>12,.2f}")
print(f"Total Return: {result.total_return*100:>11.2f}%")
print(f"Sharpe:       {result.sharpe_ratio:>12.2f}")
print(f"Sortino:      {result.sortino_ratio:>12.2f}")
print(f"Max Drawdown: {result.max_drawdown_pct:>11.2f}%")
print(f"Win Rate:     {result.win_rate*100:>11.1f}%")
print(f"Profit Factor:{result.profit_factor:>11.2f}")
print(f"Total Trades: {len(result.trades):>12}")
print(f"Commission:   {result.diagnostics.total_commission:>12,.2f}")
print(f"Fills:        {result.diagnostics.fill_count:>12}")
print(f"Lot Adjusted: {result.diagnostics.lot_adjusted_trades:>12}")
print(f"Limit Reject: {result.diagnostics.limit_rejected_orders:>12}")
print(f"T+1 Reject:   {result.diagnostics.t1_rejected_sells:>12}")
print("---")

buys = [t for t in result.trades if t.side == "BUY"]
sells = [t for t in result.trades if t.side == "SELL"]
print(f"Buys: {len(buys)}, Sells: {len(sells)}")

if sells:
    wins = [t for t in sells if t.pnl > 0]
    losses = [t for t in sells if t.pnl <= 0]
    print(f"Winning sells: {len(wins)}, Losing sells: {len(losses)}")
    avg_pnl = sum(t.pnl for t in sells) / len(sells)
    print(f"Avg sell PnL: {avg_pnl:,.2f}")

print("\nTrade details:")
for t in result.trades:
    entry = t.entry_time.strftime("%Y-%m-%d") if hasattr(t.entry_time, "strftime") else str(t.entry_time)
    exit_ = t.exit_time.strftime("%Y-%m-%d") if hasattr(t.exit_time, "strftime") else str(t.exit_time)
    print(f"  {t.side:4s} qty={t.quantity:>6.0f} entry={t.entry_price:>10.2f} "
          f"exit={t.exit_price:>10.2f} pnl={t.pnl:>10.2f} @ {exit_}")

# Buy-and-hold benchmark
first_close = data.iloc[0]["close"]
last_close = data.iloc[-1]["close"]
bh_return = (last_close - first_close) / first_close * 100
print(f"\nBenchmark (000300 buy-and-hold): {bh_return:.2f}%")
