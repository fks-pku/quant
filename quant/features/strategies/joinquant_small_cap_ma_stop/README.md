# JoinQuant Small Cap MA Stop

Ranks A-share symbols by point-in-time market capitalization, buys the smallest names equal weighted, and exits held names on a short/long moving-average crossunder.

Requires point-in-time `total_mv` or `circ_mv` data from `cn_daily_basic.duckdb`.
