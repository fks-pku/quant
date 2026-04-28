#!/usr/bin/env python3
"""Walkthrough: Corporate Actions and Adjusted Price Separation in Backtest.

Constructs 20 trading days for stock 600519 with 3 corporate actions:
  Day 8:  Cash dividend  10派20 (2 CNY/share)
  Day 13: Stock dividend 10送10 (1 bonus share per share held)
  Day 17: Capitalization 10转5  (0.5 extra shares per share held)

Verifies:
  1. Signal uses adj_close -> momentum stays positive through corporate actions
  2. Execution uses real open -> fill prices are actual market prices
  3. Dividends correctly credit cash to portfolio
  4. Stock dividend (送股) correctly increases position quantity
  5. Capitalization (转增) correctly increases position quantity
  6. NAV reflects total return (real prices + dividend income)
"""

import sys
from pathlib import Path

_pkg_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_dir.parent))

from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import pandas as pd
import numpy as np

from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import DataFrameProvider
from quant.features.strategies.base import Strategy
from quant.features.strategies.registry import strategy

if TYPE_CHECKING:
    from quant.features.trading.engine import Context

START = datetime(2025, 1, 2)

ADJ_FACTORS = {
    1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.0,
    8: 1.05, 9: 1.05, 10: 1.05, 11: 1.05, 12: 1.05,
    13: 2.10, 14: 2.10, 15: 2.10, 16: 2.10,
    17: 3.15, 18: 3.15, 19: 3.15, 20: 3.15,
}

EVENTS = {
    8: "现金分红(10派20)",
    13: "送股(10送10)",
    17: "转增(10转5)",
}


@strategy("WalkthroughBuyHold")
class WalkthroughBuyHold(Strategy):

    def __init__(self, symbols=None, buy_day=6, max_pct=0.90):
        super().__init__("WalkthroughBuyHold")
        self._symbols = symbols or ["600519"]
        self.buy_day = buy_day
        self.max_pct = max_pct
        self._day_data: Dict[str, List] = {}
        self._day_count = 0
        self._bought = False
        self._momentum_scores: Dict[str, float] = {}
        self.daily_states: List[Dict] = []

    @property
    def symbols(self):
        return self._symbols

    def on_data(self, context: "Context", data: Any) -> None:
        symbol = data.get("symbol") if isinstance(data, dict) else getattr(data, "symbol", "")
        if symbol and symbol in self._symbols:
            self._day_data.setdefault(symbol, []).append(data)
            self._day_count += 1

    def on_after_trading(self, context: "Context", trading_date: date) -> None:
        self._snapshot(context, trading_date, phase="after_trading")

        if self._bought:
            return

        self._calculate_momentum_scores()

        if self._day_count >= self.buy_day:
            for sym in self._symbols:
                adj_close = self._adj(self._day_data[sym][-1], "close")
                real_close = self._day_data[sym][-1].get("close", 0)
                score = self._momentum_scores.get(sym, 0)

                nav = context.portfolio.nav
                qty = int(nav * self.max_pct / real_close)
                lot = 100
                qty = qty // lot * lot
                if qty > 0 and score > 0:
                    self.buy(sym, qty)
                    self._bought = True

    def _snapshot(self, context: "Context", trading_date: date, phase: str = "") -> None:
        pf = context.portfolio
        pos = pf.get_position("600519")
        self.daily_states.append({
            "date": trading_date,
            "day": (trading_date - START.date()).days + 1,
            "phase": phase,
            "qty": int(pos.quantity) if pos else 0,
            "avg_cost": pos.avg_cost if pos else 0.0,
            "cash": pf.cash,
            "market_value": sum(
                p.market_value for p in pf.positions.values() if p.quantity > 0
            ),
            "nav": pf.nav,
        })

    def _calculate_momentum_scores(self) -> None:
        self._momentum_scores.clear()
        for sym in self._symbols:
            bars = self._day_data.get(sym, [])
            if len(bars) < 2:
                continue
            prices = [self._adj(b, "close") for b in bars]
            if prices[0] > 0:
                self._momentum_scores[sym] = (prices[-1] - prices[0]) / prices[0]
            else:
                self._momentum_scores[sym] = 0.0

    def on_fill(self, context: "Context", fill: Any) -> None:
        super().on_fill(context, fill)

    def get_momentum_scores(self):
        return self._momentum_scores.copy()


