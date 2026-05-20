# Symbol Registry Reference

## Market Symbol Patterns

| Market | Code Pattern | Example | DuckDB Table |
|--------|--------------|---------|--------------|
| US | Letters | `AAPL`, `SPY` | `daily_us` / `minute_us` |
| HK | 5-digit numeric | `00700` | `daily_hk` / `minute_hk` |
| CN stock | 6-digit numeric A-share stock | `600519` | `cn_ohlcv.duckdb::daily_cn_ochl` / `minute_cn` |
| CN ETF | 6-digit exchange-traded fund prefixes `15/51/56/58` | `510300`, `159915` | `cn_etf_ohlcv.duckdb::daily_cn_ochl` |
| CN index | configured benchmark/index symbols | `000300`, `399006` | `cn_index_ohlcv.duckdb::daily_cn_ochl` |
