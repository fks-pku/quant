# Power Assisted Trend Following

## Source

- **Paper**: [Power Assisted Trend Following](http://arxiv.org/abs/2003.09298v1)
- **Author**: Andreas A. Aigner
- **Type**: Momentum / Trend Following
- **LLM Evaluation Score**: 7.5/10 (GLM-5.1)

## Strategy Logic

Uses Welles Wilder's Directional Movement indicators (ADX/DMI) to measure trend power. Enters long positions only when:

1. +DI crosses above -DI (bullish crossover)
2. ADX exceeds the threshold (trend is strong)
3. No existing position

Exits when either:
- -DI crosses above +DI (bearish crossover), or
- ADX drops below the threshold (trend weakening)

Position sizing uses ATR-based volatility scaling.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `dm_period` | 14 | DMI smoothing period |
| `adx_period` | 14 | ADX smoothing period |
| `adx_threshold` | 25.0 | Minimum ADX for trend confirmation |
| `max_position_pct` | 0.95 | Max portfolio allocation per trade |
| `holding_days` | 1 | Rebalance frequency |

## Backtest Results (2020-01-01 to 2025-01-01)

| Metric | Value |
|--------|-------|
| Symbols | SPY, GLD, TLT |
| Initial Capital | $1,000,000 |
| Final NAV | $1,036,756 |
| **Total Return** | **+3.68%** |
| Sharpe Ratio | 0.252 |
| Max Drawdown | 3.88% |
| Total Trades | 74 (37 buys, 37 sells) |
| Commissions | $34,463 |

## Files

- `strategy.py` — Full implementation
- Registry name: `PowerAssistedTrendFollowingStrategy`

## Key Notes

- Conservative strategy with low drawdown — only trades when trend power is confirmed
- ATR-based position sizing adapts to volatility
- Balanced buy/sell count (37/37) indicates proper position tracking
