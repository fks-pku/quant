# A-share Davis Double Click

Research-only A-share candidate inspired by the Davis Double Click thesis: buy companies where earnings growth is already visible and valuation is still reasonable, then require price momentum as confirmation that the market is starting to re-rate the stock.

The strategy uses point-in-time `cn_financial_indicators` fields joined by `ann_date`, plus daily `cn_daily_basic` valuation fields. It filters ST, suspended, non-listed, low-price, and low-liquidity names through the shared A-share mid-cap composite guard.

Default construction:

- Universe: all A-share daily bars, filtered each rebalance by live status and the 25th to 95th percentile point-in-time `total_mv` band.
- Growth filter: `q_netprofit_yoy` falling back to `netprofit_yoy`, requiring at least 15%.
- Quality filter: `q_roe` falling back to `roe`, requiring at least 6%.
- Valuation filter: `5 <= pe_ttm <= 60`.
- Re-rating confirmation: 126-day adjusted-price momentum skipping the most recent 5 trading days, requiring at least -5%.
- Ranking: high growth-to-PE, high profit growth, high ROE, high earnings yield, high momentum, and high sales growth.
- Portfolio: every 20 trading days, hold up to 10 equal-weight stocks, rounded to 100-share lots.
