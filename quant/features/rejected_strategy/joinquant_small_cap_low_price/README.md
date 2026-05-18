# JoinQuant Small Cap Low Price

## Source
- **URL:** https://www.joinquant.com/community/post/detailMobile?postId=59884
- **Authors:** Codex Quant Research
- **Published:** 2026-05-17

## Evaluation
- **Suitability Score:** 6.76/10
- **Admission Score:** 6.76/10
- **Signal Quality Score:** 6.61/10
- **Complexity Score:** 3.0/10
- **Data Requirement:** low
- **Daily Adaptable:** True
- **Estimated Edge:** 6.0%
- **Type:** factor
- **Formula Key:** joinquant_small_cap_low_price_factor
- **Required Data Fields:** close, date, point_in_time_market_cap, symbol, volume
- **Validation Tests:** factor_decomposition, fdr_control, ic_decay, purged_walk_forward, rank_ic, transaction_cost_capacity

## Summary
factor idea triaged by deterministic professional rubric

## Execution Flow

- Universe: A-share daily bars from the research/backtest data provider.
- Eligibility: `2 <= close <= 20`, trailing 20-day average `turnover >= 20000`, valid point-in-time market cap, `is_st=False`, `tradable=True`, `is_listed=True`, and `list_status == "L"`.
- Signal: `1 / market_cap`; smaller market cap ranks higher.
- Portfolio: long-only Top 20, target gross exposure `1.0`, equal target value per selected name.
- Normal rebalance: controlled by `holding_days=5`.
- Delisting risk exit: before the normal rebalance gate, existing holdings are checked daily; if price, liquidity, ST, suspension, tradability, listing, or list-status guard fails, the strategy submits a SELL immediately.
- Strict execution: submitted orders still go through Backtester T+1, 100-share CN lot sizing, price-limit checks, volume caps, cash/position checks, commission, and slippage.

## Invariant Coverage

- `quant/tests/test_backtest_invariants.py` CASE-37 verifies that entry guards block risky names and that daily delisting-risk exits are not blocked by `holding_days`.
