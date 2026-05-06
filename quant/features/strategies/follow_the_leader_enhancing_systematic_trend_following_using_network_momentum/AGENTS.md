# Follow the Leader: Enhancing Systematic Trend-Following Using Network Momentum

## Source

- **Paper**: [Follow the Leader](http://arxiv.org/abs/2501.07135v1)
- **Author**: Linze Li
- **Type**: Cross-Asset Momentum / Network Analysis
- **LLM Evaluation Score**: 6.0/10 (GLM-5.1)

## Strategy Logic

A cross-asset trend-following strategy that exploits lead-lag relationships via network momentum signals:

1. Computes univariate trend signals (price vs. moving average) for each asset
2. Estimates cross-asset correlation matrix from returns
3. Constructs **network signals** — weighted average of other assets' trend signals, weighted by absolute correlation
4. Combines univariate and network signals: `combined = alpha * univariate + (1 - alpha) * network`
5. Goes long when combined signal > 0, closes when combined signal < 0

Position sizing scales with signal strength.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `trend_period` | 50 | Moving average period for trend signal |
| `correlation_period` | 60 | Window for correlation matrix estimation |
| `alpha` | 0.6 | Weight on univariate signal (vs network) |
| `max_position_pct` | 0.90 | Max portfolio allocation |
| `holding_days` | 5 | Rebalance frequency |

## Backtest Results (2020-01-01 to 2025-01-01)

| Metric | Value |
|--------|-------|
| Symbols | SPY, QQQ, IWM, GLD, TLT |
| Initial Capital | $1,000,000 |
| Final NAV | $1,072,823 |
| **Total Return** | **+7.28%** |
| Sharpe Ratio | 0.634 |
| Max Drawdown | 3.44% |
| Total Trades | 188 (94 buys, 94 sells) |
| Commissions | $5,320 |

## Files

- `strategy.py` — Full implementation
- Registry name: `FollowTheLeaderEnhancingSystematicTrendfollowingUsingNetworkMomentumStrategy`

## Key Notes

- Best Sharpe ratio (0.634) among the 4 strategies
- Lowest drawdown (3.44%) — very conservative allocation
- Balanced buy/sell (94/94) with low commission drag
- Network momentum adds genuine cross-asset signal diversification
