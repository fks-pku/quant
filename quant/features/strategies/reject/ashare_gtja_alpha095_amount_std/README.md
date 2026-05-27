# A-share GTJA Alpha095 Amount Standard Deviation

Research-only A-share candidate for Guotai Junan Alpha191 factor 095.

Source formula:

```text
Alpha095 = STD(AMOUNT, 20)
```

The local implementation uses the last 20 daily cash amount observations, ranks the cross-section by the raw Alpha191 value, and holds the top-ranked basket. Because the public Alpha191 formula library provides the formula but not a separate economic direction proof for each market regime, the default `alpha_high_is_better=true` is treated as a faithful formula-direction assumption rather than an optimized direction choice.

Default design:

- Universe: historical Top800 market-cap union, then daily point-in-time 25%-100% market-cap percentile band.
- Factor: 20-day sample standard deviation of daily amount.
- Portfolio: 40 equal-weight slots, 95% target gross exposure, weekly rebalance.
- Execution: T+1 next-open execution, 100-share lots, A-share commission/tax, price-limit and suspension checks, and CN daily liquidity-impact costs.
- Research caveat: this is a single raw short-cycle volume factor without industry neutralization, factor winsorization, or direction tuning.

## 2026-05-27 Strict Research Result

Full report: `quant/infrastructure/var/research/reports/ashare_gtja_alpha095_amount_std/full_research_report.html`

Window: 2016-01-01 to 2025-12-31, initial cash 1000000, historical Top800 market-cap union, 2314 symbols.

Result: rejected. Strict Sharpe -0.63, CAGR -13.45%, total return -76.40%, max drawdown -81.86%, total trades 12397. Calendar walk-forward aggregate OOS Sharpe -0.62, worst OOS Sharpe -1.36, profitable split ratio 0%. Capacity was not the binding issue: max ADV participation was 0.01%.
