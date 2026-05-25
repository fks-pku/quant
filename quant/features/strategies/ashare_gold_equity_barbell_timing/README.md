# A-share Gold-Equity ETF Barbell Timing

Active candidate daily ETF timing strategy.

The strategy avoids single-stock and small-cap exposure. It uses the CSI 300 index as the market temperature signal, builds a point-in-time broad ETF category universe, and ranks the ETFs that were visible at each rebalance date by momentum adjusted for volatility. When risk is off, it uses the visible gold ETF candidates as the defensive leg.

Daily process:

1. Update bars for broad equity ETF categories and gold ETF candidates.
2. Every 20 trading days, classify market state from CSI 300 index (`000300`): close above 120-day moving average and 63-day momentum above zero means risk-on.
3. At each rebalance date, keep only ETFs with current bars, point-in-time fund size fields, sufficient liquidity, and enough lookback history; future ETF launches are not eligible in earlier windows.
4. In risk-on state, rank the visible equity ETF candidates by 63-day momentum divided by 20-day annualized volatility.
5. Allocate half of the configured exposure to the best equity ETF and half to the best gold ETF.
6. In risk-off state, allocate the configured exposure to gold ETF only; if data is missing, stay in cash.

Strict research report after ETF `adj_factor` share-adjustment handling: CAGR 12.24%, MaxDD -11.85%, 303 trades, max single-order participation 2.00% ADV. The current report still carries a data-audit warning because some ETF symbols lack complete fund metadata and delist markers.
