# JoinQuant Wufu Daily ETF/LOF

Daily-bar approximation of the JoinQuant Wufu ETF rotation idea.

The strategy builds a dynamic listed ETF/LOF universe from Tushare fund metadata, removes cash-like and bond-like products from the risk pool, ranks candidates by 25-day weighted log-price regression momentum times R-squared, filters by recent turnover and closing premium to NAV, and rotates into the highest positive score. If too few candidates pass or the best score is non-positive, it holds `511880` as the defensive cash-like leg.

Signals are generated after close and executed by the project Backtester on the next session.
