# joinquant_small_cap_ma_stop

Implements the JoinQuant small-cap selection strategy with a 10/50 moving-average stop.

## Invariants

- Requires point-in-time market-cap fields in incoming daily bars.
- Does not rank by turnover or price as a market-cap proxy.
- Uses adjusted closes for moving-average stop detection and raw close for order sizing.
- Keeps orders long-only and equal-weighted across selected positions.
