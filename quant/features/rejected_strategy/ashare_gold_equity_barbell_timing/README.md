# A-share Gold-Equity ETF Barbell Timing

Candidate non-small-cap daily ETF strategy.

The strategy avoids single-stock and small-cap exposure. It uses the CSI 300 index as the market temperature signal, builds a point-in-time ETF category universe, selects the largest ETF in each category using as-of fund size, and then applies momentum/volatility ranking only across those category representatives. When risk is off, it holds the largest available gold ETF only.

Daily process:

1. Update bars for broad equity ETF categories and gold ETF candidates.
2. Every 20 trading days, classify market state from CSI 300 index (`000300`): close above 120-day moving average and 63-day momentum above zero means risk-on.
3. For each category (`sse50`, `csi300`, `chinext`, `chinext50`, `dividend`, `gold`), keep only ETFs with current bars and as-of `total_netasset`/`net_asset`, then choose the largest ETF in that category at the rebalance date.
4. In risk-on state, rank the equity category representatives by 63-day momentum divided by 20-day annualized volatility.
5. Allocate half of the configured exposure to the best equity representative and half to the largest gold ETF.
6. In risk-off state, allocate the configured exposure to gold ETF only; if data is missing, stay in cash.

This is a transparent asset-allocation rule, not a hardcoded ETF selection. Strict backtests must use ETF total-return bars normalized by fund NAV when available, and missing point-in-time size data rejects the ETF instead of silently falling back to a fixed list.
