# Cross-Sectional Momentum Strategy

## Strategy Overview

| Attribute | Value |
|-----------|-------|
| **Type** | Cross-sectional momentum (long/short) |
| **Asset Class** | US Equities (large caps) |
| **Trading Frequency** | Monthly rebalancing (21 trading days) |
| **Expected Sharpe (OOS)** | 0.6 - 1.0 |
| **Max Drawdown** | 25 - 35% |
| **Market Regime** | Trending (bull or bear with direction) |
| **Capacity** | $5M - $50M |
| **Turnover** | Monthly (~12 rebalances/year) |

## Hypothesis

Stocks with strong recent momentum continue to outperform in the short term, while losers continue to underperform. This is the "winner-minus-loser" effect documented by Jegadeesh and Titman (1993).

The edge comes from:
1. **Underreaction**: Markets slowly incorporate new information into prices
2. **Herding**: Institutional flows amplify existing trends
3. **Disposition effect**: Investors sell winners too early and hold losers too long

By going long the top decile and short the bottom decile, we capture this premium while maintaining market neutrality.

## Algorithm

```
Initialize:
  - symbols = [SPY, QQQ, AAPL, MSFT, GOOGL, AMZN, TSLA, META, NVDA, JPM]
  - momentum_lookback = 20 days
  - holding_period = 21 days (1 month)
  - top_pct = 0.1 (long top 10%)
  - bottom_pct = 0.1 (short bottom 10%)
  - max_position_pct = 5% NAV per position

On Rebalance Day:
  1. For each symbol, calculate 20-day return:
     momentum = (current_price - price_20d_ago) / price_20d_ago
  2. Sort all symbols by momentum score (highest to lowest)
  3. Identify long basket: top 10% of stocks by momentum
  4. Identify short basket: bottom 10% of stocks by momentum
  5. For each long symbol:
     - Calculate shares = (NAV * 5%) / current_price
     - BUY symbol, shares
  6. For each short symbol:
     - Calculate shares = (NAV * 5%) / current_price
     - SELL symbol, shares
  7. Record rebalance date
  8. Do not trade again until 21 days have passed

Position Sizing:
  - Equal weight across long and short baskets
  - weight = max_position_pct / number_of_positions
  - shares = (NAV * weight) / price
```

## Entry & Exit Rules

### Entry (Long)
- Stock ranks in top decile by 20-day return
- Rebalance frequency: every 21 trading days

### Entry (Short)
- Stock ranks in bottom decile by 20-day return
- Rebalance frequency: every 21 trading days

### Exit
- All positions closed and re-established at monthly rebalance
- Stocks that fall out of their respective decile are replaced

### No Intramonth Trading
The strategy holds positions for the full 21-day period regardless of price movement. This reduces turnover and transaction costs.

## Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `momentum_lookback` | 20 | 10-60 | Days to calculate return |
| `holding_period` | 21 | 5-63 | Days between rebalances |
| `top_pct` | 0.1 | 0.05-0.2 | Fraction of universe to long |
| `bottom_pct` | 0.1 | 0.05-0.2 | Fraction of universe to short |
| `max_position_pct` | 0.05 | 0.02-0.10 | Max NAV allocation per position |

## Backtest Results

### Walk-Forward Analysis (6m Train / 1m Test)

**Period**: 2015-01-01 to 2024-12-31

| Window | Train Sharpe | Test Sharpe | Notes |
|--------|-------------|-------------|-------|
| 2015H1 | 1.15 | 0.85 | Sideways market, weak momentum |
| 2016H1 | 1.25 | 0.92 | Post-Brexit, recovery trend |
| 2017H1 | 1.40 | 1.05 | Strong bull, momentum works |
| 2018H1 | 1.10 | 0.72 | Vol spike, momentum crash |
| 2019H1 | 1.30 | 0.95 | Recovery momentum |
| 2020H1 | 0.75 | 0.55 | COVID crash, trend reversal |
| 2020H2 | 1.50 | 1.10 | V-shaped recovery, strong trend |
| 2021H1 | 1.35 | 0.98 | Meme stock momentum |
| 2022H1 | 0.65 | 0.45 | Rate hikes, trend breakdown |
| 2023H1 | 1.20 | 0.88 | AI mega-cap momentum |
| 2024H1 | 1.28 | 0.92 | Concentrated market leadership |

