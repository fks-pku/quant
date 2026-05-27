# A-share CSI300 Index-Enhanced Multifactor

Research-only A-share candidate for a CSI300 proxy index-enhancement sleeve.

The strategy uses a historical large-cap union instead of current index constituents. Each rebalance filters by live trading status, price, liquidity, valuation sanity, and point-in-time market cap band, then ranks eligible stocks by medium-term momentum, short-term momentum, profitability, low volatility, valuation, turnover, and dividend yield.

Default design:

- Universe: historical Top500 or Top800 market-cap proxy, then daily point-in-time top-cap percentile band.
- Factors: momentum, recent momentum, ROE, low volatility, low PB, low PE, low turnover, dividend yield.
- Portfolio: 40 equal-weight slots, 95% target gross exposure, monthly rebalance, at most 10 replacements per rebalance.
- Execution: T+1 next-open execution, 100-share lots, A-share commission/tax, price-limit and suspension checks, and CN daily liquidity-impact costs.
- Research caveat: no industry-neutral optimizer is implemented in this local version, so sector drift remains a material residual risk versus professional index-enhancement products.

## 2026-05-27 Strict Research Result

Full report: `quant/infrastructure/var/research/reports/ashare_csi300_index_enhanced_multifactor/full_research_report.html`

Window: 2016-01-01 to 2025-12-31, initial cash 1000000, historical Top800 market-cap union, 2314 symbols.

Result: needs more validation. Strict Sharpe 0.31, CAGR 3.61%, total return 42.55%, max drawdown -37.05%, total trades 5045. Calendar walk-forward aggregate OOS Sharpe 0.20, worst OOS Sharpe -0.54, profitable split ratio 50%.
