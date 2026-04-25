"""Run A-share strategy backtests and generate report."""
import sys
import json
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd

from quant.features.backtest.engine import Backtester, BacktestResultExporter
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.features.strategies.registry import StrategyRegistry
from quant.features.backtest.walkforward import DataFrameProvider

warnings.filterwarnings("ignore")

INDEXES_3 = ["000300", "000905", "399006"]
STOCKS_15 = [
    "000333", "000651", "000858", "000568", "000001",
    "000538", "000895", "000725", "000625", "000878",
    "000709", "000876", "000786", "000063", "000938",
]
START = "2015-01-01"
END = "2024-12-31"
CASH = 1_000_000


def run_backtest(strategy_name, symbols, extra_kwargs=None):
    extra_kwargs = extra_kwargs or {}
    start = datetime.strptime(START, "%Y-%m-%d")
    end = datetime.strptime(END, "%Y-%m-%d")

    registry = StrategyRegistry()
    strategy_class = registry.get(strategy_name)
    strategy = strategy_class(symbols=symbols, **extra_kwargs)

    print(f"\n--- {strategy_name} ---")
    print(f"Symbols: {symbols}")
    if extra_kwargs:
        print(f"Extra params: {extra_kwargs}")

    provider = DuckDBProvider()
    provider.connect()

    all_data = []
    loaded = []
    for symbol in sorted(set(symbols)):
        bars = provider.get_bars(symbol, start, end, "1d")
        if bars.empty:
            print(f"  NO DATA: {symbol}")
        else:
            print(f"  {symbol}: {len(bars)} bars")
            all_data.append(bars)
            loaded.append(symbol)
    provider.disconnect()

    if not all_data:
        print("  ERROR: No data loaded!")
        return None

    data = pd.concat(all_data, ignore_index=True)

    storage = DuckDBStorage()
    lot_sizes = {s: storage.get_lot_size(s) for s in symbols}
    storage.close()

    config = {
        "backtest": {"slippage_bps": 5},
        "execution": {
            "commission": {
                "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
                "HK": {"type": "hk_realistic"},
                "CN": {"type": "cn_realistic"},
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

    data_provider = DataFrameProvider(data)
    backtester = Backtester(config, lot_sizes=lot_sizes)
    result = backtester.run(
        start=start,
        end=end,
        strategies=[strategy],
        initial_cash=CASH,
        data_provider=data_provider,
        symbols=loaded,
    )

    output_dir = Path("quant/infrastructure/var/research")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_path = str(output_dir / f"{strategy_name}_{START}_{END}")
    BacktestResultExporter.to_csv(result, base_path)

    m = result.metrics
    d = result.diagnostics
    metrics = {
        "strategy": strategy_name,
        "start": START,
        "end": END,
        "symbols": symbols,
        "initial_cash": CASH,
        "final_nav": round(result.final_nav, 2),
        "total_return_pct": round(result.total_return * 100, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "sortino_ratio": round(result.sortino_ratio, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct * 100, 2),
        "win_rate_pct": round(result.win_rate * 100, 1),
        "profit_factor": round(result.profit_factor, 2),
        "total_trades": m.total_trades,
        "calmar_ratio": round(m.calmar_ratio, 4),
        "payoff_ratio": round(m.payoff_ratio, 2),
        "expectancy": round(m.expectancy, 2),
        "diagnostics": {
            "volume_limited_trades": d.volume_limited_trades,
            "lot_adjusted_trades": d.lot_adjusted_trades,
            "t1_rejected_sells": d.t1_rejected_sells,
            "total_commission": round(d.total_commission, 2),
            "cost_drag_pct": round(d.cost_drag_pct, 1),
        },
    }
    with open(f"{base_path}_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    print(f"  NAV: {result.final_nav:,.0f} | Return: {result.total_return*100:.2f}%")
    print(f"  Sharpe: {result.sharpe_ratio:.2f} | Sortino: {result.sortino_ratio:.2f}")
    print(f"  MaxDD: {result.max_drawdown_pct*100:.2f}% | WinRate: {result.win_rate*100:.1f}%")
    print(f"  Trades: {m.total_trades} | PF: {result.profit_factor:.2f}")
    print(f"  Commission: ${d.total_commission:,.0f} | CostDrag: {d.cost_drag_pct:.1f}%")

    return metrics


def main():
    print("=" * 60)
    print("A-SHARE STRATEGY BACKTESTS (2015-2024)")
    print("=" * 60)

    results = []

    r1 = run_backtest("VolatilityScaledTrend", INDEXES_3)
    if r1:
        results.append(r1)

    r2 = run_backtest("DailyReturnAnomaly", STOCKS_15)
    if r2:
        results.append(r2)

    r3 = run_backtest(
        "RegimeFilteredMomentum", STOCKS_15, extra_kwargs={"benchmark_symbol": "000300"}
    )
    if r3:
        results.append(r3)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    header = f"{'Strategy':<28} {'Ret%':>7} {'Sharpe':>7} {'MaxDD%':>7} {'Win%':>6} {'Trades':>7} {'PF':>5}"
    print(header)
    print("-" * len(header))
    for r in results:
        line = (
            f"{r['strategy']:<28} "
            f"{r['total_return_pct']:>7.2f} "
            f"{r['sharpe_ratio']:>7.2f} "
            f"{r['max_drawdown_pct']:>7.2f} "
            f"{r['win_rate_pct']:>6.1f} "
            f"{r['total_trades']:>7} "
            f"{r['profit_factor']:>5.2f}"
        )
        print(line)

    print(f"\nPeriod: {START} to {END} | Initial Cash: {CASH:,} CNY")
    print("Output: quant/infrastructure/var/research/")


if __name__ == "__main__":
    main()
