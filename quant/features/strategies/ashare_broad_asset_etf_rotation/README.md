# A-share Broad Asset ETF Rotation

Top-level daily ETF strategy for domestic broad-asset rotation.

Full report: `full_research_report.html`

## Universe

Default audited categories: SSE50, CSI300, CSI1000, ChiNext, ChiNext50, dividend, gold, cash ETF, and rate bond ETF. Cross-border ETFs and sector/theme ETFs are excluded from the default pool.

## Signal

Every 20 trading days, score visible ETFs by skipped 126-day momentum divided by 60-day realized volatility with a volatility floor. Current bars, sufficient turnover, lookback coverage, and PIT NAV or size evidence remain hard tradability requirements. Momentum strength and the 120-day trend state drive relative branch weights rather than a hard in/out decision.

## Portfolio

Generate a continuous 100% branch weight vector across tradable audited branches. Stronger signals receive higher weights, weaker signals receive lower weights, and single-branch weight is capped by `max_branch_weight`. If a branch has no tradable representative, that branch is not forced into a hidden defensive ETF; residual capital stays as actual cash after lot-size rounding.

## Strategy Status

This strategy has been moved into the top-level strategy registry by user approval. The bundled full report remains the audit record and still preserves walk-forward and stability warnings; use those caveats when deciding paper or live deployment.
