# A-share Mid Cap Low-Vol Value

Ranks the tradable A-share universe inside a dynamic mid-cap band. The default band is the 30th to 80th percentile of point-in-time `total_mv` on each rebalance date.

The strategy rebalances every 20 trading days into 50 equal-weight names. It prefers lower `pb`, lower `pe_ttm`, lower `ps_ttm`, lower 60-day realized volatility, and smaller 60-day drawdown. ST, suspended, non-listed, non-tradable, low-price, low-turnover, and out-of-band market-cap names are filtered before ranking; held positions still run the risk guard daily.
