# A-share Gold-Equity ETF Barbell Timing

Candidate non-small-cap daily ETF strategy.

The strategy avoids single-stock and small-cap exposure. It uses the CSI 300 index as the market temperature signal and trades only the user-audited stable ETF registry. New ETF categories must be explicitly reviewed and registered before any strategy can use them.

Daily process:

1. Update bars for the audited representative equity ETF categories and gold ETF.
2. Every 20 trading days, classify market state from CSI 300 index (`000300`): close above 120-day moving average and 63-day momentum above zero means risk-on.
3. For each registered category (`sse50`, `csi300`, `chinext`, `chinext50`, `dividend`, `gold`), keep only the audited representative ETF if it has current bars, NAV/size, liquidity, and lookback data.
4. In risk-on state, rank the registered equity representatives by 63-day momentum divided by 20-day annualized volatility.
5. Allocate half of the configured exposure to the best registered equity representative and half to the registered gold ETF.
6. In risk-off state, allocate the configured exposure to gold ETF only; if data is missing, stay in cash.

This is a transparent registered-representative asset-allocation rule. It no longer relies on current broad ETF taxonomy to infer historical categories. Strict backtests must use ETF total-return bars normalized by fund NAV when available, and missing size data rejects the ETF instead of silently falling back.
