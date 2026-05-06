# Learning to Learn Financial Networks for Optimising Momentum Strategies

## Source

- **Paper**: [L2GMOM](http://arxiv.org/abs/2308.12212v1)
- **Author**: Xingyue Pu
- **Type**: Cross-Sectional Momentum / Network Portfolio Optimization
- **LLM Evaluation Score**: 8.5/10 (GLM-5.1)

## Strategy Logic

Uses covariance-based network weighting to construct a cross-sectional momentum portfolio:

1. Computes momentum score for each asset (price change over `momentum_period`)
2. Estimates return covariance matrix over `covariance_period`
3. Calculates **network weight** per asset = sum of absolute covariance with all other assets (row sum of |cov matrix|, excluding diagonal)
4. **Adjusted score** = momentum score * network weight
5. Ranks assets by adjusted score, selects top-K
6. Allocates equally to top-K holdings, rebalances every `holding_days`

Assets more connected in the return covariance network receive higher portfolio weights.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `momentum_period` | 20 | Lookback for momentum score |
| `covariance_period` | 60 | Window for covariance estimation |
| `top_k` | 2 | Number of assets to hold |
| `max_position_pct` | 0.90 | Max portfolio allocation |
| `holding_days` | 5 | Rebalance frequency |

## Backtest Results (2020-01-01 to 2025-01-01)

| Metric | Value |
|--------|-------|
| Symbols | SPY, QQQ, IWM, GLD, TLT |
| Initial Capital | $1,000,000 |
| Final NAV | $1,439,385 |
| **Total Return** | **+43.94%** |
| Sharpe Ratio | 0.588 |
| Max Drawdown | 34.70% |
| Total Trades | 337 (198 buys, 139 sells) |
| Commissions | $156,371 |

## Files

- `strategy.py` — Full implementation
- Registry name: `LearningFinancialNetworksMomentumStrategy`

## Key Notes

- Highest total return (+43.94%) among the 4 strategies
- Significant drawdown (34.70%) — concentrated bets on top-2 assets
- High commission drag ($156K) due to frequent rebalancing with large positions
- Network-weighted momentum adds genuine alpha but amplifies concentration risk
