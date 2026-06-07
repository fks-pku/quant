# A-share All-Weather Risk Parity

Reject-zone daily ETF candidate inspired by Bridgewater-style all-weather portfolio construction.

## Universe

The default pool uses domestic listed ETF proxies only: broad equity (`510300`, `510050`, `510880`), gold (`518880`), rate-bond ETF (`511010`), and cash ETF (`511990`). Cross-border, sector/theme, and small-cap proxy ETFs are excluded from the default pool.

## Signal

At each rebalance date, the strategy first verifies current bars, enough lookback history, average turnover, and PIT fund size/NAV evidence. Each category selects its strongest visible representative by skipped momentum divided by realized volatility. The default portfolio then converts category risk budgets into inverse-volatility weights with a per-asset cap.

## Portfolio

Default risk budgets are equity 35%, bond-rate 35%, gold 20%, and cash 10%. These are risk budgets, not fixed capital weights. Capital weights are recomputed from realized volatility, capped by `max_asset_weight`, and rounded to ETF lots. If a bucket has no eligible ETF, that risk budget is not forced into a hidden substitute.

## Risk Exit

`risk_exit.enabled=true` by default. The PnL risk-exit package checks stop loss, trailing take profit, and time stop every trading day before the rebalance gate. Disabled risk-exit runs are sensitivity/ablation cases only.

## Research Status

This is a candidate strategy. It needs strict backtest, walk-forward validation, ETF metadata survivorship audit, capacity audit, and portfolio correlation review before promotion.
