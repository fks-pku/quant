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

The local data does not contain absolute net profit or operating revenue. Positive profit is therefore proxied by positive `pe_ttm`, `pe`, `eps`, or `netprofit_margin`; operating revenue above RMB 100m is proxied by market cap divided by `ps_ttm`/`ps`. These approximations must be treated as residual model risk.
