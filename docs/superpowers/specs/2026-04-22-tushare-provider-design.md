# Tushare Data Provider Design

## Summary

Add a Tushare data provider to the quant trading system, following the existing hexagonal architecture. The provider fetches CN stock daily bars and CN index daily bars via Tushare Pro API, caching all data in DuckDB for reuse.

## Architecture

```
domain/ports/data_feed.py          (unchanged — port interface stays pure)
         ↑ implements
infrastructure/data/providers/tushare.py   (TushareProvider)
         ↓ depends on
infrastructure/data/storage_duckdb.py      (DuckDB cache)
infrastructure/data/symbol_registry.py     (symbol format routing)
shared/config/config.yaml                  (token config)
```

Dependency direction: `infrastructure → domain` (compliant with hexagonal rules).

## Components

### 1. TushareProvider (`infrastructure/data/providers/tushare.py`)

- Extends `DataProvider` base class
- Methods:
  - `connect()` — validate token, initialize `tushare.pro_api`
  - `disconnect()` — no-op
  - `is_connected()` — return connection state
  - `get_bars(symbol, start, end, timeframe)` — fetch daily bars with DuckDB caching
  - `get_quote(symbol)` — fetch latest price via `daily` or `index_daily` API
- Rate limiting: 0.3s minimum between requests (respects Tushare ~200 req/min)
- Symbol routing:
  - Stock symbols (6-digit, prefix 0/3/6/8/9) → `daily` API
  - Index symbols (known codes like 000300, 000016, 000001, 399001, 399006) → `index_daily` API
- Tushare `ts_code` format: `{symbol}.{exchange}` where exchange is `SH` for 6/9-prefix, `SZ` for 0/3-prefix

### 2. DuckDB Cache Strategy

`get_bars()` flow:
1. Query DuckDB for existing bars in `[start, end]` range
2. If fully cached (covers entire date range): return cached data
3. If partially cached: compute missing date range, fetch only missing portion from Tushare, upsert into DuckDB, return merged result
4. If not cached: fetch full range from Tushare, save to DuckDB, return

All data stored in `daily_cn` table (matching existing table naming convention).

### 3. Column Mapping

Tushare → Internal format:

| Tushare Column | Internal Column | Notes |
|---|---|---|
| `ts_code` | `symbol` | Strip `.SH`/`.SZ` suffix (e.g. `600519.SH` → `600519`) |
| `trade_date` | `timestamp` | Parse `YYYYMMDD` string to datetime |
| `open` | `open` | Direct |
| `high` | `high` | Direct |
| `low` | `low` | Direct |
| `close` | `close` | Direct |
| `vol` | `volume` | Tushare uses `vol` (in 手/volume units) |
| `amount` | `turnover` | Tushare uses `amount` (in 千元) |

### 4. Config (`shared/config/config.yaml`)

Add under `data:` section:

```yaml
data:
  tushare:
    token: "YOUR_TUSHARE_TOKEN"
  providers:
    - yahoo
    - tushare
```

Token read via `ConfigLoader` at provider init time.

### 5. Ingest Script (`scripts/ingest_tushare.py`)

CLI tool mirroring `ingest_akshare.py`:
```
python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2024-01-01
```
Fetches from Tushare and saves directly to DuckDB. Accepts `--db-path` for custom database path.

### 6. Registry Updates

- `infrastructure/data/providers/__init__.py` — export `TushareProvider`
- `infrastructure/data/symbol_registry.py` — add `"tushare"` to `format_for_provider()` and `normalize()`
- `requirements.txt` — add `tushare>=1.4.0`

### 7. Tests

- `tests/test_tushare_provider.py` — unit tests with mocked Tushare API responses
  - Test stock daily bar fetching and normalization
  - Test index daily bar fetching and normalization
  - Test DuckDB cache hit/miss/partial scenarios
  - Test `ts_code` ↔ internal symbol conversion
  - Test rate limiting

## Index Symbol Detection

Known CN index codes (routed to `index_daily` API):

| Code | Name |
|---|---|
| 000001 | 上证指数 |
| 000016 | 上证50 |
| 000300 | 沪深300 |
| 000905 | 中证500 |
| 399001 | 深证成指 |
| 399006 | 创业板指 |
| 399673 | 创业板50 |

Detection rule: if symbol starts with `0` or `3` and is in the known index set, use `index_daily`; otherwise treat as stock.

## Files Changed

| File | Action |
|---|---|
| `infrastructure/data/providers/tushare.py` | **NEW** — TushareProvider class |
| `scripts/ingest_tushare.py` | **NEW** — CLI ingest script |
| `tests/test_tushare_provider.py` | **NEW** — unit tests |
| `infrastructure/data/providers/__init__.py` | **MODIFY** — add export |
| `infrastructure/data/symbol_registry.py` | **MODIFY** — add tushare routing |
| `shared/config/config.yaml` | **MODIFY** — add tushare token config |
| `requirements.txt` | **MODIFY** — add tushare dependency |
