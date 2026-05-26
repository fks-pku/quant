# A-share Gold-Equity ETF Barbell Timing

Active candidate daily ETF timing strategy.

The strategy avoids single-stock and small-cap exposure. It uses the CSI 300 index as the market temperature signal and trades only a user-audited stable ETF registry: `510050`, `510300`, `159915`, `159949`, `510880`, and `518880`. New ETF categories must be explicitly reviewed and added to the registry before any strategy can use them.

Daily process:

1. Update bars for the audited representative equity ETF categories and gold ETF.
2. Every 20 trading days, classify market state from CSI 300 index (`000300`): close above 120-day moving average and 63-day momentum above zero means risk-on.
3. At each rebalance date, keep only registered ETFs with current bars, fund NAV/size fields, sufficient liquidity, and enough lookback history.
4. In risk-on state, rank the registered equity representatives by 63-day momentum divided by 20-day annualized volatility.
5. Allocate half of the configured exposure to the best registered equity ETF and half to the registered gold ETF.
6. In risk-off state, allocate the configured exposure to gold ETF only; if data is missing, stay in cash.

The universe is intentionally not a full-market ETF category universe. It is a pre-registered representative pool that reduces implicit category look-ahead risk at the cost of manual representative-pool selection bias.
