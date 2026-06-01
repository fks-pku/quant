# A-share CSI300 Strict Index Enhanced

Research-only candidate for a strict CSI300 internal index-enhancement strategy.

The strategy uses Tushare `index_weight` history for `000300.SH`. At each rebalance it takes the latest known constituent weights at or before the signal date, scores only those constituents with point-in-time price, valuation, quality, dividend, and risk fields, then applies a bounded active tilt around CSI300 weights. It never buys stocks outside the historical CSI300 constituent set.

Current constraints:

- Universe: historical CSI300 constituents from `cn_index_weight.duckdb::cn_index_weight`.
- Benchmark: `000300`.
- Portfolio: up to 90 CSI300 constituents, 98% target exposure, monthly rebalance.
- Active control: target weights start from index weights, then use bounded multipliers and a single-name cap.
- Risk exits: wide stock-level 40% stop-loss and 100%/30% trailing take-profit remain enabled for research consistency.

This is a high active-share index-enhancement candidate, not a low-deviation full replication portfolio. Promotion requires strict full-report evidence, residual permission checks for ChiNext/STAR constituents, and formal tracking-error review.
