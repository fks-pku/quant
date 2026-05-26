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
- `cn_fund_nav.duckdb::cn_fund_nav` stores ETF/LOF unit NAV, accumulated NAV, adjusted NAV, and asset size fields used to normalize fund splits in strict research providers.
- `cn_fund_nav.duckdb::cn_etf_share_size` stores Tushare ETF daily share/size snapshots when the upstream endpoint is available; it is preferred for point-in-time ETF size ranking.
- `cn_fund_meta.duckdb::cn_fund_instruments` stores full Tushare fund/ETF lifecycle metadata across listed, delisted, issuing, and pending statuses, including delist dates, manager/custodian, benchmark, invest type, ETF tracking index fields, and the stable `cn_fund_taxonomy_v1` classification columns.
- `cn_fund_meta.duckdb::cn_etf_benchmark_indices` stores the Tushare ETF benchmark index library when the upstream endpoint is available.
- `cn_index_ohlcv.duckdb::daily_cn_ochl` stores CN index OHLCV and is attached as `cn_index`.
- `cn_daily_basic.duckdb::cn_daily_basic` stores Tushare daily valuation, shares, market cap, and turnover sidecar fields.
- `cn_status.duckdb::cn_security_status_daily` stores daily listing/tradability/ST/suspension/limit status.
- `cn_financial_indicators.duckdb::cn_financial_indicators` stores Tushare `fina_indicator` quality/growth fields with `ann_date` and `end_date` for point-in-time joins.
- `cn_corporate_actions.duckdb::cn_dividends` stores CN dividend and allotment records.

## Security Status Data

- `quant/scripts/build_cn_security_status.py` rebuilds `var/duckdb/live/cn_status.duckdb::cn_security_status_daily` from read-only stock OHLCV plus Tushare `stock_basic`, `namechange`, `suspend_d`, `stk_limit`, and `trade_cal`.
- The status table is stored separately from `cn_ohlcv.duckdb`; keep DuckDB market reads `read_only=True` when rebuilding.
- `DuckDBStorage(use_security_status=True)` attaches the status DB read-only and filters by requested symbols/date range before joining, so backtests do not scan the whole status table unless the requested universe itself is whole-market.
- `quant.infrastructure.data.fund_classification.classify_cn_fund()` owns stable fund taxonomy. ETF/LOF rotation and barbell universes should consume `fund_category` or `category_group` plus point-in-time bar/size/tradability filters, not hand-picked symbols.
- Fund classification links `cn_fund_instruments.index_code` to `cn_etf_benchmark_indices.ts_code` and prefers Tushare `mkt_idx_bmk` fields for benchmark asset class/category before falling back to deterministic metadata rules.
