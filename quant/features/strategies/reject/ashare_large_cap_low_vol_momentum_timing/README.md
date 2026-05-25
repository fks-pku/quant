# A-share Large Cap Rule-Based Trend Rotation

This strategy upgrades the earlier single-stock Wuliangye (`000858`) MA60/stop-loss timing idea into a rule-based A-share large-cap trend rotation.

There is no fixed stock whitelist. Each rebalance day starts from the supplied A-share universe, filters ST/suspension/list-status/price/liquidity, then keeps the point-in-time top market-cap band. Candidates must pass adjusted-price trend, long and recent momentum, volatility, recent drawdown, and PB/PS valuation filters before being ranked.

The default score profile is `rule_based_quality_trend`, combining trend strength, 12-1 momentum, recent momentum, lower volatility, shallower drawdown, lower PB, and lower PS. The production candidate configuration disables the broad `000300` timing gate and instead applies each stock's own adjusted-price MA60 trend filter.

Exposure is capped by `target_weight_slots`: the total target exposure is split across the intended number of slots even when fewer names are active, so a sparse signal day does not turn the strategy back into a single-stock bet.

The target research profile is annualized return above 10% and max drawdown no worse than -40% under the project Backtester with T+1 execution, CN lot size, realistic commission/taxes, limit/suspension constraints, and liquidity impact costs.

Latest strict research after removing the fixed eight-stock basket:

- Report: `quant/infrastructure/var/research/reports/ashare_large_cap_low_vol_momentum_timing/strict_backtest_report.html`
- Universe mode: latest total_mv Top 500 for fast iteration, with point-in-time cap banding inside the strategy
- Best non-fixed scenario: `top20_momentum_lowvol_ma200`
- Best CAGR: 2.41%
- Best max drawdown: -31.55%
- Rule-based quality trend scenarios: did not pass, with negative CAGR in the tested Top 20% and monthly variants

The previous fixed eight-stock basket passed the numeric target, but it was removed because the stock list itself encoded too much sample selection. The current rule-based candidate is more honest and less overfit-prone, but it does not meet the 10% CAGR target yet.

The strategy is not enabled for live trading. It remains `status: candidate` and should stay out of paper/live trading until a rule-based version passes strict backtest, walk-forward validation, capacity review, and portfolio allocation review.
