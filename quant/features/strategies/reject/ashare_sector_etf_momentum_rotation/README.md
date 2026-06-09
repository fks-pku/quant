# A-share Sector ETF Momentum Rotation

Candidate strategy for rotating among CN-listed sector and theme ETFs.

The signal ranks one representative ETF per sector category by risk-adjusted momentum, with liquidity, positive momentum, and trend filters. Orders are submitted from the close signal and are executed by the project backtester on the next trading step.

Default research status is `candidate` and `enabled: false` because local sector ETF history is currently short for many symbols. The strategy must remain outside the promoted strategy pool until strict backtest, walk-forward, stability, execution, capacity, and PIT coverage checks are acceptable.

References:

- https://bigquant.com/wiki/doc/DlXVSO3ZVu
- https://bigquant.com/square/paper/bb877968-2f1a-438a-bae8-6a89bdc04d08
- https://bigquant.com/square/paper/4ad7a69b-c6b0-4658-8c7e-5f395659576c
- https://onlinelibrary.wiley.com/doi/pdf/10.1111/0022-1082.00146
