# Symbol Registry Reference

## Market Symbol Patterns

| Market | Code Pattern | Example | DuckDB Table |
|--------|--------------|---------|--------------|
| US | Letters | `AAPL`, `SPY` | `daily_us` / `minute_us` |
| HK | 5-digit numeric | `00700` | `daily_hk` / `minute_hk` |
| CN | 6-digit numeric (0/3/6/8/9 prefix) | `600519` | `daily_cn` / `minute_cn` |
