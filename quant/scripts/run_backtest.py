"""Run backtest for a strategy using yfinance data + DataFrameProvider.

Usage:
    python quant/scripts/run_backtest.py --strategy MyStrategy --symbols SPY GLD TLT --start 2020-01-01 --end 2025-01-01
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import numpy as np


def download_data(symbols, start, end):
    import yfinance as yf
    frames = []
    for sym in symbols:
        ticker = yf.Ticker(sym)
        df = ticker.history(start=start, end=end, auto_adjust=False)
        if df.empty:
            print(f"  WARNING: no data for {sym}")
            continue
        df = df.reset_index()
        df["symbol"] = sym
        df["timestamp"] = df["Date"]
        df["date"] = df["Date"].dt.date
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low", "Close": "close",
            "Volume": "volume",
        })
        if "Adj Close" in df.columns:
            df["adj_close"] = df["Adj Close"]
        else:
            df["adj_close"] = df["close"]
        df["adj_open"] = df["open"] * df["adj_close"] / df["close"]
        df["adj_high"] = df["high"] * df["adj_close"] / df["close"]
        df["adj_low"] = df["low"] * df["adj_close"] / df["close"]
        frames.append(df[["symbol", "timestamp", "date", "open", "high", "low", "close", "adj_close", "adj_open", "adj_high", "adj_low", "volume"]])
    if not frames:
        raise ValueError("No data downloaded")
    return pd.concat(frames, ignore_index=True)


def run_backtest(strategy_name, symbols, start, end, initial_capital=1_000_000):
    from quant.features.backtest.engine import Backtester
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.trading import Portfolio, RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.features.strategies.registry import StrategyRegistry

    registry = StrategyRegistry()
    strategy_cls = registry.get(strategy_name)
    if strategy_cls is None:
        raise ValueError(f"Strategy '{strategy_name}' not found in registry")

    print(f"  Downloading {symbols} from {start} to {end}...")
    data = download_data(symbols, start, end)
    print(f"  Downloaded {len(data)} bars for {data['symbol'].nunique()} symbols")

    provider = DataFrameProvider(data)
    strategy = strategy_cls(symbols=symbols)

    config = {
        "backtest": {
            "initial_capital": initial_capital,
            "start_date": start,
            "end_date": end,
            "slippage_bps": 5,
            "risk_price_deviation_limit": 0.15,
            "force_close_on_stop": True,
        },
        "execution": {"commission": {"market": "US", "rate": 0.001}},
    }

    backtester = Backtester(
        config=config,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
    )

    print(f"  Running backtest for {strategy_name}...")
    result = backtester.run(
        start=datetime.strptime(start, "%Y-%m-%d"),
        end=datetime.strptime(end, "%Y-%m-%d"),
        strategies=[strategy],
        initial_cash=initial_capital,
        data_provider=provider,
        symbols=symbols,
    )

    print()
    print("=" * 60)
    print(f"  BACKTEST RESULTS: {strategy_name}")
    print("=" * 60)
    print(f"  Period:          {start} → {end}")
    print(f"  Symbols:         {symbols}")
    print(f"  Initial Capital: ${initial_capital:,.0f}")
    print(f"  Final NAV:       ${result.final_nav:,.2f}")
    print(f"  Total Return:    {result.total_return:.2%}")
    print(f"  Sharpe Ratio:    {result.sharpe_ratio:.3f}")
    print(f"  Sortino Ratio:   {result.sortino_ratio:.3f}")
    print(f"  Max Drawdown:    {result.max_drawdown_pct:.2%}")
    print(f"  Win Rate:        {result.win_rate:.2%}")
    print(f"  Profit Factor:   {result.profit_factor:.2f}")
    print(f"  Total Trades:    {len(result.trades)}")
    d = result.diagnostics
    print(f"  Fills:           {d.fill_count}")
    print(f"  Commissions:     ${d.total_commission:,.2f}")
    print(f"  Gross PnL:       ${d.total_gross_pnl:,.2f}")
    print(f"  Rejections:      {dict(d.rejection_counts)}")
    print(f"  Expired orders:  {d.expired_orders}")
    print(f"  Submission rej:  {d.submission_rejected}")
    print("=" * 60)

    return {
        "strategy": strategy_name,
        "symbols": symbols,
        "start": start,
        "end": end,
        "initial_capital": initial_capital,
        "final_nav": round(result.final_nav, 2),
        "total_return": round(result.total_return, 4),
        "sharpe_ratio": round(result.sharpe_ratio, 3),
        "sortino_ratio": round(result.sortino_ratio, 3),
        "max_drawdown_pct": round(result.max_drawdown_pct, 4),
        "win_rate": round(result.win_rate, 4),
        "profit_factor": round(result.profit_factor, 2),
        "total_trades": len(result.trades),
        "fill_count": d.fill_count,
        "total_commission": round(d.total_commission, 2),
        "total_gross_pnl": round(d.total_gross_pnl, 2),
        "rejection_counts": dict(d.rejection_counts),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-01-01")
    parser.add_argument("--capital", type=float, default=1_000_000)
    args = parser.parse_args()
    result = run_backtest(args.strategy, args.symbols, args.start, args.end, args.capital)
    print(json.dumps(result, indent=2))
