# Enhancing Time Series Momentum Strategies Using Deep Neural Networks

## Source

- **Paper**: [Deep Momentum Networks](http://arxiv.org/abs/1904.04912v3)
- **Author**: Bryan Lim
- **Type**: Time Series Momentum / Conviction-Based Sizing
- **LLM Evaluation Score**: 7.0/10 (GLM-5.1)

## Strategy Logic

A simplified implementation of Deep Momentum Networks that uses multi-timeframe moving average alignment as a conviction signal, combined with volatility-scaled position sizing:

1. Computes short (10), medium (50), and long (200) moving averages
2. **Conviction score** (0 to 0.99): +0.33 for each aligned condition:
   - Short MA > Medium MA
   - Medium MA > Long MA
   - Price > Short MA
3. Estimates realized volatility from daily returns (annualized)
4. **Vol scale** = min(target_vol / realized_vol, 2.0)
5. Target position = NAV * conviction * vol_scale * max_position_pct
6. Trades the delta between target and current position (buys increases, sells reductions)
7. Goes flat when conviction drops to 0.33 or below

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `short_period` | 10 | Short MA period |
| `medium_period` | 50 | Medium MA period |
| `long_period` | 200 | Long MA period |
| `vol_lookback` | 21 | Window for realized volatility |
| `target_vol` | 0.15 | Target annual volatility (15%) |
| `max_position_pct` | 0.95 | Max portfolio allocation |
| `holding_days` | 1 | Rebalance frequency (daily) |

## Backtest Results (2020-01-01 to 2025-01-01)

| Metric | Value |
|--------|-------|
| Symbols | SPY, QQQ, GLD |
| Initial Capital | $1,000,000 |
| Final NAV | $1,188,993 |
| **Total Return** | **+18.90%** |
| Sharpe Ratio | 0.400 |
| Max Drawdown | 17.39% |
| Total Trades | 622 (327 buys, 295 sells) |
| Commissions | $88,314 |

## Files

- `strategy.py` — Full implementation
- Registry name: `DeepMomentumNetworksStrategy`

## Key Notes

- Most active strategy (622 trades) due to daily rebalancing
- Volatility scaling adapts position size inversely to realized vol
- High commission drag ($88K) from frequent small adjustments
- Conviction-based sizing provides graduated exposure rather than binary in/out
