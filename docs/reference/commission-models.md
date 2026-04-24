# Commission Models Reference

## Per-Market Commission

| Market | Commission | Stamp Duty | Other Fees |
|--------|-----------|------------|------------|
| US | per-share $0.005 min $1 | — | — |
| HK | 0.03% min HK$3 | 0.13% on SELL | SFC levy + clearing + trading fee |
| CN | 0.025% min ¥5 | 0.05% on SELL | Transfer fee 0.001% |

## CN Market Notes

- Lot size: 100 shares (backtester enforces lot rounding)
- CN stocks (e.g. 600519 茅台 ~¥1700/share) require higher `initial_cash` (500K+)
- Default 100K is insufficient for high-price CN stocks
