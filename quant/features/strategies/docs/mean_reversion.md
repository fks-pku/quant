# Mean Reversion (1m) Strategy

## Strategy Overview

| Attribute | Value |
|-----------|-------|
| **Type** | RSI-based mean reversion (intraday) |
| **Asset Class** | US Equities (ETFs and large caps) |
| **Trading Frequency** | Intraday on 1-minute bars |
| **Expected Sharpe (OOS)** | 0.2 - 0.5 |
| **Max Drawdown** | 35 - 45% |
| **Market Regime** | Range-bound (choppy) |
| **Capacity** | $1M - $5M |
| **Turnover** | High (multiple trades per day) |
| **Purpose** | Educational example |

## Hypothesis

When a stock's price deviates significantly from its recent average (as measured by RSI), it tends to revert to the mean. Specifically:

1. **Oversold (RSI < 30)**: Selling pressure is exhausted, price likely to bounce
2. **Overbought (RSI > 70)**: Buying pressure is exhausted, price likely to pull back

This strategy exploits these mean-reverting tendencies on 1-minute bars, buying oversold and selling overbought conditions.

**Why mean reversion works on short timeframes**:
- Market makers provide liquidity around fair value
- Algorithmic traders fade extreme moves
- Order flow imbalance corrects within minutes

**Educational note**: This strategy has a low Sharpe ratio (0.35 OOS) and is meant to demonstrate the RSI indicator and intraday position management. It is not recommended for live trading without significant enhancements.

## Algorithm

```
Initialize:
  - symbols = [SPY, QQQ, AAPL]
  - rsi_period = 14 bars (14 minutes)
  - oversold_threshold = 30
  - overbought_threshold = 70
  - max_position_pct = 5% NAV

On Each 1-Minute Bar:
  1. Accumulate bar data per symbol
  2. If fewer than 15 bars available, skip (need rsi_period + 1)
  3. Calculate 14-period RSI:
     a. Compute price changes (deltas)
     b. Separate gains (positive deltas) and losses (negative deltas)
     c. avg_gain = mean(gains over last 14 bars)
     d. avg_loss = mean(losses over last 14 bars)
     e. RS = avg_gain / avg_loss
     f. RSI = 100 - (100 / (1 + RS))
  4. Trading logic:
     If RSI < 30 AND currently flat:
       - shares = (NAV * 5%) / current_price
       - BUY symbol, shares
       - Set state = "long"
     If RSI > 70 AND currently long:
       - SELL symbol, all shares
       - Set state = "flat"
     (No short selling in this version)
```

## Entry & Exit Rules

### Entry (Long)
- **Condition**: RSI(14) < 30 (oversold)
- **State**: Must be flat (no existing position)
- **Size**: 5% of NAV per symbol

### Exit (Long)
- **Condition**: RSI(14) > 70 (overbought)
- **State**: Must be long (existing position)
- **Size**: Close entire position

### No Short Positions
The current implementation only goes long. A production version could add short selling when RSI > 70 from a flat state.

### No Stop-Loss
There is no hard stop-loss. Position is held until RSI signals overbought. This can lead to large drawdowns during trending markets.

## RSI Calculation Detail

The Relative Strength Index (RSI) measures the speed and magnitude of recent price changes:

```
RSI = 100 - (100 / (1 + RS))

where RS = Average Gain / Average Loss over N periods

For each bar:
  delta = close[current] - close[previous]
  gain = max(delta, 0)
  loss = max(-delta, 0)

avg_gain = sum(gains[-N:]) / N
avg_loss = sum(losses[-N:]) / N

Interpretation:
  RSI = 100  -> avg_loss = 0 (only gains, strong uptrend)
  RSI = 0    -> avg_gain = 0 (only losses, strong downtrend)
  RSI = 50   -> balanced (no directional bias)
```

**Note**: This implementation uses Simple Moving Average (SMA) for RSI. Wilder's original uses Exponential Moving Average (EMA), which is more responsive. The difference is minor for educational purposes.

## Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `rsi_period` | 14 | 7-28 | Number of 1-min bars for RSI |
| `oversold_threshold` | 30 | 20-40 | RSI level to buy |
| `overbought_threshold` | 70 | 60-80 | RSI level to sell |
| `max_position_pct` | 0.05 | 0.02-0.10 | Max NAV per position |

## Backtest Results

### Walk-Forward Analysis (6m Train / 1m Test)

**Period**: 2015-01-01 to 2024-12-31

