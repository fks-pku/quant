# A-share Broad Asset ETF Rotation

Reject-zone daily ETF candidate for domestic broad-asset rotation.

## Universe

Default audited categories: SSE50, CSI300, CSI1000, ChiNext, ChiNext50, dividend, gold, cash ETF, and rate bond ETF. Cross-border ETFs and sector/theme ETFs are excluded from the default pool.

## Signal

Every 20 trading days, rank visible ETFs by skipped 126-day momentum divided by 60-day realized volatility with a volatility floor. Candidates must have current bars, enough lookback history, positive momentum, close above the 120-day average, sufficient average turnover, and PIT NAV or size evidence when required.

## Portfolio

Hold at most 2 ETFs, with at most 1 ETF per category. Selected ETFs are equal weighted up to the configured target exposure. If no ETF qualifies, the strategy holds actual cash rather than buying a hidden defensive proxy.

## Research Status

This is a candidate strategy. It needs strict backtest, walk-forward validation, ETF metadata survivorship audit, capacity audit, and return contribution attribution before promotion.
