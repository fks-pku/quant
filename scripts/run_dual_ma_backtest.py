"""Run DualMACrossover backtest."""
from datetime import datetime
import pandas as pd
from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.registry import StrategyRegistry
from quant.tests.conftest import make_cn_bars

start = datetime(2024, 1, 2)
symbols = ["600519"]
data = make_cn_bars(symbols, start, 320, {"600519": 1800.0}, daily_return=0.0005)
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
print("DualMACrossover Backtest Report")
print("=" * 60)
print(f"Period: {data['timestamp'].min().date()} ~ {data['timestamp'].max().date()}")
print(f"Symbols: {symbols}")
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
    avg_pnl = sum(t.pnl for t in sells) / len(sells)
    print(f"Avg sell PnL: {avg_pnl:,.2f}")
