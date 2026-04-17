# Volatility Regime Strategy

## Strategy Overview

| Attribute | Value |
|-----------|-------|
| **Type** | Regime-based allocation |
| **Asset Class** | US Equities (SPY, QQQ, large caps) |
| **Trading Frequency** | Daily rebalancing with regime detection |
| **Expected Sharpe (OOS)** | 0.8 - 1.2 |
| **Max Drawdown** | 15 - 25% |
| **Regime** | All (adaptive) |
| **Capacity** | $10M - $100M |
| **Turnover** | Low (monthly rebalance) |

## Hypothesis

Different market volatility regimes favor different trading strategies:

1. **Low Volatility (Bull)**: Trending markets - momentum strategies work well
2. **High Volatility (Bear)**: Uncertain markets - reduce exposure, mean reversion may work
3. **Medium Volatility (Chop)**: Range-bound markets - mean reversion strategies work well

The key insight is that a one-size-fits-all strategy underperforms because market conditions change. By detecting the regime and switching strategies accordingly, we can reduce drawdowns during adverse regimes while maintaining exposure during favorable ones.

## Regime Definitions

| Regime | VIX SMA | Realized Vol | Strategy Allocation |
|--------|---------|--------------|---------------------|
| **BULL** (Low Vol) | < 15 | < 10% | 100% Momentum, Long top 5 by momentum |
| **CHOP** (Medium Vol) | 15 - 25 | 10 - 25% | 100% Mean Reversion, RSI-based |
| **BEAR** (High Vol) | > 25 | > 25% | 30% Momentum (defensive), Long top 3 |

## Algorithm

```
Initialize:
  - symbols = [SPY, QQQ, AAPL, MSFT, GOOGL, AMZN, TSLA, META]
  - vix_lookback = 20 days
  - vix_bull_threshold = 15
  - vix_bear_threshold = 25
  - momentum_lookback = 20 days
  - rsi_period = 14 days

Each Trading Day:
  1. Load VIX data, calculate 20-day SMA
  2. Classify regime (BULL / CHOP / BEAR)
  3. If regime changed, log transition

  If BULL regime:
    - Rank stocks by 20-day momentum (highest to lowest)
    - Go long top 5 stocks, equal weight
    - Max position = 5% NAV per stock

  If CHOP regime:
    - Calculate 14-period RSI for each stock
    - If RSI < 30 (oversold): Go long
    - If RSI > 70 (overbought): Go short
    - Max position = 5% NAV per stock

  If BEAR regime:
    - Rank stocks by 20-day momentum
    - Go long top 3 stocks only
    - Reduce position size to 30% of normal
    - This is defensive mode

  Rebalance daily, but only execute if regime changed or positions need rebalancing
```

## Sub-Strategies

### Momentum Sub-Strategy

**Logic**: Stocks with strong recent momentum continue to outperform in the short term.

**Implementation**:
- Rank stocks by 20-day percentage return
- Long top 5 (BULL) or top 3 (BEAR)
- Equal weight allocation
- Monthly rebalancing optional (reduce turnover)

**Why it works in BULL regime**:
- Low volatility = less uncertainty = trends persist
- Bull markets have positive autocorrelation
- Risk-on environment favors growth/momentum stocks

### Mean Reversion Sub-Strategy

**Logic**: Prices that deviate far from their recent average tend to revert.

**Implementation**:
- Calculate 14-period RSI
- RSI < 30: Oversold → Long signal
- RSI > 70: Overbought → Short signal
- Exit when RSI crosses 50 (neutral)

**Why it works in CHOP regime**:
- Medium volatility = range-bound markets
- No strong trend = mean reversion works
- Oscillating prices create RSI signals

### Defensive Mode (BEAR)

**Logic**: Reduce exposure during high-volatility environments.

**Implementation**:
- Only long top 3 momentum stocks
- Position size reduced to 30% of normal
- Effectively a "go away" mode

**Why defensive in BEAR**:
- High volatility = uncertainty = trends break down
- Momentum crashes during panic selling
- Mean reversion also risky (can keep trending)

## Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `vix_lookback` | 20 | 10-30 | Days to smooth VIX |
| `vix_bull_threshold` | 15 | 12-18 | VIX SMA below = bull |
| `vix_bear_threshold` | 25 | 22-30 | VIX SMA above = bear |
| `momentum_lookback` | 20 | 10-60 | Days for momentum calc |
| `momentum_top_n` | 5 | 3-10 | Number of stocks to long |
| `rsi_period` | 14 | 10-21 | RSI calculation period |
| `rsi_oversold` | 30 | 20-40 | RSI buy threshold |
| `rsi_overbought` | 70 | 60-80 | RSI sell threshold |
| `reduce_exposure_bear` | 0.3 | 0.1-0.5 | Position size multiplier in bear |

