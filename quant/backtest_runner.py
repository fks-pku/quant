"""CLI runner for backtests using DuckDB-stored data."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from quant.core.backtester import Backtester, BacktestResultExporter
from quant.data.providers.duckdb_provider import DuckDBProvider
from quant.data.storage_duckdb import DuckDBStorage
from quant.strategies.registry import StrategyRegistry


def _normalize_symbol(symbol):
    if symbol.startswith("HK.") or symbol.startswith("US."):
        return symbol
    if symbol.isdigit() and len(symbol) >= 5:
        return f"HK.{symbol}"
    return f"US.{symbol}"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Backtest Runner (DuckDB data)")
    parser.add_argument("--strategy", required=True, help="Strategy name (e.g. SimpleMomentum)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols (e.g. AAPL,HK.00700)")
    parser.add_argument("--initial-cash", type=float, default=100000)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--output-dir", default="./backtest_output")
    parser.add_argument("--db", default="./var/duckdb/quant.duckdb")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    raw_symbols = [s.strip().upper() for s in args.symbols.split(",")]
    symbols = [_normalize_symbol(s) for s in raw_symbols]

    registry = StrategyRegistry()
    strategy_class = registry.get(args.strategy)
    if strategy_class is None:
        print(f"Error: Strategy '{args.strategy}' not found. Available: {registry.list_strategies()}")
        sys.exit(1)
    strategy = strategy_class(symbols=symbols)

    print(f"Loading data for {symbols} from DuckDB ({args.start} ~ {args.end})...")
    provider = DuckDBProvider(db_path=args.db)
    provider.connect()

    all_data = []
    for symbol in symbols:
        bars = provider.get_bars(symbol, start, end, "1d")
        if bars.empty:
            print(f"Warning: No data for {symbol}, skipping")
            continue
        all_data.append(bars)

    if not all_data:
        print("Error: No data found for any symbol. Run `python scripts/prepare_data.py` first.")
        sys.exit(1)

    data = pd.concat(all_data, ignore_index=True)
    print(f"Loaded {len(data)} bars for {data['symbol'].nunique()} symbols")

    provider.disconnect()

    storage = DuckDBStorage(args.db)
    lot_sizes = {}
    for symbol in symbols:
        lot_sizes[symbol] = storage.get_lot_size(symbol)
    storage.close()
    print(f"Lot sizes: {lot_sizes}")

    config = {
        "backtest": {"slippage_bps": args.slippage_bps},
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

    from quant.core.walkforward import DataFrameProvider

    data_provider = DataFrameProvider(data)

    warnings = data_provider.validate()
    if warnings:
        print("\nData quality warnings:")
        for w in warnings:
            print(f"  - {w}")

    backtester = Backtester(config, lot_sizes=lot_sizes)
    result = backtester.run(
        start=start,
        end=end,
        strategies=[strategy],
        initial_cash=args.initial_cash,
        data_provider=data_provider,
        symbols=data['symbol'].unique().tolist(),
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_path = str(output_dir / f"{args.strategy}_{args.start}_{args.end}")

    BacktestResultExporter.to_csv(result, base_path)

    metrics = result.metrics
    diag = result.diagnostics
    print(f"\n{'='*50}")
    print(f"BACKTEST RESULTS: {args.strategy}")
    print(f"Period: {args.start} to {args.end}")
    print(f"{'='*50}")
    print(f"Final NAV:        ${result.final_nav:,.2f}")
    print(f"Total Return:     {result.total_return*100:.2f}%")
    print(f"Sharpe Ratio:     {result.sharpe_ratio:.2f}")
    print(f"Sortino Ratio:    {result.sortino_ratio:.2f}")
    print(f"Max Drawdown:     {result.max_drawdown_pct*100:.2f}%")
    print(f"Win Rate:         {result.win_rate*100:.1f}%")
    print(f"Profit Factor:    {result.profit_factor:.2f}")
    print(f"Total Trades:     {metrics.total_trades}")
    print(f"Avg Duration:     {metrics.avg_trade_duration}")
    print(f"{'='*50}")
    print(f"\nDiagnostics:")
    print(f"  Suspended days:       {diag.suspended_days}")
    print(f"  Volume-limited:       {diag.volume_limited_trades}")
    print(f"  Lot-adjusted:         {diag.lot_adjusted_trades}")
    print(f"  Avg fill delay:       {diag.avg_fill_delay_days:.1f} days")
    print(f"  Total costs:          ${diag.total_commission:,.2f}")
    print(f"  Cost drag:            {diag.cost_drag_pct:.1f}%")
    print(f"\nResults saved to: {base_path}_*.csv")

    metrics_json = {
        "strategy": args.strategy,
        "start": args.start,
        "end": args.end,
        "symbols": symbols,
        "initial_cash": args.initial_cash,
        "final_nav": result.final_nav,
        "total_return": result.total_return,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "max_drawdown": result.max_drawdown,
        "max_drawdown_pct": result.max_drawdown_pct,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_trades": metrics.total_trades,
        "calmar_ratio": metrics.calmar_ratio,
        "payoff_ratio": metrics.payoff_ratio,
        "expectancy": metrics.expectancy,
        "diagnostics": {
            "suspended_days": diag.suspended_days,
            "volume_limited_trades": diag.volume_limited_trades,
            "lot_adjusted_trades": diag.lot_adjusted_trades,
            "avg_fill_delay_days": diag.avg_fill_delay_days,
            "total_commission": diag.total_commission,
            "cost_drag_pct": diag.cost_drag_pct,
        }
    }
    with open(f"{base_path}_metrics.json", "w") as f:
        json.dump(metrics_json, f, indent=2, default=str)

    return result


if __name__ == "__main__":
    main()
