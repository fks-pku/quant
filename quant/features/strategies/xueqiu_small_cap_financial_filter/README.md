# Xueqiu Small Cap Financial Filter

Source: https://xueqiu.com/7708198303/333999968

This candidate models the article's small-cap rule as a research-only A-share daily strategy:

- Universe: local full A-share stock universe with status sidecar.
- Entry filters: non-ST, tradable, listed, `list_status == L`, price and liquidity guard, point-in-time `total_mv/circ_mv >= 100000` in Tushare ten-thousand-CNY units, positive-profit proxy, and inferred revenue proxy `total_mv / ps_ttm >= 10000`.
- Ranking: smaller point-in-time market cap ranks higher.
- Portfolio: long-only Top 3-5 concentrated names.
- Timing approximation: Monday close signal, Tuesday open T+1 fill, because the local strict engine uses daily bars and cannot model Tuesday 10:00 intraday execution.
- Seasonal risk-off: January and April hold cash.
- Optional index risk-off: if configured, a short-window Shenzhen/ChiNext proxy drawdown can force cash.
- Position exits: status/delisting/low-liquidity exits run daily before rebalance; PnL exits are controlled by `risk_exit.enabled`, use portfolio `avg_cost` when available, then fallback to strategy fill state. The default enabled package combines volatility-adjusted stop loss, trailing take-profit after a 25% gain, and a 45-trading-day time stop for positions that have not earned at least 2%.

The local data does not contain absolute net profit or operating revenue. Positive profit is therefore proxied by positive `pe_ttm`, `pe`, `eps`, or `netprofit_margin`; operating revenue above RMB 100m is proxied by market cap divided by `ps_ttm`/`ps`. These approximations must be treated as residual model risk.

Exit defaults:

- `risk_exit.enabled=true` by default; formal reports must also run `risk_exit.enabled=false` as the no stop-loss/take-profit baseline.
- `stop_loss_pct=0.12`, widened by `3.0 * realized_daily_volatility_20`, bounded to `[0.08, 0.18]`.
- `take_profit_pct=0.25` arms a trailing exit; `trailing_stop_pct=0.10`, widened by `2.5 * realized_daily_volatility_20`, capped at `0.22`.
- `hard_take_profit_pct=0.0` is disabled by default because fixed profit caps usually truncate small-cap winners.
- `max_holding_days=45` exits stale positions when return is below `min_time_stop_return=0.02`.
