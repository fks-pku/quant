# A-share PEG Undervalued Reversion

Research-only A-share candidate for the user's PEG thesis: buy currently undervalued stocks where trailing PE is low relative to already-disclosed profit growth, then hold for a longer re-rating window until PEG normalizes.

The strategy computes `PEG = pe_ttm / growth_pct`, where `growth_pct` is `q_netprofit_yoy` and falls back to `netprofit_yoy` only when quarterly growth is unavailable. Financial indicators are consumed through the research provider's `ann_date` point-in-time join; valuation fields come from daily `cn_daily_basic`.

Default construction:

- Universe: all observed A-share daily bars, excluding ordinary-account restricted board prefixes `300`, `301`, `688`, and `689`, then filtered by live status, price, liquidity, and the 15th to 95th percentile point-in-time `total_mv` band.
- PEG entry: `0 < PEG <= 0.60`; profit growth must be at least 8%, with growth capped at 120% for PEG scoring to reduce one-off base effects.
- Valuation and quality guards: `3 <= pe_ttm <= 80`, `q_roe/roe >= 5%`, and sales growth no worse than -20%.
- Value-trap guard: 126-day adjusted-price momentum, skipping the most recent 5 trading days, must be no worse than -25%.
- Ranking: largest PEG discount to fair value, highest inverse PEG, ROE, profit growth, earnings yield, momentum, and sales growth.
- Portfolio: every 60 trading days, hold up to 10 equal-weight stocks, rounded to 100-share lots.
- Thesis exit: if disclosed growth breaks below 8% or current PEG reaches `1.05`, sell even before the next scheduled rebalance.
- Risk exit: enabled by default with a wide package suitable for longer holding periods: 18%-32% volatility-adjusted stop loss, 60% profit trigger with 20%-35% trailing stop, and a 252-trading-day time stop for positions still below breakeven.

This is not promoted by default. Promotion requires the full strict research checklist to pass, including point-in-time data, T+1 execution, limit/suspension handling, lot sizes, commissions, liquidity impact, walk-forward evidence, and capacity.
