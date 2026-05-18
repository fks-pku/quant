# joinquant_small_cap_low_price

JoinQuant community-style A-share low-price small-cap candidate.

## Contract

- Uses point-in-time market-cap fields from the data adapter: `total_mv`, `circ_mv`, `market_cap`, `total_market_cap`, `float_market_cap`, or `circulating_market_cap`.
- Buys only raw-close low-price names with `2 <= close <= 20`.
- Requires basic liquidity: trailing `liquidity_lookback` average `turnover >= min_avg_turnover`.
- Skips ST, suspended, non-tradable, non-listed, and `list_status != "L"` bars when status fields are available.
- Runs `delisting_risk_guard` before the normal rebalance gate; held positions that trip the guard submit SELL daily even when `holding_days` would otherwise skip rebalancing.
- Keeps the generated strategy long-only and A-share daily-bar based.

## Pitfalls

- Do not replace missing market cap with price, turnover, or static proxies.
- Do not remove the daily risk-exit path when changing `holding_days`; CASE-37 in `test_backtest_invariants.py` depends on that invariant.
- Fast research is signal-level validation; strict Backtester results are the execution-quality source of truth.
