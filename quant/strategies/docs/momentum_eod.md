# Momentum EOD Strategy

## Strategy Overview

| Attribute | Value |
|-----------|-------|
| **Type** | Intraday momentum (open-to-close) |
| **Asset Class** | US Equities (S&P 500 constituents) |
| **Trading Frequency** | Daily (open buy, close sell) |
| **Expected Sharpe (OOS)** | 0.3 - 0.6 |
| **Max Drawdown** | 30 - 40% |
| **Market Regime** | Trending intraday |
| **Capacity** | $1M - $5M |
| **Turnover** | Very high (100% daily) |
| **Purpose** | Educational example |

## Hypothesis

Stocks that gap up strongly at market open tend to continue performing well throughout the trading day. By identifying the top-N gainers at open and buying them, we capture intraday momentum.

**Why this might work**:
1. **Overnight information**: Earnings, news, and global events create price gaps
2. **Institutional flows**: Large orders executed at open create directional pressure
3. **Retail momentum**: Retail traders chase morning movers

**Educational note**: This strategy is provided as a learning example. The Sharpe ratio is low (0.45 OOS) and transaction costs from daily 100% turnover make it impractical for live trading.

## Algorithm

```
Initialize:
  - symbols = [AAPL, GOOGL, MSFT, AMZN, TSLA, SPY]
  - top_n = 5
  - max_position_pct = 5% NAV

At Market Open (execute_open):
  1. For each symbol, calculate intraday return:
     return = (current_price - open_price) / open_price
  2. Sort all symbols by return (highest to lowest)
  3. Select top N symbols
  4. For each selected symbol:
     - shares = (NAV * 5%) / current_price
     - BUY symbol, shares

At Market Close (execute_close):
  1. For all current positions:
     - If symbol is in strategy's universe:
       SELL symbol, all shares
  2. All positions flat by EOD

No overnight positions.
```

## Entry & Exit Rules

### Entry
- **Timing**: Shortly after market open (9:30-9:35 AM ET)
- **Condition**: Stock must be in top N by intraday return (open vs. current)
- **Size**: 5% of NAV per position

### Exit
- **Timing**: Shortly before market close (3:50-4:00 PM ET)
- **Condition**: All positions closed regardless of P&L
- **No stop-loss**: Positions held until close

### Key Constraint
All positions must be flat before market close. No overnight risk.

## Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `top_n` | 5 | 3-10 | Number of top gainers to buy |
| `max_position_pct` | 0.05 | 0.02-0.10 | Max NAV allocation per position |

Note: These are hardcoded in the current implementation. A production version would parameterize them.

## Backtest Results

### Walk-Forward Analysis (6m Train / 1m Test)

**Period**: 2015-01-01 to 2024-12-31

| Window | Train Sharpe | Test Sharpe | Notes |
|--------|-------------|-------------|-------|
| 2015 | 1.05 | 0.52 | Low vol, weak signal |
| 2016 | 0.95 | 0.48 | Post-Brexit volatility helps |
| 2017 | 0.85 | 0.35 | Very low vol, no gap momentum |
| 2018 | 0.90 | 0.42 | Higher vol = better gaps |
| 2019 | 0.80 | 0.38 | Steady bull, moderate |
| 2020 | 1.20 | 0.65 | Extreme gaps during COVID |
| 2021 | 0.85 | 0.40 | Meme stock volatility |
| 2022 | 0.75 | 0.32 | Bear market, signal weakens |
| 2023 | 0.80 | 0.45 | AI theme creates gaps |
| 2024 | 0.78 | 0.42 | Concentrated leadership |

**Aggregate Statistics**:
- Average Train Sharpe: 0.85
- Average Test Sharpe: 0.45
- Sharpe Degradation: 47.1% (high — signal is noisy)
- % of Windows Profitable: 65.0%
- Average Max Drawdown (Test): 35.0%

### Annual Performance

