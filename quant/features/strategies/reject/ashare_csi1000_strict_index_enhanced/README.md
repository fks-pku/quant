# A-share CSI1000 Strict Index-Enhanced Multifactor

Research-only A-share candidate for a CSI1000 index-enhancement sleeve.

This runner is used as a BigQuant CSI1000 index-enhancement replication baseline. The public source uses a CNY 2,000,000 starting capital assumption; the dedicated full-research runner keeps that scale because a 120-name A-share basket with 100-share buy lots can otherwise round target positions to zero.

The strategy reuses the strict index-enhanced implementation pattern: each rebalance reads the latest prior point-in-time CSI1000 constituent weights, rejects non-tradable or incomplete bars, scores only current constituents, and applies a bounded active multifactor tilt around index weights.

Default design:

- Benchmark: CSI1000 (`000852`).
- Universe: historical CSI1000 constituents from point-in-time index weights.
- Factors: 12-1 momentum, recent momentum, ROE, gross margin, low volatility, low PB, low debt-to-assets, and index weight anchor.
- Portfolio: up to 120 names, 98% target exposure, monthly rebalance, 5.5% single-name cap.
- Risk exits: enabled by default with loose stop-loss, take-profit, and trailing take-profit thresholds suitable for a diversified small/mid-cap sleeve.
- Execution: T+1 next-open execution, 100-share lots, A-share commission/tax, price-limit and suspension checks, and CN daily liquidity-impact costs from the framework.
- Research capital: `quant/scripts/run_ashare_csi1000_strict_index_enhanced_full_research.py` uses CNY 2,000,000 to match the public BigQuant source and avoid invalid zero-trade lot rounding.

Research caveat: no industry-neutral optimizer or Barra-style risk model is implemented in this local candidate. Sector, style, and constituent-weight drift must be audited before promotion.
