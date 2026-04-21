# Cross-Sectional Mean Reversion Strategy

## Strategy Overview

| Attribute | Value |
|-----------|-------|
| **Type** | Cross-sectional mean reversion (long/short) |
| **Asset Class** | US Equities (large caps) |
| **Trading Frequency** | Daily rebalancing (5-day holding period) |
| **Expected Sharpe (OOS)** | 0.5 - 0.9 |
| **Max Drawdown** | 20 - 30% |
| **Market Regime** | Range-bound / reversal |
| **Capacity** | $5M - $50M |
| **Turnover** | Moderate (~12 rebalances/year) |

## Strategy Logic

Stocks that have underperformed the market over the past N days tend to revert to the mean. This strategy:
- Longs the most underperforming stocks (bottom decile)
- Shorts the most overperforming stocks (top decile)
- Equal weight per leg for market neutrality

Hypothesis: Short-term reversal effect - excess returns mean-revert over 5-day windows.

## Applicable Scenarios

- **Works best**: Range-bound markets with no strong directional trend
- **Works best**: Post-earnings moves that overextend
- **Fails in**: Strong trending markets where losers keep losing
- **Fails in**: High volatility regimes with persistent trends

## Backtest Results

| Metric | Value |
|--------|-------|
| Sharpe (OOS) | TBD |
| Max Drawdown | TBD |
| CAGR | TBD |
| Win Rate | TBD |

## Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `symbols` | [SPY, QQQ, AAPL, MSFT, ...] | - | Trading universe |
| `market_symbol` | SPY | - | Benchmark for excess return |
| `lookback_days` | 5 | 3-20 | Days to calculate excess return |
| `holding_days` | 5 | 1-21 | Days to hold positions |
| `top_pct` | 0.1 | 0.05-0.2 | Fraction to short |
| `bottom_pct` | 0.1 | 0.05-0.2 | Fraction to long |
| `max_position_pct` | 0.05 | 0.02-0.10 | Max NAV per position |
