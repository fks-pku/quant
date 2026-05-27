---
name: quant-strategy-bias-audit
description: Use when developing, reviewing, backtesting, tuning, comparing, promoting, or reporting quant strategies; choosing universes; interpreting PIT data; or asking whether a strategy has lookahead, survivorship, universe, overfitting, execution, capacity, validation, or reporting bias.
---

# Quant Strategy Bias Audit

## Core Principle

Treat every attractive backtest as guilty until the research record proves what was knowable, tradable, and reproducible at each decision time.

The goal is not to eliminate every possible bias. The goal is to identify which biases are structurally removed, which are tested by evidence, and which remain as explicit residual risk before a strategy is promoted.

## When To Use

Use this skill before or during:

- Creating or modifying a quant strategy.
- Choosing symbols, ETF categories, stock universes, benchmarks, or data filters.
- Running fast research, strict backtest, walk-forward, grid search, or full research reports.
- Explaining whether a strategy can go online.
- Comparing strategy variants or selecting parameters.
- Investigating suspiciously good Sharpe, low drawdown, high win rate, low turnover, or clean regime behavior.

## Required Output

When auditing a strategy, produce this structure:

```markdown
## Bias Audit Verdict
Overall: Pass / Warning / Fail

## Bias Checklist
| Bias | Status | Evidence | Residual Risk | Required Fix |

## Key Findings
1. ...

## Go / No-Go Impact
...
```

Use these statuses:

- `Pass`: structurally prevented or directly tested with clean evidence.
- `Warning`: partly mitigated but residual risk remains.
- `Fail`: likely contaminated, missing evidence, or impossible-to-trade assumption.
- `N/A`: genuinely not applicable, with reason.

## Fast Triage Questions

Ask these before trusting any result:

1. Could every input have been known at the signal timestamp?
2. Could every instrument have been known, listed, and tradable at that date?
3. Would missing, delisted, suspended, or failed instruments change the result?
4. Are parameters, filters, and universes chosen before seeing test performance?
5. Are execution prices, costs, liquidity, lot size, limits, and cash constraints realistic?
6. Does walk-forward or OOS evidence survive bad regimes, not only the full sample?
7. Are failed variants, rejected hypotheses, and warnings preserved in the report?
8. If a stop-loss/take-profit package exists, is there a same-assumption off/on comparison, and were those parameters fixed before the final validation?

## Bias Taxonomy And Controls

### 1. Look-Ahead Bias

Risk: using future close, future constituents, restated fundamentals, future NAV, future corporate actions, or same-day execution assumptions that were unavailable at decision time.

Controls:

- Separate signal time, order time, and fill time.
- Use `execution_lag_days >= 1` for daily A-share strategies unless explicitly justified.
- Use point-in-time fields and `as_of` dates for fundamentals, ETF NAV/size, index membership, and status.
- In code, reject bars or metadata with dates after the decision date.
- In reports, state signal timestamp and fill timestamp.

Red flags:

- Close-to-close trading without next-day execution.
- Using final index members for historical membership.
- Using latest fund size, latest market cap, latest classification, or latest delist status across the whole history.

### 2. Survivorship Bias

Risk: only surviving stocks, ETFs, funds, or data records remain, so failed or delisted instruments are absent.

Controls:

- Use historical listing/status tables, delist dates, and inactive instruments where available.
- Audit data coverage against raw bar tables and metadata tables.
- Report missing metadata counts and missing delist markers.
- Treat `no delist markers` or `bar-only symbols missing metadata` as a warning or fail depending on materiality.

Project-specific expectation:

- For ETF strategies, report ETF metadata survivorship audit: ETF bar symbol count, fund metadata count, missing metadata symbols, and delist marker coverage.

### 3. Universe Selection Bias

Risk: the strategy uses a hand-picked list of winners, current top names, current liquid names, or a universe selected after viewing performance.

Controls:

- Define universe construction before performance evaluation.
- Prefer dynamic point-in-time universe rules over fixed winner lists.
- Require `list_date <= decision_date`; respect delist/status where available.
- For ETF/category strategies, classify categories by metadata known by the date, then filter by that date's bar, PIT size, liquidity, and lookback availability.
- Report universe policy, backtest window, symbol count, sample symbols, and residual coverage risk.

Important distinction:

- A PIT dynamic universe can remove fixed-list selection bias while still carrying residual survivorship risk if underlying metadata lacks delisted or failed instruments.

### 4. Data Snooping, Multiple Testing, And P-Hacking

Risk: many ideas, filters, parameters, dates, assets, and benchmarks are tried until one looks good.

Controls:

- Preserve rejected variants and failed hypotheses.
- Separate discovery, fast validation, strict backtest, and walk-forward.
- Penalize grid searches with deflated Sharpe ratio, multiple-testing notes, or stricter OOS requirements.
- Lock parameters before final OOS evaluation.
- Do not use the same walk-forward failures to repeatedly tune the strategy without resetting validation.
- Treat stop-loss, take-profit, trailing stop, and time-stop thresholds as parameters. Require a no-risk-exit baseline and disclose whether the risk-exit package was designed before or after seeing the equity curve.

Red flags:

- "Best" parameter chosen from a large grid with no penalty.
- Report only shows the winning run.
- Rules changed after seeing the equity curve.
- Stop-loss/take-profit rules are added after inspecting drawdowns, but the report only shows the improved version.

