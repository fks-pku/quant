# Infrastructure Layer

## 职责

Implements domain ports (adapters). Contains EventBus, data providers, storage implementations, and broker adapters.

## 对外契约

- `EventBus` — implements `EventPublisher` port
- `DuckDBStorage` — implements `Storage` port, supports `read_only=True`
- `TushareProvider`, `AkshareProvider`, `YfinanceProvider` — implement `DataFeed` port
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
