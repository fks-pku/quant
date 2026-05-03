# Commission Models Reference

> Authoritative source: `quant/features/backtest/commission.py`

## Per-Market Commission

| Market | Commission | Stamp Duty | Other Fees |
|--------|-----------|------------|------------|
| US | per-share $0.005 min $1, or % of trade value | — | SEC fee 0.00278% (SELL), FINRA TAF $0.000166/share (SELL) |
| HK | 0.03% min HK$3 | 0.1% on BUY+SELL (math.ceil) | SFC levy 0.00278%, clearing fee 0.002%, trading fee 0.005%, system fee HK$0.50 |
| CN | 0.025% min ¥5 | 0.05% on SELL | Transfer fee 0.001%, regulator fee 0.002% |

## Execution Limits

- **Volume participation limit:** 5% of daily bar volume per order (`VOLUME_PARTICIPATION_LIMIT = 0.05`)

## CN Market Notes

- Lot size: 100 shares (backtester enforces lot rounding)
- CN stocks (e.g. 600519 茅台 ~¥1700/share) require higher `initial_cash` (500K+)
- Default 100K is insufficient for high-price CN stocks
