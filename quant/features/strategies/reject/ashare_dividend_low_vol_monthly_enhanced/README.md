# A-share Dividend Low-Vol Monthly Enhanced

Research-only candidate based on public dividend-low-volatility enhancement ideas.

The strategy follows the reproducible daily-data parts of the BigQuant/Guosheng and Huatai dividend low-vol research summaries: use a broad A-share universe, require current dividend yield, rebalance monthly, prefer lower 20-day realized volatility, and keep valuation/quality controls to avoid obvious high-dividend traps.

Current constraints:

- Universe: historical daily top-3000 `total_mv` union, then current status, price, liquidity, market-cap percentile, dividend, valuation, and volatility filters.
- Benchmark: `000300`.
- Portfolio: up to 30 A-share stocks, 95% target exposure, monthly rebalance.
- Ranking: high `dv_ttm`, low 20-day volatility, low PB, shallower drawdown, ROE, low debt, and recent momentum.
- Risk exits: enabled by default with 18% stop loss and 45%/16% trailing take-profit.

Important residual gaps:

- Local data does not include the Guosheng intraday high-frequency volatility factor, so 20-day daily realized volatility is used as the tradable proxy.
- Local data does not include point-in-time CSI dividend-low-vol constituent history for exact BP-spread timing, so this version does not implement the report's valuation-spread timing signal.
- This candidate must remain under `strategies/reject/` until strict full-report evidence passes the production gate.