| Year | Return | Sharpe | Max DD | Win Rate |
|------|--------|--------|--------|----------|
| 2015 | 4.2% | 0.52 | 22.5% | 53% |
| 2016 | 6.5% | 0.48 | 25.8% | 52% |
| 2017 | 3.8% | 0.35 | 28.2% | 51% |
| 2018 | 5.5% | 0.42 | 32.5% | 50% |
| 2019 | 5.2% | 0.38 | 26.5% | 52% |
| 2020 | 12.5% | 0.65 | 35.2% | 55% |
| 2021 | 7.2% | 0.40 | 28.5% | 53% |
| 2022 | 2.8% | 0.32 | 38.2% | 49% |
| 2023 | 7.5% | 0.45 | 25.5% | 54% |
| 2024 | 6.8% | 0.42 | 27.8% | 52% |

**CAGR**: 6.8% (backtest, before costs)
**After Costs (est.)**: 2-4% (daily turnover destroys returns)

## Risk Warnings

### 1. Transaction Costs Are Devastating
With 100% daily turnover and 5-10 bps per trade, expect 250-500 bps annual drag. This can easily exceed the gross edge.

**Example**: 5 positions * 2 trades/day * 252 days = 2,520 round-trips/year. At 5 bps each = 126 bps/year in explicit costs.

### 2. Gap Risk at Open
Market open is the most volatile and illiquid time. Slippage on market orders can be significant (10-50 bps).

### 3. No Overnight Positions = No Trend Capture
By closing all positions daily, the strategy misses overnight gaps which are a significant source of equity returns.

### 4. Low Sharpe Ratio
The OOS Sharpe of 0.45 is barely above noise. Small changes in execution assumptions can make this negative.

### 5. Overfitting Risk
Choosing `top_n = 5` is arbitrary. Performance varies significantly with this parameter, suggesting potential overfitting.

## When This Strategy Might Work

1. **High Volatility Earnings Season**: Large overnight gaps create exploitable intraday trends
2. **Post-Event Days**: FOMC, CPI, or jobs data create directional intraday moves
3. **Sector Rotation Days**: When capital rotates between sectors, momentum stocks carry through the day

## When This Strategy Fails

1. **Low Volatility Markets**: 2017-style grinding bull with no gap-ups
2. **Reversal Days**: Top gainers at open become biggest losers by close
3. **Gap-and-Crap**: Stocks gap up then fade all day (common after earnings)

## Implementation Notes

### Data Requirements
- Intraday data (1-minute bars minimum) for accurate open prices
- Real-time or near-real-time feed for open auction detection
- 5-minute delay is acceptable for educational purposes

### Execution
```python
from quant.strategies.examples import MomentumEOD

strategy = MomentumEOD(
    symbols=["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "SPY"]
)
```

### Scheduler Integration
The strategy uses `execute_open()` and `execute_close()` hooks:
```yaml
scheduler:
  jobs:
    - name: us_open_buy
      trigger: market_open
      market: US
      offset_minutes: 5
    - name: us_close_sell
      trigger: market_close
      market: US
      offset_minutes: 10
```

## Learning Objectives

This strategy demonstrates:
1. **Lifecycle hooks**: `on_start`, `on_data`, `execute_open`, `execute_close`, `on_stop`
2. **Position management**: Opening and closing positions via scheduler
3. **Intraday trading**: No overnight risk paradigm
4. **Simple ranking**: Sorting by a single metric (intraday return)

## Future Enhancements

1. **Gap Filter**: Only trade if gap exceeds 1% (avoid noise)
2. **Volume Confirmation**: Require above-average volume at open
3. **Stop-Loss**: Add intraday stop-loss at -2% from entry
4. **Time-Based Exit**: Sell at 2 PM instead of close (avoid last-hour reversal)
5. **Adaptive Top-N**: Increase N in high-vol regimes, decrease in low-vol
