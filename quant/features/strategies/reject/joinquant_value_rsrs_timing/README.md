# JoinQuant Value RSRS Timing

Daily A-share candidate inspired by the JoinQuant article "价值选股与RSRS择时".

The public article describes the high-level structure but does not publish full executable code, so this implementation keeps the auditable pieces explicit:

- use A-share stock bars and point-in-time `daily_basic` valuation fields;
- rank eligible stocks by value: low `pb`, low `pe_ttm`, low `ps_ttm`, higher `dv_ttm`, and larger `circ_mv` as a capacity preference;
- compute RSRS on `timing_symbol` high/low prices using rolling OLS slope of high on low;
- enter risk-on only when the RSRS z-score adjusted by `R^2` crosses `rsrs_entry`;
- exit to cash when the RSRS score crosses `rsrs_exit`;
- filter ST, suspended, non-listed, non-tradable, low-price, and low-turnover stocks every day.

Default timing proxy is `000300` with a 18-day RSRS regression window and 120-day z-score window. The default rebalance cadence is 20 trading days into 20 equal-weight positions.
