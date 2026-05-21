# A-share Mid Cap Dividend Low-Vol Capacity

Ranks tradable A-shares inside the 30th to 85th percentile point-in-time `total_mv` band.

The strategy rebalances every 20 trading days into 50 equal-weight names. It prefers higher `dv_ttm`, lower 60-day realized volatility, lower `pb`, larger `circ_mv`, and lower free-float turnover rate. The larger `circ_mv` component is a capacity preference, while the low-turnover component avoids crowded high-churn names.
