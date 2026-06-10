# Infrastructure Layer

## 职责

Implements domain ports (adapters). Contains EventBus, data providers, storage implementations, and broker adapters.

## 对外契约

- `EventBus` — implements `EventPublisher` port
- `DuckDBStorage` — implements `Storage` port, supports `read_only=True`, bulk `get_bars_for_symbols()`, optional CN security-status enrichment, and local Parquet lake views for read-only default CN data
- `ParquetLakeStorage` — writes day-partitioned lake datasets under `var/parquet_lake/`; use it when Parquet/OSS is the source of truth and DuckDB is only the query engine/cache
- `TushareProvider`, `AkshareProvider`, `YfinanceProvider`, `DuckDBProvider` — implement `DataFeed` port
- `PaperBroker`, `FutuProvider`, `QMTBroker` — implement `BrokerAdapter` port
- `LiveTradingRecorder` — writes daily JSONL live records for strategy signals, broker orders, fills, and strategy snapshots under `infrastructure/var/live_trading/`; performance views are derived with `quant.analytics`
- `strategy_controls.py` — persists per-strategy live control state under `infrastructure/var/strategy_controls.json`; dashboard actions update this file and never submit broker orders directly
- `LiveExecutionManager` — converts live target signals into cost-bounded LIMIT orders and drops unfilled orders after the configured intraday deadline

## 依赖

- Depends on `domain/` — implements its ports
- No dependencies on `features/`

## 不变量

- DuckDB writers: `DuckDBStorage()` — default read-write legacy sidecar backend
- Parquet lake writers: `ParquetLakeStorage()` — partition upsert backend for Parquet-native daily data
- DuckDB readers: `DuckDBStorage(read_only=True)`; when the default local Parquet lake manifest exists, default read-only storage treats the lake as truth and registers DuckDB views instead of opening mutable sidecars
- Never open write connections from API endpoints
- Providers fetch external data then write to Storage — never expose Storage as a Provider

## 修改守则

- Change event bus: edit `infrastructure/events/event_bus.py`
- Change DuckDB storage: edit `infrastructure/data/storage_duckdb.py`
- Change Parquet lake storage: edit `infrastructure/data/parquet_lake_storage.py`
- Change providers: edit `infrastructure/data/providers/`
- Change broker adapters: edit `infrastructure/execution/brokers/`

## Known Pitfalls

- `DuckDBStorage` read_only=True must be used in API endpoints and backtest — prevents write-lock conflicts and allows the local Parquet lake to act as the default read source
- Provider subclasses should not import from features/ — only from domain ports
- Tushare provider requires token configuration in config.yaml
- Tushare per-symbol dividend refresh must treat API rate limits as retryable failures, not as empty dividend histories.
- QMT/MiniQMT uses `userdata_mini_path` for `XtQuantTrader(path, session_id)` and optional `xtquant_path` only for importing the SDK; do not pass the SDK site-packages path as the MiniQMT data path.
- QMT trade APIs require a `StockAccount` object and `subscribe(account)` before asset, position, order, and trade callbacks are reliable.
- QMT trade callbacks must pass fills through the registered callback chain so `FillHandler`, strategy attribution, and `LiveTradingRecorder` stay in sync.
- QMT trade callbacks must include commission; when MiniQMT does not provide a fee field, estimate CN A-share/ETF commission with the configured rate and a CNY 5 minimum so live strategy costs match broker cost basis.
- QMT `trade_mode=SIMULATE` is not a verified sandbox order route and must refuse `submit_order()`; paper trading should use `PaperBroker`, while QMT order submission is reserved for confirmed `REAL` live runs.
- QMT `FIX_PRICE` orders must be quantized at the broker boundary to the exchange tick; BUY rounds down and SELL rounds up so cost-bounded limits stay bounded.
- `PaperBroker` is the only paper-trading execution adapter; it consumes local execution-date bars, applies backtest-style open/LIMIT fill rules, computes commission through `quant.runtime.execution_commission`, debits paper cash/cost basis with that commission, and emits local trade callbacks for the shared fill recorder.
- Live execution adapters must stay feature-agnostic: strategy-level risk/portfolio ownership is supplied by injected resolvers from the composition root, not by importing `features/`.
- Cost-bounded live execution must preserve explicit LIMIT prices; only strategy `MARKET + reference price` signals are converted to bounded LIMIT orders.
- Bare 6-digit CN codes can be ambiguous between stocks and indices (for example `000001`, `000016`, `000905`). Default provider/storage routing treats them as stocks; index ingestion must use `TushareProvider.fetch_index_daily_with_hfq()` and `DuckDBStorage.save_cn_index_bars()`.
- Tushare dividend rows can contain repeated lifecycle records for the same `(symbol, ex_date)`. `DuckDBStorage.save_cn_dividends()` must coalesce them before writing because `cn_dividends` is keyed by `(symbol, ex_date)`.

## Research Adapters

- `research/FileResearchStore` and `research/DuckDBResearchStore` implement the `ResearchStore` domain port, including hypothesis ledger upsert/list/get APIs.
- `research/market_data/DuckDBResearchMarketData` reads market-aware daily tables and can consume optional `PITData`.
- `research/factors` owns FF/CN factor cache and download/build adapters.
- `research/sources/SSRNSource` performs polite SSRN discovery and fails closed to `[]`.
- `research/sources/ASharePublicForumSource` turns audited BigQuant/JoinQuant public strategy seeds into low-frequency research ideas and marks them for manual replication.
- `research/pit_duckdb.PITDuckDBData` implements point-in-time universe and bar access with read-only DuckDB.
- Research feature code must receive it through dependency injection; infrastructure must not import from `features/`.

