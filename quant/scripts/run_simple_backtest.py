"""Lightweight backtest runner — simulates DailyBarStrategy on yfinance data.

Usage:
    python quant/scripts/run_simple_backtest.py --strategy MyStrategy --symbols SPY GLD TLT
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd


def download_data(symbols, start, end):
    import yfinance as yf
    frames = []
    for sym in symbols:
        df = yf.Ticker(sym).history(start=start, end=end, auto_adjust=False)
        if df.empty:
            continue
        df = df.reset_index()
        df["symbol"] = sym
        df["date"] = df["Date"].dt.date
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        adj_ratio = df["Adj Close"] / df["close"] if "Adj Close" in df.columns else 1.0
        df["adj_close"] = df["close"] * adj_ratio
        df["adj_open"] = df["open"] * adj_ratio
        df["adj_high"] = df["high"] * adj_ratio
        df["adj_low"] = df["low"] * adj_ratio
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


class _Fill:
    __slots__ = ("symbol", "quantity", "side", "price")

    def __init__(self, symbol, quantity, side, price):
        self.symbol = symbol
        self.quantity = quantity
        self.side = side
        self.price = price


class SimpleContext:
    def __init__(self, capital, strategy=None):
        self.cash = capital
        self.initial_capital = capital
        self.portfolio = type("P", (), {"nav": capital})()
        self._positions = {}
        self._trades = []
        self._equity = []
        self._dates = []
        self._prices = {}
        self._strategy = strategy

    def submit_order(self, symbol, quantity, side, order_type="MARKET", price=None, strategy_name=""):
        price = price or self._prices.get(symbol, 0)
        if price <= 0:
            return None
        cost = price * quantity
        commission = max(1.0, cost * 0.001)
        if side == "BUY":
            if self.cash < cost + commission:
                return None
            self.cash -= cost + commission
            self._positions[symbol] = self._positions.get(symbol, 0) + quantity
            self._trades.append({"side": "BUY", "symbol": symbol, "qty": quantity, "price": price, "commission": commission})
        elif side == "SELL":
            held = self._positions.get(symbol, 0)
            if held < quantity:
                return None
            self.cash += cost - commission
            self._positions[symbol] = held - quantity
            if self._positions[symbol] == 0:
                del self._positions[symbol]
            self._trades.append({"side": "SELL", "symbol": symbol, "qty": quantity, "price": price, "commission": commission})
        if self._strategy is not None:
            self._strategy.on_fill(self, _Fill(symbol, quantity, side, price))
        return f"order_{len(self._trades)}"

    def update_prices(self, prices):
        self._prices = prices
        mkt_value = sum(self._positions.get(s, 0) * prices.get(s, 0) for s in self._positions)
        self.portfolio.nav = self.cash + mkt_value

    def record_equity(self, date):
        mkt_value = sum(self._positions.get(s, 0) * self._prices.get(s, 0) for s in self._positions)
        nav = self.cash + mkt_value
        self._equity.append(nav)
        self._dates.append(date)


def run_backtest(strategy_name, symbols, start, end, capital=1_000_000):
    from quant.features.strategies.registry import StrategyRegistry

    registry = StrategyRegistry()
    strategy_cls = registry.get(strategy_name)
    if strategy_cls is None:
        raise ValueError(f"Strategy '{strategy_name}' not found")

    print(f"  Downloading {symbols}...")
    data = download_data(symbols, start, end)
    print(f"  Downloaded {len(data)} bars")

    strategy = strategy_cls(symbols=symbols)
    ctx = SimpleContext(capital, strategy=strategy)

    strategy.on_start(ctx)

    all_dates = sorted(data["date"].unique())
    for dt in all_dates:
        day_data = data[data["date"] == dt]
        prices = {}
        for _, row in day_data.iterrows():
            bar = row.to_dict()
            strategy.on_data(ctx, bar)
            prices[row["symbol"]] = row["adj_close"]

        ctx.update_prices(prices)

        from datetime import date as date_cls
        trading_date = dt if isinstance(dt, date_cls) else datetime.strptime(str(dt), "%Y-%m-%d").date()
        strategy.on_after_trading(ctx, trading_date)
        ctx.record_equity(dt)

    strategy.on_stop(ctx)

    equity = np.array(ctx._equity)
    returns = np.diff(equity) / equity[:-1]
    total_trades = len(ctx._trades)
    buy_trades = [t for t in ctx._trades if t["side"] == "BUY"]
    sell_trades = [t for t in ctx._trades if t["side"] == "SELL"]
    winning = sum(1 for t in sell_trades if t["price"] > 0)
    total_commission = sum(t["commission"] for t in ctx._trades)

    sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252)) if len(returns) > 1 and np.std(returns) > 0 else 0
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / peak
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0
    total_return = (equity[-1] / equity[0] - 1) if len(equity) > 0 else 0

    print()
    print("=" * 60)
    print(f"  BACKTEST: {strategy_name}")
    print("=" * 60)
    print(f"  Period:          {start} → {end}")
    print(f"  Symbols:         {symbols}")
    print(f"  Initial:         ${capital:,.0f}")
    print(f"  Final NAV:       ${equity[-1]:,.2f}")
    print(f"  Total Return:    {total_return:.2%}")
    print(f"  Sharpe:          {sharpe:.3f}")
    print(f"  Max Drawdown:    {max_dd:.2%}")
    print(f"  Trades:          {total_trades} ({len(buy_trades)} buys, {len(sell_trades)} sells)")
    print(f"  Commissions:     ${total_commission:,.2f}")
    print("=" * 60)

    return {
        "strategy": strategy_name,
        "symbols": list(symbols),
        "period": f"{start} to {end}",
        "initial_capital": capital,
        "final_nav": round(float(equity[-1]), 2),
        "total_return": round(float(total_return), 4),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown": round(float(max_dd), 4),
        "total_trades": total_trades,
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "total_commission": round(total_commission, 2),
    }


STRATEGIES = {}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-01-01")
    args = parser.parse_args()

    results = {}
    to_run = {args.strategy: ["SPY"]}
    for name, syms in to_run.items():
        try:
            results[name] = run_backtest(name, syms, args.start, args.end)
        except Exception as e:
            print(f"  ERROR: {name}: {e}")
            results[name] = {"error": str(e)}

    print("\n\n" + json.dumps(results, indent=2, default=str))
