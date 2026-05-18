# JoinQuant Small Cap MA Stop

Source: https://www.joinquant.com/view/community/detail/cc21565a660487b31666dc40a6aa9ecd?type=1

This strategy implements the article's daily A-share logic:

- Universe: broad Shanghai and Shenzhen A-share universe supplied by the runner.
- Selection: rank by point-in-time market cap ascending.
- Portfolio: hold the smallest `max_positions` names, equal weighted.
- Risk exit: sell a held name when the short moving average crosses below the long moving average.
- Default parameters: 10-day short MA, 50-day long MA, 20 positions.

The current `daily_cn_ochl` schema in this workspace has OHLCV and turnover, but no market cap column. The strategy therefore requires `total_mv`, `circ_mv`, `market_cap`, or another configured market-cap field to be present in the incoming bar records. When market-cap data is missing it does not trade, avoiding an accidental liquidity proxy backtest.
