"""Run DualMACrossover on 000001 (Ping An Bank)."""
from datetime import datetime
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.registry import StrategyRegistry

storage = DuckDBStorage()
symbols = ["000001"]
data = storage.get_bars("000001", datetime(2020, 1, 1), datetime(2025, 5, 1))
print(f"Loaded {len(data)} bars, price range: {data['close'].min():.2f} ~ {data['close'].max():.2f}")

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
    initial_cash=500000,
    data_provider=provider,
    symbols=symbols,
)

print("=" * 60)
print("DualMACrossover on 000001 (Ping An Bank)")
print("=" * 60)
print(f"Period: {data['timestamp'].min().date()} ~ {data['timestamp'].max().date()}")
print(f"Initial Cash: 500,000 CNY")
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
print(f"T+1 Reject:   {result.diagnostics.t1_rejected_sells:>12}")
print("---")

buys = [t for t in result.trades if t.side == "BUY"]
sells = [t for t in result.trades if t.side == "SELL"]
print(f"Buys: {len(buys)}, Sells: {len(sells)}")
if sells:
    wins = [t for t in sells if t.pnl > 0]
    print(f"Winning sells: {len(wins)}/{len(sells)}")
