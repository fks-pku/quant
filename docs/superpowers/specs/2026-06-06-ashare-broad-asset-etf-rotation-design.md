# A-share Broad Asset ETF Rotation Design

## Status

Design approved for specification on 2026-06-06.

User direction:

- Build a new historical ETF rotation candidate rather than changing the existing gold-equity barbell strategy.
- Do not include cross-border broad ETFs such as Nasdaq, Hang Seng, H-share, or China Internet funds in the default pool.
- Include CSI1000 as an explicit high-volatility small-cap proxy candidate.

## Problem

The existing `ashare_gold_equity_barbell_timing` strategy uses a narrow audited registry: SSE50, CSI300, ChiNext, ChiNext50, dividend, and gold. That keeps the promoted candidate easy to audit, but it creates visible universe-selection risk. Gold has performed strongly in recent years, so a strategy that always keeps gold as the defensive leg may partly be fitting one successful asset rather than proving robust rotation logic.

We need a separate research candidate that tests broader domestic ETF rotation while preserving the current promoted strategy's audit boundary.

## Goals

- Create a new reject-zone candidate strategy for domestic ETF asset rotation.
- Use an audited, frozen default candidate universe before backtests are evaluated.
- Treat gold, cash ETF, and bond ETF as candidates in the same ranking process rather than fixed fallback legs.
- Include CSI1000 as a deliberately recorded small-cap proxy.
- Keep all signal timestamps, order timestamps, and execution timestamps separated for daily A-share research.
- Preserve enough diagnostics for survivorship, universe, capacity, and overfitting review.

## Non-Goals

- Do not modify the current promoted `ashare_gold_equity_barbell_timing` default universe.
- Do not include cross-border ETF categories in the default universe.
- Do not include sector or theme ETFs in the first implementation.
- Do not auto-expand from current full-market ETF taxonomy.
- Do not tune parameters after reading the new backtest result.
- Do not add a PnL stop-loss or take-profit package in the first implementation. Any PnL risk-exit package should be a later ablation with a no-exit baseline.

## Candidate Universe

The first default universe is domestic and category-bucketed:

| Bucket | Role | Default Symbol | Notes |
|---|---|---:|---|
| SSE50 | Equity risk | 510050 | Large-cap A-share exposure |
| CSI300 | Equity risk | 510300 | Core large/mid A-share exposure |
| CSI1000 | Equity risk | 512100 | Explicit high-volatility small-cap proxy |
| ChiNext | Equity risk | 159915 | Growth board exposure |
| ChiNext50 | Equity risk | 159949 | Concentrated growth board exposure |
| Dividend | Equity style | 510880 | Dividend/style exposure |
| Gold | Defensive commodity | 518880 | Candidate only, not a fixed fallback |
| Cash ETF | Low-risk cash | 511990 | Candidate only, not a guaranteed fallback |
| Rate bond ETF | Low-risk bond | 511010 | Candidate only, not a guaranteed fallback |

The registry already contains these representative domestic instruments under `audited_stable_etf_registry_v1`; some are currently `registered_only`. The new strategy should consume an explicit strategy-specific category set from the audited registry rather than changing the gold-equity barbell role semantics.

Cross-border candidates such as `513100`, `513050`, `159920`, and `510900` stay out of the default universe.

## Approach Options

### Option A: Static Flat Symbol List

The strategy owns a hard-coded list of symbols and ranks them directly.

Pros:

- Small implementation.
- Easy unit tests.

Cons:

- Weaker audit story.
- Harder to report category coverage and future visibility.
- Encourages later ad hoc symbol edits.

### Option B: Audited Category-Bucket Universe

Add a broad-asset universe builder that reads selected categories from `audited_stable_etf_registry_v1`, then pass category symbols into a new reject-zone strategy.

Pros:

- Best fit with current ETF registry constraints.
- Keeps full-market taxonomy out of strategy selection.
- Produces clean category counts, missing-data counts, and audit rows.
- Lets each bucket later hold multiple user-approved representatives without changing strategy ranking logic.

Cons:

- Requires a small infrastructure helper plus strategy tests.

### Option C: Dynamic Full ETF Taxonomy

Build a point-in-time universe from fund metadata categories and let the strategy choose across all matching domestic ETFs.

Pros:

- Broadest historical search.
- Less manual registry maintenance.

Cons:

- Too much universe-selection and survivorship risk for a first candidate.
- Violates the current guardrail against auto-expanding promoted ETF category strategies from current taxonomy.
- Harder to explain failed or delisted coverage.

Recommendation: Option B.

## Strategy Design

New strategy id:

`ashare_broad_asset_etf_rotation`

Location:

`quant/features/strategies/reject/ashare_broad_asset_etf_rotation/`

Default mechanics:

- Daily-bar strategy using `DailyBarStrategy`.
- Rebalance every 20 trading days by default.
- Signal uses adjusted close history available at the close of the signal date.
- Orders are submitted after close and filled by the backtest engine with T+1 daily execution assumptions.
- Rank eligible ETFs by skipped momentum divided by volatility.
- Use `momentum_lookback=126`, `momentum_skip=1`, `trend_window=120`, `volatility_window=60`, `liquidity_window=20`.
- Require current bar, sufficient lookback, positive skipped momentum, price above trend, minimum average turnover, and optional PIT NAV or size evidence.
- Use a volatility floor so near-cash assets do not dominate solely because their realized volatility is close to zero.
- Select at most 3 ETFs.
- Select at most 1 ETF per category bucket.
- Equal-weight selected ETFs up to `target_exposure=0.98`.
- If no ETF qualifies, liquidate non-selected holdings and remain in actual cash.
- Gold, cash ETF, and bond ETF are ordinary candidates. They are not forced fallback legs.

