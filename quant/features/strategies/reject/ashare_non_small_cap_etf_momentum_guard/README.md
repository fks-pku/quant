# A-share Non-small-cap ETF Momentum Guard

Candidate daily strategy for rotating across liquid CN-listed ETFs without using small-cap index proxies.

The default universe is category-defined rather than hindsight-selected: SSE 50, CSI 300, ChiNext, ChiNext 50, dividend, securities, financial real estate, semiconductor, gold, Nasdaq 100, China Internet/H-share, Hang Seng, and H-share ETFs. CSI 500 and CSI 1000 ETFs are intentionally excluded so the strategy does not become a disguised small-cap allocation.

Daily process:

1. Update daily ETF bars and compute liquidity from the latest 20 trading days.
2. Every 5 trading days, keep ETFs with sufficient history, 20-day average turnover above the threshold, positive 126-day momentum with a 1-day skip, and close above the 120-day moving average.
3. Score candidates by 126-day momentum divided by 20-day annualized volatility.
4. Hold the top three candidates equally at the configured total exposure; if no ETF qualifies, sell existing ETF positions and stay in real cash.
5. Orders are generated after close and execute T+1 at next open in strict backtests with ETF fund commission, lot size, and liquidity-impact costs.

This is a rule-based research candidate. Promotion requires strict backtest review and walk-forward validation.