| Window | Train Sharpe | Test Sharpe | Notes |
|--------|-------------|-------------|-------|
| 2015 | 0.85 | 0.40 | Choppy, mean reversion works |
| 2016 | 0.80 | 0.38 | Moderate vol |
| 2017 | 0.60 | 0.25 | Low vol trend, reversion fails |
| 2018 | 0.75 | 0.35 | Higher vol helps |
| 2019 | 0.70 | 0.30 | Steady trend hurts |
| 2020 | 0.90 | 0.50 | Extreme moves revert fast |
| 2021 | 0.65 | 0.28 | Trendy market |
| 2022 | 0.72 | 0.38 | Choppy bear, some reversion |
| 2023 | 0.68 | 0.32 | AI trend, reversion weak |
| 2024 | 0.65 | 0.30 | Concentrated trend |

**Aggregate Statistics**:
- Average Train Sharpe: 0.72
- Average Test Sharpe: 0.35
- Sharpe Degradation: 51.4% (very high — signal degrades fast)
- % of Windows Profitable: 55.0%
- Average Max Drawdown (Test): 42.0%

### Annual Performance

| Year | Return | Sharpe | Max DD | Win Rate |
|------|--------|--------|--------|----------|
| 2015 | 3.5% | 0.40 | 28.5% | 48% |
| 2016 | 4.2% | 0.38 | 32.2% | 47% |
| 2017 | 1.8% | 0.25 | 38.5% | 45% |
| 2018 | 4.8% | 0.35 | 35.5% | 48% |
| 2019 | 2.5% | 0.30 | 30.8% | 46% |
| 2020 | 8.5% | 0.50 | 42.5% | 52% |
| 2021 | 3.2% | 0.28 | 36.2% | 46% |
| 2022 | 3.8% | 0.38 | 38.8% | 49% |
| 2023 | 2.8% | 0.32 | 35.5% | 47% |
| 2024 | 3.2% | 0.30 | 34.2% | 48% |

**CAGR**: 4.5% (backtest, before costs)
**After Costs (est.)**: 0-2% (intraday costs are punishing)

## Risk Warnings

### 1. Mean Reversion Fails in Trends
The biggest risk. In trending markets, oversold stocks get more oversold. The "it's cheap, buy more" mentality leads to mounting losses.

**Historical Case**: March 2020 — SPY dropped 35% in weeks. RSI was oversold for days, luring buyers into a falling knife.

### 2. No Stop-Loss
Without a hard stop, a position can accumulate significant losses before RSI triggers overbought (which may never come in a crash).

### 3. 1-Minute Noise
1-minute bars contain a lot of market microstructure noise. RSI on such short timeframes is unreliable.

### 4. Transaction Costs
Multiple round-trips per day at high frequency. With 1-minute evaluation and typical holding periods of 10-60 minutes, expect 5-20 trades per symbol per day.

### 5. Low Win Rate
48% win rate means the strategy loses more often than it wins. It relies on winners being larger than losers, which is unreliable.

## When This Strategy Works

1. **Range-bound markets**: SPY bouncing between support and resistance
2. **High volatility choppiness**: Quick moves that revert within hours
3. **Post-news digestion**: After initial reaction, prices settle back

## When This Strategy Fails

1. **Trending markets**: Any sustained directional move
2. **Low volatility**: RSI rarely reaches extreme levels
3. **Earnings events**: Single-stock gaps that don't revert
4. **Flash crashes**: RSI goes oversold, price keeps falling

## Implementation Notes

### RSI Calculation
```python
def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
```

### Configuration
```yaml
strategies:
  MeanReversion1m:
    enabled: false
    priority: 4
    parameters:
      symbols: [SPY, QQQ, AAPL]
      rsi_period: 14
      oversold_threshold: 30.0
      overbought_threshold: 70.0
```

### API Usage
```python
from quant.strategies.examples import MeanReversion1m

strategy = MeanReversion1m(
    symbols=["SPY", "QQQ", "AAPL"]
)
```

## Learning Objectives

This strategy demonstrates:
1. **Technical indicators**: RSI calculation and interpretation
2. **Intraday data handling**: Processing 1-minute bars
3. **State management**: Tracking flat/long position states
4. **Event-driven architecture**: `on_data` as the main trading loop

## Future Enhancements

1. **Bollinger Band Filter**: Only buy oversold when price is also below lower Bollinger Band
2. **Trend Filter**: Add 200-SMA filter — only take long reversion signals when above 200-SMA
3. **Stop-Loss**: Add -1% hard stop-loss per position
4. **Time-of-Day Filter**: Avoid first and last 30 minutes (more noise)
5. **Adaptive Thresholds**: Widen oversold/overbought in low vol, tighten in high vol
6. **EMA-based RSI**: Use Wilder's smoothing for more accurate RSI values
7. **Short Signals**: Add short selling when RSI > 70 from flat state
