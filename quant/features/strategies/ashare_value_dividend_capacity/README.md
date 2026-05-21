# A-share Value Dividend Capacity

Ranks the A-share universe with point-in-time `daily_basic` valuation and capacity fields: low `pe_ttm`, low `pb`, low `ps_ttm`, high `dv_ttm`, and larger `circ_mv`.

The strategy rebalances every 20 trading days into 50 equal-weight names. It filters ST, suspended, non-listed, low-price, low-turnover, and small-capacity names before ranking. This is a candidate research strategy intended to test whether the current DuckDB `daily_basic` fields are sufficient for a low-turnover mid/large-cap value-dividend portfolio.
