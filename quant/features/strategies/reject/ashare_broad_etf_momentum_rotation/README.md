# A-share Listed Broad ETF Momentum Rotation

Candidate strategy for rotating among liquid CN-listed broad ETFs and broad asset ETFs.

The universe is defined by index/asset category rather than by hindsight performance: SSE 50, CSI 300, CSI 500, CSI 1000, ChiNext, ChiNext 50, dividend, gold, Nasdaq 100, and Hang Seng ETFs listed in the mainland market.

Daily process:

1. Update daily ETF bars and compute liquidity from the latest 20 trading days.
2. Keep only ETFs with enough history, sufficient average turnover, positive 180-day momentum, and close above the 120-day moving average.
3. Score each candidate by 180-day return divided by 60-day annualized volatility.
4. Every 60 trading days, hold the top two candidates with 70% total risk exposure; residual capital remains as cash.
5. On any trading day, exit a held ETF early if it falls below trend or its risk-adjusted momentum turns non-positive.

This is a simple rule-based candidate, not a tuned production allocation model. The strict backtest must be reviewed with walk-forward validation before promotion.
