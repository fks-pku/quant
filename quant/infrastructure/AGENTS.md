# Infrastructure Layer

## 职责

Implements domain ports (adapters). Contains EventBus, data providers, storage implementations, and broker adapters.

## 对外契约

- `EventBus` — implements `EventPublisher` port
- `DuckDBStorage` — implements `Storage` port, supports `read_only=True`, bulk `get_bars_for_symbols()`, and optional CN security-status enrichment
- `TushareProvider`, `AkshareProvider`, `YfinanceProvider`, `DuckDBProvider` — implement `DataFeed` port
- `PaperBroker`, `FutuProvider` — implement `BrokerAdapter` port

## 依赖

- Depends on `domain/` — implements its ports
- No dependencies on `features/`

## 不变量

- DuckDB writers: `DuckDBStorage()` — default read-write
- DuckDB readers: `DuckDBStorage(read_only=True)`
- Never open write connections from API endpoints
- Providers fetch external data then write to Storage — never expose Storage as a Provider

## 修改守则

- Change event bus: edit `infrastructure/events/event_bus.py`
- Change storage: edit `infrastructure/data/storage_duckdb.py`
- Change providers: edit `infrastructure/data/providers/`
- Change broker adapters: edit `infrastructure/execution/brokers/`

## Known Pitfalls

- `DuckDBStorage` read_only=True must be used in API endpoints and backtest — prevents write-lock conflicts
- Provider subclasses should not import from features/ — only from domain ports
- Tushare provider requires token configuration in config.yaml

## Research Adapters

- `research/FileResearchStore` and `research/DuckDBResearchStore` implement the `ResearchStore` domain port, including hypothesis ledger upsert/list/get APIs.
- `research/market_data/DuckDBResearchMarketData` reads market-aware daily tables and can consume optional `PITData`.
- `research/factors` owns FF/CN factor cache and download/build adapters.
- `research/sources/SSRNSource` performs polite SSRN discovery and fails closed to `[]`.
- `research/pit_duckdb.PITDuckDBData` implements point-in-time universe and bar access with read-only DuckDB.
- Research feature code must receive it through dependency injection; infrastructure must not import from `features/`.

## DuckDB Data Layout

- Live mutable DuckDB data is stored under ignored `var/duckdb/live/`.
- `cn_ohlcv.duckdb::daily_cn_ochl` stores CN stock OHLCV only.
- `cn_etf_ohlcv.duckdb::daily_cn_ochl` stores CN ETF OHLCV and is attached as `cn_etf`.
- `cn_index_ohlcv.duckdb::daily_cn_ochl` stores CN index OHLCV and is attached as `cn_index`.
- `cn_daily_basic.duckdb::cn_daily_basic` stores Tushare daily valuation, shares, market cap, and turnover sidecar fields.
- `cn_status.duckdb::cn_security_status_daily` stores daily listing/tradability/ST/suspension/limit status.
- `cn_financial_indicators.duckdb::cn_financial_indicators` stores Tushare `fina_indicator` quality/growth fields with `ann_date` and `end_date` for point-in-time joins.
- `cn_corporate_actions.duckdb::cn_dividends` stores CN dividend and allotment records.

## Security Status Data

- `quant/scripts/build_cn_security_status.py` rebuilds `var/duckdb/live/cn_status.duckdb::cn_security_status_daily` from read-only stock OHLCV plus Tushare `stock_basic`, `namechange`, `suspend_d`, `stk_limit`, and `trade_cal`.
- The status table is stored separately from `cn_ohlcv.duckdb`; keep DuckDB market reads `read_only=True` when rebuilding.
- `DuckDBStorage(use_security_status=True)` attaches the status DB read-only and filters by requested symbols/date range before joining, so backtests do not scan the whole status table unless the requested universe itself is whole-market.
