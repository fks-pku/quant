# A-share Value Momentum Filter

Ranks tradable A-shares inside the 20th to 90th percentile point-in-time `total_mv` band.

The strategy rebalances every 20 trading days into 50 equal-weight names. It combines low `pb`, low `pe_ttm`, low `ps_ttm`, 12-1 style adjusted-price momentum, lower `turnover_rate_f`, and larger `circ_mv`. It rejects names whose most recent 21-trading-day adjusted return is above 30% to avoid buying short-term overheated moves.
