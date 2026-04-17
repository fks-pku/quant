# Dual Momentum Strategy

## Strategy Overview

| Attribute | Value |
|-----------|-------|
| **Type** | Dual momentum (absolute + relative) |
| **Asset Class** | US Equities (large caps) |
| **Trading Frequency** | Monthly rebalancing (21-day holding) |
| **Expected Sharpe (OOS)** | 0.7 - 1.1 |
| **Max Drawdown** | 20 - 30% |
| **Market Regime** | Trending |
| **Capacity** | $5M - $50M |
| **Turnover** | Monthly (~12 rebalances/year) |

## Strategy Logic

Combines absolute momentum (trend following via SMA) with relative momentum (cross-sectional ranking). Only takes trades when both agree.

- **Absolute momentum**: Price > SMA(60) indicates bullish trend
- **Relative momentum**: Rank stocks by 20-day return, go long top tercile
- **Dual confirmation**: Only enter when BOTH absolute and relative momentum signal bullish

Hypothesis: Dual confirmation filters false signals and improves risk-adjusted returns.

## Applicable Scenarios

- **Works best**: Trending markets with clear leadership
- **Works best**: Bull markets with broad participation
- **Fails in**: Choppy markets with no clear trend
- **Fails in**: Bear markets where even top-ranked stocks decline

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
| `abs_lookback` | 60 | 30-120 | Days for absolute momentum SMA |
| `rel_lookback` | 20 | 10-60 | Days for relative momentum |
| `sma_short` | 20 | 10-30 | Short SMA for trend detection |
| `holding_days` | 21 | 5-63 | Days between rebalances |
| `top_tercile_pct` | 0.33 | 0.2-0.4 | Fraction to long |
| `bottom_tercile_pct` | 0.33 | 0.2-0.4 | Fraction to short |
| `max_position_pct` | 0.05 | 0.02-0.10 | Max NAV per position |