Diagnostics should expose:

- Last eligible symbols by category.
- Last selected symbols and scores.
- Entry rejection counts by reason.
- Category counts and missing-data counts.
- Parameters and universe registry version.

## Data Flow

1. The research or strict backtest setup resolves the broad-asset ETF universe from the audited registry.
2. The resolved category map is passed into the strategy constructor.
3. The backtest data loader includes every symbol in the category map.
4. On each trading day, the strategy stores incoming daily bars through existing daily-bar mechanics.
5. On rebalance dates, the strategy filters candidates using only current or earlier data.
6. The strategy ranks candidates, builds target weights, sells stale holdings, and submits buy/sell deltas.
7. Reports and diagnostics show the resolved universe and the reasons candidates were rejected.

## Error Handling

- Missing current bar: reject the ETF for that date.
- Missing lookback: reject the ETF for that date.
- Missing PIT size or NAV when required: reject the ETF for that date.
- Low turnover: reject the ETF for that date.
- Non-positive score: reject the ETF for that date.
- No selected ETF: hold actual cash instead of buying a hidden defensive proxy.
- Invalid price or zero NAV: do not place the order.

## Testing

Add focused strategy tests:

- Default universe includes CSI1000 and excludes cross-border ETFs.
- Gold, cash ETF, and bond ETF are ranked as ordinary candidates, not forced fallback legs.
- When no candidate qualifies, the strategy sells existing holdings and stays in cash.
- Current-bar filtering prevents a future or stale ETF from becoming visible.
- Ranking uses risk-adjusted momentum with a volatility floor.
- Category cap prevents multiple representatives from the same bucket from crowding out the rest of the pool.
- Orders are sized by target slots rather than by the number of currently passing symbols when max slots are configured.

Add or extend invariant tests only if the implementation introduces a reusable strategy-layer rule. Expected invariant candidates:

- ETF universe strategies must not auto-include cross-border categories unless explicitly configured.
- Broad-asset ETF rotation must preserve explicit cash fallback rather than hidden defensive ETF fallback.

## Bias Audit Verdict

Overall: Warning.

This is acceptable for a reject-zone research candidate. It is not enough for promotion until strict backtest, walk-forward, survivorship audit, and capacity review are complete.

| Bias | Status | Evidence | Residual Risk | Required Fix |
|---|---|---|---|---|
| Look-ahead | Pass | Signal uses current daily bars and T+1 execution assumptions | Tests must prove stale and future bars are rejected | Add current-bar and future-listing tests |
| Survivorship | Warning | Uses audited registry and ETF metadata audit hooks | Delist marker and metadata coverage can still be incomplete | Report ETF metadata coverage and registered missing-data counts |
| Universe selection | Warning | User-approved domestic buckets are frozen before backtest | CSI1000 is a subjective addition and may fit small-cap regimes | Record category rationale and keep rejected variants |
| Data snooping | Warning | Parameters are fixed in the design | Future iterations may tune after seeing results | Preserve this spec and compare only locked variants |
| Overfitting/regime | Warning | Gold is not a fixed leg and all assets compete under one ranking process | Returns may still concentrate in gold or CSI1000 regimes | Walk-forward and yearly contribution attribution |
| Execution | Warning | Existing backtest framework models ETF fees, lots, T+1, and ADV participation | Capacity may be weaker for small-cap proxy ETFs | Run 5 percent ADV capacity audit |
| Corporate action | Pass | Existing ETF bar/NAV normalization handles fund adjustments | Needs strict provider coverage in full backtest | Use ETF adjusted close for signals and executable prices for fills |
| Benchmark/metric | Warning | Benchmark can be set before validation | A single benchmark may flatter domestic multi-asset rotation | Compare against CSI300 and equal-weight universe baseline |

## Go / No-Go Impact

Go for implementation as a reject-zone research candidate.

No-Go for promotion until:

- The strategy passes focused unit tests.
- Strict backtest includes cost, liquidity, and capacity evidence.
- Walk-forward reports worst split, profitable split ratio, and no-trade windows.
- The report shows whether returns are concentrated in CSI1000, gold, or one calendar period.
- The universe audit confirms no cross-border symbols entered the default run.

## Implementation Scope

Expected new files:

- `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/strategy.py`
- `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/config.yaml`
- `quant/features/strategies/reject/ashare_broad_asset_etf_rotation/README.md`
- `quant/tests/test_ashare_broad_asset_etf_rotation.py`

Expected modified files:

- `quant/infrastructure/research/cn_etf_universe.py`, if a dedicated broad-asset audited universe helper is needed.
- `quant/tests/test_research_pipeline.py`, if the universe helper needs coverage.
- `quant/features/strategies/AGENTS.md` or strategy invariant docs only if a new reusable invariant is added.

## Self-Review

- No placeholder requirements remain.
- The design matches the user-approved exclusions and CSI1000 inclusion.
- The strategy is scoped to a new reject-zone candidate and does not modify the existing gold-equity barbell default pool.
- The first implementation is intentionally not a parameter search or full-market ETF taxonomy strategy.
