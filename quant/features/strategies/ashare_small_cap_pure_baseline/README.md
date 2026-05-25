# A-share Small Cap Guarded Baseline

Ranks the full A-share universe by point-in-time market capitalization and holds the smallest eligible names after risk and quality controls.

Eligibility excludes ST, suspended, non-tradable, non-listed, non-`L` list status, non-mainland A-share symbols, stocks below the price floor, missing market cap, stocks below the trailing liquidity floor, and stocks below their configured trend filter. The default quality guard also excludes names with `pb > 12`, `ps_ttm`/`ps > 20`, `turnover_rate_f`/`turnover_rate > 35`, or `volume_ratio > 5`; enabled quality fields must be present. Held positions that trigger those risk rules are submitted for daily exit.

The default guarded profile keeps a pure small-cap universe, adds soft valuation/crowding controls, and caps gross exposure at 55%. Position sizing uses fixed target slots (`target_exposure / max_positions`) so sparse signals do not concentrate the portfolio into a few stocks. The 2016-2025 strict grid selected this quality-controlled small-cap risk budget because it improved CAGR and Sharpe while keeping max drawdown inside 30%, without broad-index ETF blending.