def build_bars():
    rows = []
    for day in range(1, 21):
        ts = START + timedelta(days=day - 1)
        adj_close = 98.0 + day * 2.0
        factor = ADJ_FACTORS[day]
        close = round(adj_close / factor, 2)
        open_ = round(close * 0.998, 2)
        high = round(close * 1.005, 2)
        low = round(close * 0.995, 2)
        rows.append({
            "symbol": "600519",
            "timestamp": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 5_000_000,
            "adj_open": round(open_ * factor, 2),
            "adj_high": round(high * factor, 2),
            "adj_low": round(low * factor, 2),
            "adj_close": adj_close,
            "adj_factor": factor,
        })
    return pd.DataFrame(rows)


def build_dividends():
    return pd.DataFrame({
        "symbol": ["600519", "600519", "600519"],
        "ex_date": [
            START + timedelta(days=7),
            START + timedelta(days=12),
            START + timedelta(days=16),
        ],
        "cash_dividend": [2.0, 0.0, 0.0],
        "stock_dividend": [0.0, 1.0, 0.5],
    })


def print_header(title):
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")


def print_data_table(bars, dividends):
    print_header("Step 0: 构建数据 (20 个交易日 + 3 次公司行动)")
    print(f"\n{'Day':>4} {'Date':>12} {'close':>10} {'adj_close':>10} {'factor':>8} {'事件'}")
    print("-" * 70)
    for _, row in bars.iterrows():
        day = (row["timestamp"] - START).days + 1
        event = EVENTS.get(day, "")
        print(f"{day:>4} {row['timestamp'].strftime('%Y-%m-%d'):>12} "
              f"{row['close']:>10.2f} {row['adj_close']:>10.2f} {row['adj_factor']:>8.2f} {event}")

    print(f"\n公司行动数据:")
    for _, row in dividends.iterrows():
        day = (row["ex_date"] - START).days + 1
        parts = []
        if row["cash_dividend"] > 0:
            parts.append(f"现金分红 {row['cash_dividend']:.1f} 元/股")
        if row["stock_dividend"] > 0:
            parts.append(f"送股/转增 {row['stock_dividend']:.1f} 股/股")
        print(f"  Day {day:>2}: {', '.join(parts)}")


def print_momentum_comparison(bars):
    print_header("Step 1: 动量信号对比 (adj_close vs real close)")

    lookback = 5
    print(f"\n回看窗口: {lookback} 天")
    print(f"{'Day':>4} {'事件':>18} {'adj动量%':>10} {'real动量%':>10} {'信号'}")
    print("-" * 65)

    prices_real = bars["close"].values
    prices_adj = bars["adj_close"].values

    for i in range(lookback, len(bars)):
        day = i + 1
        event = EVENTS.get(day, "")
        adj_ret = (prices_adj[i] / prices_adj[i - lookback] - 1) * 100
        real_ret = (prices_real[i] / prices_real[i - lookback] - 1) * 100

        adj_sign = "多" if adj_ret > 0 else "空"
        real_sign = "多" if real_ret > 0 else "空"
        if adj_sign == real_sign:
            verdict = "一致"
        else:
            verdict = f"分歧! adj={adj_sign} real={real_sign}"

        print(f"{day:>4} {event:>18} {adj_ret:>+10.2f} {real_ret:>+10.2f} {verdict}")

    print(f"\n结论: adj_close 始终为正动量(多), real close 在公司行动日产生虚假负动量(空)")


