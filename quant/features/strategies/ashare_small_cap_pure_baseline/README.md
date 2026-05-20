# A-share Pure Small Cap Baseline

Ranks the full A-share universe by point-in-time market capitalization and holds the smallest eligible names equal weighted.

Eligibility excludes ST, suspended, non-tradable, non-listed, non-`L` list status, non-mainland A-share symbols, stocks below the price floor, missing market cap, and stocks below the trailing liquidity floor. The default liquidity floor is `turnover >= 20000` in the local `daily_cn_ochl` amount units, approximately RMB 20 million for Tushare-style thousand-CNY turnover. Held positions that trigger those risk rules are submitted for daily exit.
