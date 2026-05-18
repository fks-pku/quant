# joinquant_small_cap_low_price

JoinQuant community-style A-share low-price small-cap candidate.

## Contract

- Uses point-in-time market-cap fields from the data adapter: `total_mv`, `circ_mv`, `market_cap`, `total_market_cap`, `float_market_cap`, or `circulating_market_cap`.
- Buys only raw-close low-price names with `close <= 20`.
- Skips ST and non-tradable bars when status fields are available.
- Keeps the generated strategy long-only and A-share daily-bar based.

## Pitfalls

- Do not replace missing market cap with price, turnover, or static proxies.
- Fast research is signal-level validation; strict Backtester results are the execution-quality source of truth.