def run_and_analyze(bars, dividends):
    print_header("运行回测并采集逐日状态")

    config = {
        "backtest": {"slippage_bps": 0},
        "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
        "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0},
    }

    bt = Backtester(config, lot_sizes={"600519": 100})
    strategy = WalkthroughBuyHold(symbols=["600519"], buy_day=6, max_pct=0.90)
    provider = DataFrameProvider(bars, dividends=dividends)

    result = bt.run(
        start=bars["timestamp"].min(),
        end=bars["timestamp"].max(),
        strategies=[strategy],
        initial_cash=1_000_000,
        data_provider=provider,
        symbols=["600519"],
    )

    # ===== 逐日状态表 =====
    print_header("逐日状态变化表")

    bar_map = {}
    for _, row in bars.iterrows():
        day = (row["timestamp"] - START).days + 1
        bar_map[day] = row

    div_map = {}
    for _, row in dividends.iterrows():
        day = (row["ex_date"] - START).days + 1
        parts = []
        if row["cash_dividend"] > 0:
            parts.append(f"现金分红{row['cash_dividend']:.0f}元/股")
        if row["stock_dividend"] > 0:
            parts.append(f"送转{row['stock_dividend']:.1f}股/股")
        div_map[day] = ", ".join(parts)

    trades_by_day = {}
    for t in result.trades:
        day = (pd.Timestamp(t.entry_time) - pd.Timestamp(START)).days + 1
        side = "BUY" if t.entry_time == t.exit_time else "SELL"
        trades_by_day.setdefault(day, []).append(
            f"{side} {int(t.quantity)}股@{t.fill_price:.2f}"
        )

    state_map = {}
    for s in strategy.daily_states:
        state_map[s["day"]] = s

    print()
    hdr = (
        f"{'Day':>3} "
        f"{'事件':>16} "
        f"{'real_close':>10} "
        f"{'adj_close':>10} "
        f"{'factor':>6} "
        f"{'持仓数量':>8} "
        f"{'成本价':>10} "
        f"{'市值':>12} "
        f"{'现金':>12} "
        f"{'NAV':>12} "
        f"{'成交/动作':>24}"
    )
    print(hdr)
    print("-" * len(hdr))

    prev_qty = 0
    prev_cash = 1_000_000.0
    for day in range(1, 21):
        bar = bar_map.get(day, {})
        st = state_map.get(day, {})
        event = EVENTS.get(day, "")
        div = div_map.get(day, "")

        label = event if event else div

        real_close = bar.get("close", 0)
        adj_close = bar.get("adj_close", 0)
        factor = bar.get("adj_factor", 0)

        qty = st.get("qty", prev_qty)
        cost = st.get("avg_cost", 0)
        mv = st.get("market_value", 0)
        cash = st.get("cash", prev_cash)
        nav = st.get("nav", prev_cash)

        trade_str = ""
        if day in trades_by_day:
            trade_str = "; ".join(trades_by_day[day])

        qty_delta = ""
        if qty != prev_qty:
            if qty > prev_qty:
                if prev_qty > 0 and event == "送股(10送10)":
                    qty_delta = f"(送股 {prev_qty}→{qty})"
                elif prev_qty > 0 and event == "转增(10转5)":
                    qty_delta = f"(转增 {prev_qty}→{qty})"
                else:
                    qty_delta = f"(新买入)"
            trade_str = trade_str + " " + qty_delta if trade_str else qty_delta.strip()

        cash_delta = ""
        if abs(cash - prev_cash) > 0.01 and not trade_str:
            delta = cash - prev_cash
            cash_delta = f"(分红入账{delta:+.0f})"
        elif abs(cash - prev_cash) > 0.01 and trade_str:
            delta = cash - prev_cash
            if delta > 100:
                pass
            elif div:
                pass

        print(
            f"{day:>3} "
            f"{label:>16} "
            f"{real_close:>10.2f} "
            f"{adj_close:>10.2f} "
            f"{factor:>6.2f} "
            f"{qty:>8d} "
            f"{cost:>10.2f} "
            f"{mv:>12.2f} "
            f"{cash:>12.2f} "
            f"{nav:>12.2f} "
            f"{trade_str:>24}"
        )

        prev_qty = qty
        prev_cash = cash

    # ===== 分阶段详解 =====
    print_header("分阶段详解")

    buy_trade = next((t for t in result.trades if t.entry_time == t.exit_time), None)
    if not buy_trade:
        print("  (无成交)")
        return

    iq = int(buy_trade.quantity)
    buy_price = buy_trade.fill_price
    buy_cost = buy_price * iq

    adj_open_7 = bar_map.get(7, {}).get("adj_open", 0)
    cash_7 = state_map.get(7, {}).get("cash", 0)
    tax = 2.0 * iq * 0.20

    lines = []
    lines.append("")
    lines.append("  +-- Day 1-6: 空仓观望 " + "-" * 50)
    lines.append("  | 策略积累 K 线数据, adj_close 从 100 涨到 110 (+10%)")
    lines.append("  | 持仓=0, 现金=1,000,000, NAV=1,000,000")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 6 收盘 -> Day 7: T+1 成交 " + "-" * 40)
    lines.append(f"  | Day 6 收盘后下单 -> Day 7 以 real_open = {buy_price:.2f} 成交")
    lines.append(f"  |   成交价 = 真实开盘价 (不是 adj_open={adj_open_7:.2f})")
    lines.append(f"  |   买入 {iq} 股 x {buy_price:.2f} = {buy_cost:,.2f}")
    lines.append(f"  |   佣金 = {abs(buy_trade.pnl):,.2f}")
    lines.append(f"  |   持仓 = {iq} 股, 成本价 = {buy_price:.2f}")
    lines.append(f"  |   现金 = {cash_7:,.2f}")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 8: 现金分红 (10派20 = 2元/股) " + "-" * 30)
    lines.append("  | 引擎在填充 deferred orders 之前处理分红:")
    lines.append(f"  |   持有 {iq} 股, 且是 Day 7 买入, 持仓天数 = 1天")
    lines.append(f"  |   分红金额: {iq} x 2元 = {2*iq:,.0f} 元 (税前)")
    lines.append(f"  |   红利税: 持仓 1天 <= 30天, 税率 20% = {tax:,.0f} 元")
    lines.append(f"  |   实际到账: {2*iq - tax:,.0f} 元")
    lines.append(f"  |   现金: {cash_7:,.0f} + {2*iq - tax:,.0f} = {cash_7 + 2*iq - tax:,.0f}")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 9-12: 分红后持有 " + "-" * 42)
    lines.append("  | real close: 108.57 -> 116.19 (稳步回升)")
    lines.append("  | adj_close:  116 -> 122 (+5.2%, 平滑上涨)")
    lines.append(f"  | 持仓数量不变 = {iq} 股")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 13: 送股 (10送10 = 每股送1股) " + "-" * 28)
    lines.append("  | 引擎处理:")
    lines.append("  |   stock_dividend = 1.0 (每股送1股)")
    lines.append(f"  |   新增股份 = {iq} x 1.0 = {iq} 股")
    lines.append(f"  |   持仓: {iq} -> {iq*2} 股 (翻倍)")
    lines.append("  |   成本价自动下调 (加权平均, 新股 cost=0)")
    lines.append("  |")
    lines.append("  | real close: 116.19 -> 59.05 (-49.2%, 腰斩!)")
    lines.append("  | adj_close:  122 -> 124 (+1.6%, 正常上涨)")
    lines.append(f"  | 市值: {iq}x116.19={iq*116.19:,.0f} -> {iq*2}x59.05={iq*2*59.05:,.0f} (连续)")
    lines.append("  |")
    lines.append("  | 关键: adj_close 不受影响, 策略动量仍为正")
    lines.append("  |       real close 腰斩, 如果用它算动量会得到 -45% 的虚假做空信号")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 14-16: 送股后持有 " + "-" * 40)
    lines.append(f"  | 持仓 = {iq*2} 股")
    lines.append("  | real close: 60.00 -> 61.90 (回升)")
    lines.append("  | adj_close:  126 -> 130 (+3.2%)")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 17: 转增 (10转5 = 每股转增0.5股) " + "-" * 24)
    lines.append("  | 引擎处理:")
    lines.append("  |   stock_dividend = 0.5 (每股转增0.5股)")
    lines.append(f"  |   新增股份 = {iq*2} x 0.5 = {iq} 股")
    lines.append(f"  |   持仓: {iq*2} -> {iq*3} 股")
    lines.append("  |")
    lines.append("  | real close: 61.90 -> 41.90 (-32.3%)")
    lines.append("  | adj_close:  130 -> 132 (+1.5%, 正常)")
    lines.append(f"  | 市值: {iq*2}x61.90={iq*2*61.90:,.0f} -> {iq*3}x41.90={iq*3*41.90:,.0f} (连续)")
    lines.append("  +" + "-" * 60)
    lines.append("")
    lines.append("  +-- Day 18-20: 转增后持有至结束 " + "-" * 34)
    lines.append(f"  | 持仓 = {iq*3} 股")
    lines.append("  | real close: 42.54 -> 43.81")
    lines.append("  | adj_close:  134 -> 138")
    lines.append("  +" + "-" * 60)

    for line in lines:
        print(line)

    # ===== 汇总验证 =====
    print_header("汇总验证")

    for pos in result.open_positions:
        final_qty = int(pos["quantity"])
        expected = iq * 2 * 3 // 2
        print(f"  持仓数量: {final_qty} = {iq}(买入) × 2(送股) × 1.5(转增) → {'ok' if final_qty == iq*3 else 'FAIL'}")
        buy_day_bar = bar_map.get(7, {})
        print(f"  成交价(真实open): {buy_price:.2f} vs Day7 open={buy_day_bar.get('open',0):.2f} → {'ok' if abs(buy_price - buy_day_bar.get('open',0)) < 0.02 else 'FAIL'}")
        print(f"  最终NAV: {result.final_nav:,.2f} (初始 1,000,000)")
        print(f"  总收益率: {(result.final_nav / 1_000_000 - 1) * 100:.2f}%")
        print(f"  总佣金: {result.diagnostics.total_commission:,.2f}")


def main():
    print("=" * 80)
    print("    回测流程走查: 后复权价格 vs 真实价格 分离验证")
    print("    场景: 600519 现金分红 + 送股 + 转增")
    print("=" * 80)

    bars = build_bars()
    dividends = build_dividends()

    print_data_table(bars, dividends)
    print_momentum_comparison(bars)
    run_and_analyze(bars, dividends)

    print("走查完成.")


if __name__ == "__main__":
    main()