### 5. Overfitting And Regime Bias

Risk: strategy fits one sample, one crisis, one bull market, one volatility regime, or one instrument family.

Controls:

- Use walk-forward or purged OOS splits.
- Show worst split, profitable split ratio, no-trade split count, and regime breakdown.
- Require performance to survive adverse regimes or explicitly label strategy as regime-dependent.
- Check whether return comes from one period or one event.

Red flags:

- High full-sample Sharpe but negative worst OOS Sharpe.
- Profits concentrated in one year.
- No-trade OOS windows hidden from the report.

### 6. Execution And Microstructure Bias

Risk: backtest assumes fills that could not happen in real trading.

Controls:

- Include commissions, slippage, spread, market impact, lot size, cash, T+1, suspension, limit-up/limit-down, and volume participation limits.
- Use next tradable bar and realistic order sizing.
- Report rejected orders, insufficient cash, volume-limited trades, lot adjustments, and cost drag.

A-share specifics:

- Long-only by default unless legal shorting or hedging instrument is explicitly implemented.
- Respect 100-share lots, T+1, daily price limits, suspension, and fund/ETF commission rules.

### 7. Liquidity And Capacity Bias

Risk: alpha exists only at tiny size or assumes unlimited volume.

Controls:

- Report max participation as percent of ADV.
- Run capacity gate with target capital and max ADV threshold.
- Treat missing volume as fail unless the strategy proves it holds cash or uses non-volume instruments.
- Recompute costs with market impact, not only flat bps.

### 8. Corporate Action And Adjustment Bias

Risk: prices, returns, shares, dividends, splits, ETF accumulated NAV, or adjusted factors are inconsistent.

Controls:

- Use adjusted prices for signal/validation where appropriate.
- Use executable raw prices for fills when the engine models corporate actions separately.
- Verify quantity adjustments for splits/fund adjustments.
- Report whether signal uses `adj_close`, `adj_factor`, NAV, or raw close.

### 9. Benchmark And Metric Selection Bias

Risk: benchmark, date range, or metrics are chosen to flatter the strategy.

Controls:

- Pick benchmark by market/universe before seeing performance.
- Report CAGR, Sharpe, Sortino, Calmar, max drawdown, turnover, win rate, profit factor, tail risk, and yearly calendar.
- Show benchmark coverage and fallback logic.
- Do not promote based on one headline metric.

### 10. Cross-Strategy Correlation And Portfolio Context Bias

Risk: a strategy looks good alone but duplicates existing exposure, factor beta, or regime risk.

Controls:

- Compare returns against existing promoted strategies.
- Estimate correlation, beta, sector/style/factor exposure, and drawdown overlap.
- Require low-correlation evidence when the strategy is meant to diversify.

## Development Workflow

### Before Coding

Document:

- Economic hypothesis.
- Tradable universe construction.
- Data fields and point-in-time requirements.
- Signal timestamp, rebalance timestamp, order timestamp, and fill timestamp.
- Execution assumptions and risk budget.
- Expected residual biases.

If any of these are unknown, mark the strategy as research-only.

### Before Strict Backtest

Check:

- No future fields enter `on_data`, `on_before_trading`, or `on_after_trading`.
- Strategy rejects instruments without current bar, PIT data, liquidity, and lookback.
- Constructor parameters do not silently fall back to stale fixed symbols.
- Backtest runner loads every symbol needed by the strategy, benchmark, timing leg, hedge leg, and defensive leg.
- Cost model matches asset class.

### Before Walk-Forward

Check:

- Same strategy parameters and universe rules are reused.
- Splits are purged or otherwise protected from label leakage.
- Split-level no-trade windows are disclosed.
- Worst OOS Sharpe and profitable split ratio are included.
- Capacity is checked on split trades, not only full-sample trades.

### Before Promotion

Require:

- Bias checklist in the report.
- Strict backtest pass or documented exception.
- Walk-forward pass, or explicit warning/no-go if OOS stability fails.
- Survivorship and universe audit.
- Capacity and cost audit.
- Stop-loss/take-profit off/on comparison with trigger attribution when the strategy includes a risk-exit package.
- Correlation/factor exposure review if strategy will join a portfolio.

## Common Mistakes

- Saying "PIT universe means no bias" when metadata coverage still lacks delisted instruments.
- Filtering by today's liquidity or today's market cap for historical trades.
- Tuning universe filters after seeing report metrics.
- Treating no-trade OOS splits as if they never happened.
- Using a fixed ETF list because those products are familiar.
- Comparing a strategy to a weak benchmark chosen after the fact.
- Reporting only the full-sample equity curve and hiding calendar returns, split returns, or rejected orders.

## Good Audit Language

Use precise wording:

- "Fixed-list universe selection bias is mitigated by dynamic PIT eligibility."
- "Residual survivorship risk remains because delisted metadata coverage is incomplete."
- "Look-ahead is structurally controlled by current-bar checks, PIT date filters, and T+1 execution."
- "OOS stability fails because worst split is below threshold, despite acceptable full-sample Sharpe."
- "Capacity passes at the tested capital but must be rechecked before capital scaling."

Avoid overclaiming:

- Do not say "no bias" unless every relevant data source is point-in-time complete and audited.
- Do not say "tradable" unless execution, liquidity, and cost constraints are modeled.
- Do not say "robust" unless walk-forward and regime evidence support it.