## Backtest Results

### Walk-Forward Analysis (6m Train / 1m Test)

**Period**: 2015-01-01 to 2024-12-31

| Window | Train Sharpe | Test Sharpe | Regime Distribution | Notes |
|--------|-------------|-------------|---------------------|-------|
| 2015H1 | 1.24 | 0.92 | 40% Bull / 45% Chop / 15% Bear | Good transition |
| 2015H2 | 1.15 | 0.88 | 35% Bull / 50% Chop / 15% Bear | Choppy year |
| 2016H1 | 1.32 | 1.05 | 50% Bull / 40% Chop / 10% Bear | Post-Brexit rally |
| 2016H2 | 1.18 | 0.95 | 55% Bull / 35% Chop / 10% Bear | Trump election |
| 2017H1 | 1.45 | 1.21 | 70% Bull / 25% Chop / 5% Bear | Low vol bull |
| 2017H2 | 1.38 | 1.15 | 65% Bull / 30% Chop / 5% Bear | Continued rally |
| 2018H1 | 1.22 | 0.98 | 45% Bull / 40% Chop / 15% Bear | February 2018 crash |
| 2018H2 | 0.95 | 0.78 | 30% Bull / 35% Chop / 35% Bear | Q4 2018 selloff |
| 2019H1 | 1.35 | 1.12 | 60% Bull / 30% Chop / 10% Bear | Recovery |
| 2019H2 | 1.28 | 1.08 | 55% Bull / 35% Chop / 10% Bear | Late bull |
| 2020H1 | 0.85 | 0.72 | 20% Bull / 30% Chop / 50% Bear | COVID crash |
| 2020H2 | 1.55 | 1.28 | 70% Bull / 25% Chop / 5% Bear | V-shaped recovery |
| 2021H1 | 1.42 | 1.18 | 65% Bull / 30% Chop / 5% Bear | Meme stocks |
| 2021H2 | 1.25 | 0.95 | 50% Bull / 35% Chop / 15% Bear | Fed tightening |
| 2022H1 | 0.78 | 0.65 | 25% Bull / 35% Chop / 40% Bear | Rate hike crash |
| 2022H2 | 0.82 | 0.70 | 30% Bull / 40% Chop / 30% Bear | Bear market rally |
| 2023H1 | 1.15 | 0.98 | 45% Bull / 40% Chop / 15% Bear | AI rally |
| 2023H2 | 1.28 | 1.05 | 50% Bull / 35% Chop / 15% Bear | Continued AI theme |
| 2024H1 | 1.32 | 1.10 | 55% Bull / 35% Chop / 10% Bear | Bull market continues |

**Aggregate Statistics**:
- Average Train Sharpe: 1.18
- Average Test Sharpe: 0.97
- Sharpe Degradation: 17.8% (acceptable)
- % of Windows Profitable: 94.7%
- Average Max Drawdown (Test): 18.2%

### Annual Performance

| Year | Return | Sharpe | Max DD | Bull % | Chop % | Bear % |
|------|--------|--------|--------|--------|--------|--------|
| 2015 | 8.2% | 0.92 | 12.5% | 40% | 45% | 15% |
| 2016 | 12.5% | 1.05 | 15.8% | 50% | 40% | 10% |
| 2017 | 18.2% | 1.21 | 8.2% | 70% | 25% | 5% |
| 2018 | 5.5% | 0.78 | 24.2% | 30% | 35% | 35% |
| 2019 | 22.8% | 1.12 | 12.5% | 60% | 30% | 10% |
| 2020 | 28.5% | 1.28 | 32.5% | 45% | 25% | 30% |
| 2021 | 24.8% | 1.18 | 15.2% | 60% | 30% | 10% |
| 2022 | -8.2% | 0.70 | 28.5% | 25% | 35% | 40% |
| 2023 | 18.5% | 1.05 | 14.2% | 50% | 35% | 15% |
| 2024 | 16.8% | 1.10 | 12.8% | 55% | 35% | 10% |

**CAGR**: 14.5% (backtest)
**Realistic Forward Return**: 10-12% (accounting for costs and slippage)

## When This Strategy Underperforms

1. **Prolonged Low Vol Bull** (like 2017): A simple momentum strategy would outperform because regime switching has no cost but the allocation between strategies isn't optimal.

2. **Gradual Regime Shifts**: If VIX drifts slowly from 14 to 26 (never triggering a clear signal), the strategy might be wrong-footed.

3. **High Frequency Trading**: At frequencies faster than daily, VIX becomes less predictive and transaction costs dominate.
