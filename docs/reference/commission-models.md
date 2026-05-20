# Commission Models Reference

> Authoritative source: `quant/features/backtest/commission.py`

## Per-Market Commission

| Market | Commission | Stamp Duty | Other Fees |
|--------|-----------|------------|------------|
| US | per-share $0.005 min $1, or % of trade value | — | SEC fee 0.00278% (SELL), FINRA TAF $0.000166/share (SELL) |
| HK | 0.03% min HK$3 | 0.1% on BUY+SELL (math.ceil) | SFC levy 0.00278%, clearing fee 0.002%, trading fee 0.005%, system fee HK$0.50 |
| CN stock | 0.025% min CNY 5 | 0.05% on SELL after 2023-08-28, 0.1% before | Transfer fee 0.001%, regulator fee 0.002% |
| CN ETF/LOF/fund | default research backtest: 0.01% min CNY 0; configurable via `fund_percent` / `fund_min_per_order` | none | Stock transfer/regulator fee keys are reported as 0.0 |

## Execution Limits

- **Volume participation limit:** 5% of daily bar volume per order (`VOLUME_PARTICIPATION_LIMIT = 0.05`)

## CN Market Notes

- Lot size: 100 shares (backtester enforces lot rounding)
- ETF/LOF/fund code prefixes route to the CN fund commission path and do not pay stock stamp duty.
- CN stocks (e.g. 600519 茅台 ~¥1700/share) require higher `initial_cash` (500K+)
- Default 100K is insufficient for high-price CN stocks
