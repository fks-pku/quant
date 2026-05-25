# A-share Small Cap Quality Reversal

Ranks the full A-share universe with a conservative small-cap composite: smaller point-in-time market cap is preferred, but overheated, expensive, crowded, illiquid, ST, suspended, delisting-risk, and sub-price-floor names are penalized or excluded.

The strategy rebalances every 10 trading days into 20 equal-weight names. The default liquidity floor is `turnover >= 20000` in the local `daily_cn_ochl` amount units, approximately RMB 20 million for Tushare-style thousand-CNY turnover. Held positions still run the risk guard daily, so price-floor, ST, listing-status, tradability, or liquidity failures trigger exit attempts outside the rebalance schedule.