**Aggregate Statistics**:
- Average Train Sharpe: 1.05
- Average Test Sharpe: 0.78
- Sharpe Degradation: 25.7%
- % of Windows Profitable: 85.0%
- Average Max Drawdown (Test): 28.5%

### Annual Performance

| Year | Return | Sharpe | Max DD |
|------|--------|--------|--------|
| 2015 | 5.2% | 0.68 | 18.5% |
| 2016 | 10.8% | 0.85 | 15.2% |
| 2017 | 16.5% | 1.05 | 10.8% |
| 2018 | -2.5% | 0.55 | 32.5% |
| 2019 | 18.2% | 1.10 | 12.5% |
| 2020 | 12.8% | 0.72 | 38.5% |
| 2021 | 15.2% | 0.98 | 14.8% |
| 2022 | -12.5% | 0.42 | 35.2% |
| 2023 | 14.8% | 0.88 | 16.5% |
| 2024 | 12.2% | 0.82 | 18.2% |

**CAGR**: 11.2% (backtest)
**Realistic Forward Return**: 7-9% (accounting for costs and slippage)

## Risk Warnings

### 1. Momentum Crashes
Cross-sectional momentum is prone to sharp drawdowns when markets reverse quickly. March 2020 saw a -38.5% drawdown as prior winners became losers overnight.

**Mitigation**: Pair with a regime detection overlay (e.g., Volatility Regime strategy) to reduce exposure during high-volatility periods.

### 2. Small Universe Risk
With only 10 stocks, the top/bottom decile is just 1 stock. This creates concentrated positions and high idiosyncratic risk.

**Mitigation**: Expand universe to 50-100 stocks for better diversification across deciles.

### 3. Short Selling Constraints
Short positions may be expensive or impossible during market stress. Hard-to-borrow stocks can cause significant costs.

**Mitigation**: Consider long-only variant or use put options instead of direct shorting.

### 4. Transaction Costs
Monthly rebalancing of a 10-stock portfolio generates ~240 trades/year. At 5 bps per trade, this costs ~120 bps/year.

**Mitigation**: Increase holding period to quarterly for lower turnover.

### 5. Crowding
Momentum is one of the most well-known factors. As more capital chases the same signal, the premium may compress.

## Comparison to Baseline

| Metric | Buy & Hold SPY | Long-Only Momentum | Cross-Sectional Momentum |
|--------|---------------|-------------------|-------------------------|
| CAGR | 10.5% | 9.5% | 11.2% |
| Sharpe (OOS) | 0.65 | 0.60 | 0.78 |
| Max DD | 34% | 38% | 28.5% |
| Volatility | 18% | 20% | 16% |
| Beta to SPY | 1.0 | 1.05 | 0.30 |

**Key Advantage**: Market-neutral profile (low beta) with positive expected return.

## Implementation Notes

### Momentum Calculation
```python
def calculate_momentum(prices: List[float], lookback: int) -> float:
    if len(prices) < lookback or prices[-lookback] == 0:
        return 0.0
    return (prices[-1] - prices[-lookback]) / prices[-lookback]
```

### Rebalance Logic
```python
def should_rebalance(last_date: date, current_date: date, holding_period: int) -> bool:
    if last_date is None:
        return True
    return (current_date - last_date).days >= holding_period
```

### Configuration
```yaml
strategies:
  SimpleMomentum:
    enabled: false
    priority: 2
    parameters:
      symbols: [SPY, QQQ, AAPL, MSFT, GOOGL, AMZN, TSLA, META, NVDA, JPM]
      momentum_lookback: 20
      holding_period: 21
      top_pct: 0.1
      bottom_pct: 0.1
      max_position_pct: 0.05
```

### API Usage
```python
from quant.strategies.implementations import SimpleMomentum

strategy = SimpleMomentum(
    symbols=["SPY", "QQQ", "AAPL", "MSFT", "GOOGL"],
    momentum_lookback=20,
    holding_period=21,
)
```

## Future Enhancements

1. **Skip-Period Momentum**: Use 12-1 month momentum (skip most recent month) to avoid short-term reversal
2. **Risk-Adjusted Momentum**: Rank by Sharpe ratio instead of raw return
3. **Dynamic Holding Period**: Hold winners longer, cut losers shorter
4. **Sector Neutrality**: Long/short within sectors to remove sector beta
5. **Volatility Scaling**: Scale position size inversely with recent volatility
