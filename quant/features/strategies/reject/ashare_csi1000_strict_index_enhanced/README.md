# A-share CSI1000 Strict Index-Enhanced Multifactor

Research-only A-share candidate for a CSI1000 index-enhancement sleeve.

The strategy reuses the strict index-enhanced implementation pattern: each rebalance reads the latest prior point-in-time CSI1000 constituent weights, rejects non-tradable or incomplete bars, scores only current constituents, and applies a bounded active multifactor tilt around index weights.

Default design:

- Benchmark: CSI1000 (`000852`).
- Universe: historical CSI1000 constituents from point-in-time index weights.
- Factors: 12-1 momentum, recent momentum, ROE, gross margin, low volatility, low PB, low debt-to-assets, and index weight anchor.
- Portfolio: up to 120 names, 98% target exposure, monthly rebalance, 5.5% single-name cap.
- Risk exits: enabled by default with loose stop-loss, take-profit, and trailing take-profit thresholds suitable for a diversified small/mid-cap sleeve.
- Execution: T+1 next-open execution, 100-share lots, A-share commission/tax, price-limit and suspension checks, and CN daily liquidity-impact costs from the framework.

Research caveat: no industry-neutral optimizer or Barra-style risk model is implemented in this local candidate. Sector, style, and constituent-weight drift must be audited before promotion.
