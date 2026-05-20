# JoinQuant Wufu ETF Momentum

Fixed-pool baseline inspired by JoinQuant community "Wufu" ETF rotation discussions.

The strategy scores five ETFs with a 13-day weighted log-price regression score:

`annualized_slope_return * regression_r_squared`

It holds the highest-scoring ETF when the score is positive and no recent extreme Bollinger downside signal is detected. If all scores are non-positive it moves to `511880` as the cash-like fill ETF. The strict backtest uses close-generated signals with next-session execution through the project Backtester.

Default pool:

- `159915`
- `513100`
- `511010`
- `518880`
- `159980`
- fallback `511880`

`510180` is ingested as an alternate community pool member but is not used by the default baseline.
