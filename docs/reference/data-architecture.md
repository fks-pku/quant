# Data Architecture Reference

## Two-Port Separation

```
DataFeed port (fetch from external)     Storage port (persist & query)
┌─────────────────────┐                ┌─────────────────────┐
│ TushareProvider     │──write──────→  │                     │
│ AkshareProvider     │──write──────→  │   DuckDBStorage     │
│ YfinanceProvider    │──write──────→  │   (implements       │
└─────────────────────┘                │    Storage port)     │
                                       │                     │
┌─────────────────────┐                │                     │
│ Backtester          │←──read────────│                     │
│ API endpoints       │←──read────────│                     │
└─────────────────────┘                └─────────────────────┘
```

- **Providers** fetch from external APIs, cache via Storage
- **Backtest** reads from Storage only — doesn't care where data came from
- **API layer** reads from Storage (read-only mode)

## Providers

| Provider | File | Markets | Storage |
|----------|------|---------|---------|
| TushareProvider | `infrastructure/data/providers/tushare.py` | CN | DuckDB (via Storage port) |
| AkshareProvider | `infrastructure/data/providers/akshare.py` | CN | — |
| YfinanceProvider | `infrastructure/data/providers/yfinance_provider.py` | US | Parquet |

## Storage

| Storage | File | Description |
|---------|------|-------------|
| DuckDBStorage | `infrastructure/data/storage_duckdb.py` | Implements Storage port. Supports `read_only=True` for readers. |

## DuckDB Connection Rules

- **Writers** (ingest scripts, providers): `DuckDBStorage()` — default read-write
- **Readers** (API, backtest, providers reading cache): `DuckDBStorage(read_only=True)`
- Only ingest scripts and providers with fresh data should write
- **Never** open write connections from API endpoints — prevents data corruption

## Tushare Configuration

```yaml
data:
  tushare:
    token: "YOUR_TUSHARE_TOKEN"
    api_url: "http://..."   # optional custom server URL
```

## CN Market Notes

- Lot size: 100 shares (backtester enforces lot rounding)
- CN stocks (e.g. 600519 茅台 ~¥1700/share) require higher `initial_cash` (500K+)
- Default 100K is insufficient for high-price CN stocks
