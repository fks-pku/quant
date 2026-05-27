# A-share Dividend Low-Volatility Quality Enhanced

Research-only A-share candidate combining dividend yield, low volatility, and quality filters.

The strategy is designed as a stricter version of dividend low-vol smart beta:

- Universe: historical large-cap / upper-mid-cap A-share proxy, filtered each rebalance by live status, liquidity, price, and point-in-time `total_mv` band.
- Dividend: require `dv_ttm >= 1.0`.
- Quality: require `roe >= 6.0` and penalize high `debt_to_assets`; rank higher ROE and gross profit margin.
- Low volatility: rank lower 120-day realized volatility and better recent drawdown.
- Valuation: rank lower `pb` while keeping broad dividend exposure.
- Timing: optional CSI 300 market-temperature gate using a 200-day moving average and medium-term momentum.
- Execution: 60-trading-day rebalance, up to 10 equal-weight holdings, T+1 next-open execution, 100-share lots, CN stock commission/taxes, price-limit/suspension checks, and liquidity-impact costs.

## 2026-05-27 Strict Research Result

Full report: `quant/infrastructure/var/research/reports/ashare_dividend_low_vol_quality_enhanced/full_research_report.html`

Window: 2016-01-01 to 2025-12-31, initial cash 20000, historical Top800 market-cap union, 2314 symbols.

Result: rejected. Strict Sharpe -0.37, CAGR -2.08%, total return -18.98%, max drawdown -23.04%, total trades 212. Calendar walk-forward aggregate OOS Sharpe -0.53, worst OOS Sharpe -0.81, profitable split ratio 0%.
