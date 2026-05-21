# A-share Mid Cap Momentum Value Guard

Ranks tradable A-shares inside the 30th to 80th percentile point-in-time `total_mv` band.

The strategy rebalances every 20 trading days into 50 equal-weight names. It uses 12-1 style momentum, measured as the adjusted-price return from 252 trading days ago to 21 trading days ago, then adds valuation and risk guards through lower `pb`, lower `ps_ttm`, lower 120-day volatility, and larger `circ_mv`.