## DuckDB Data Layout

- Live mutable DuckDB data is stored under ignored `var/duckdb/live/`; local Parquet lake truth is stored under ignored `var/parquet_lake/`.
- `cn_ohlcv.duckdb::daily_cn_ochl` stores CN stock OHLCV only.
- `cn_etf_ohlcv.duckdb::daily_cn_ochl` stores CN ETF OHLCV and is attached as `cn_etf`.
- `cn_fund_nav.duckdb::cn_fund_nav` stores ETF/LOF unit NAV, accumulated NAV, adjusted NAV, and asset size fields used to normalize fund splits in strict research providers.
- `cn_fund_meta.duckdb::cn_fund_instruments` stores full Tushare fund/ETF lifecycle metadata across listed, delisted, issuing, and pending statuses, including delist dates, manager/custodian, benchmark, invest type, ETF tracking index fields, and the stable `cn_fund_taxonomy_v1` classification columns.
- `cn_index_ohlcv.duckdb::daily_cn_ochl` stores CN index OHLCV and is attached as `cn_index`.
- `cn_daily_basic.duckdb::cn_daily_basic` stores Tushare daily valuation, shares, market cap, and turnover sidecar fields.
- `cn_status.duckdb::cn_security_status_daily` stores daily listing/tradability/ST/suspension/limit status.
- `cn_financial_indicators.duckdb::cn_financial_indicators` stores Tushare `fina_indicator` quality/growth fields with `ann_date` and `end_date` for point-in-time joins.
- `cn_industry_membership.duckdb::cn_industry_membership` stores SW industry membership intervals and L1/L2/L3 codes/names for research formulas that require industry-aware daily signals.
- `cn_corporate_actions.duckdb::cn_dividends` stores CN dividend and allotment records.
- `quant/scripts/update_cn_live_data.py` is the daily live-data updater. It incrementally updates existing stock/index/ETF symbols and then fills valuation, financial, status, and index-weight sidecars to the target date. Stock bars and ETF bars default to date-based all-market fetches; ETF `adj_factor` is carried forward from the latest local value. Full dividend-history refresh (`--refresh-dividends` or `--dividends-only`) and per-fund NAV refresh (`--refresh-fund-nav` or `--nav-only`) are opt-in because they are too slow for daily all-market runs. Use `--storage-backend parquet-lake --sync-parquet-lake` for Parquet-native daily writes and touched-partition OSS sync.
- `quant/scripts/publish_parquet_lake.py snapshot` is the routine DuckDB-to-OSS publisher. Date-bearing datasets are exported as day partitions (`year=YYYY/month=MM/day=DD/data.parquet`) and uploaded through `rclone`. On another machine, run `pull`; default read-only `DuckDBStorage` can query the pulled lake directly through DuckDB views. `restore --force` is now only for tools that still require materialized `.duckdb` sidecars.

## Security Status Data

- `quant/scripts/build_cn_security_status.py` rebuilds `var/duckdb/live/cn_status.duckdb::cn_security_status_daily` from read-only stock OHLCV plus Tushare `stock_basic`, `namechange`, `suspend_d`, `stk_limit`, and `trade_cal`.
- The status table is stored separately from `cn_ohlcv.duckdb`; keep DuckDB market reads `read_only=True` when rebuilding.
- `DuckDBStorage(use_security_status=True)` attaches the status DB read-only and filters by requested symbols/date range before joining, so backtests do not scan the whole status table unless the requested universe itself is whole-market.
- `quant.infrastructure.data.fund_classification.classify_cn_fund()` owns deterministic metadata taxonomy for inspection only. Strategy universes must not auto-expand from current broad ETF taxonomy when the strategy is promoted.
- `quant.infrastructure.research.cn_etf_universe` owns `audited_stable_etf_registry_v1`. ETF category strategies that use representative buckets must consume this user-approved registry and report `registered_universe_counts`; adding a new ETF category requires explicit audit and registry update.

## Recent Additions

- `execution/strategy_controls.py` persists per-strategy and per-mode control state in `var/strategy_controls.json`; dashboard control actions must append an audit row and must not submit broker orders directly.
- `execution/strategy_ledger.py` owns strategy operations ledgers, dashboard audit JSONL, mode-scoped liquidation plans, and optional broker-history reconciliation after a live restart.
- `execution/strategy_mode_records.py` owns legacy append-only `strategy_modes/<mode>/<strategy_name>/` JSONL records for migration compatibility; these records are raw inputs to the strict state store, not the dashboard state source.
- `execution/strategy_state_store.py` owns the strict DuckDB dashboard state store (`var/strategy_state.duckdb`) with tables for operations, runs, control state, capital events, signals, submit attempts, orders, fills, positions, NAV snapshots, watermarks, and reconciliations. Dashboard state reads must use this store after migrating JSONL inputs.
- `execution/cn_trading_calendar.py` owns CN trading-calendar resolution for live/paper schedulers, combining Tushare `trade_cal` cache with local DuckDB market/status dates.
- Broker adapters may expose `get_trade_history(start_date=None, end_date=None)` and `get_order_history(start_date=None, end_date=None)` for restart reconciliation. Implementations must return normalized dict-like records without importing `features/`.
- Strategy dashboard audit, liquidation plans, and broker-history reconciliation are always scoped by `strategy_name` and `mode`; sharing one real broker account must not merge virtual strategy state.
