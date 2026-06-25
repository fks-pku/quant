# Domain Layer

## 职责

Pure business logic with zero external dependencies. Contains models, events, and ports.

## 对外契约

### Models (`quant.domain.models.*`)

- `Order` (frozen dataclass) — immutable trading order
- `Position` (mutable dataclass) — position tracking (quantity, P&L)
- `Trade` (frozen dataclass) — completed round-trip trade
- `Fill` (frozen dataclass) — order fill record
- `Bar` (frozen dataclass) — OHLCV market data
- `AccountInfo` (frozen dataclass) — account information
- `RiskCheckResult` (frozen dataclass) — risk check result

### Events (`quant.domain.events.*`)

- `Event` (base) — EventType, event_id, timestamp
- `OrderSubmittedEvent`, `OrderFilledEvent`, `OrderCancelledEvent`, `OrderRejectedEvent`
- `BarEvent`, `MarketOpenEvent`, `MarketCloseEvent`
- `StrategySignalEvent`
- `SystemStartEvent`, `SystemStopEvent`

### Ports (`quant.domain.ports.*`)

- `DataFeed` — `get_bars(symbol, start, end, timeframe)`, `subscribe(symbol, callback)`
- `BrokerAdapter` — `submit_order(order)`, `get_positions()`, `get_account()`
- `Strategy` — `on_bar(bar)`, `on_trade(trade)`, `on_fill(fill)`, `buy(symbol, quantity)`, `sell(symbol, quantity)`
- `Storage` — `save_bars(symbol, bars)`, `get_bars(symbol, start, end)`, `get_symbols()`, `get_lot_size(symbol)`
- `EventPublisher` — `subscribe(event_type, handler)`, `publish(event)`, `publish_nowait(event)`

### Context (`quant.domain.context`)

- `StrategyContext.execution_reference_resolver` — 策略上下文中的执行参考价解析器；策略不直接依赖 broker quote，实盘 MARKET 参考价由 trading Context/执行层解析
- `StrategyScopedOrderManager.submit_order(..., execution_timing=None)` — 策略可传订单级执行时点；非默认时点只会转发给显式支持的回测 order manager

## 依赖

None. Domain has zero external dependencies — the most important invariant.

## 不变量

- All domain models are either frozen dataclasses (immutable value objects) or mutable dataclasses with explicit mutability
- Ports return `Any` type — never `pd.DataFrame` — to maintain zero-dependency
- No external imports in domain/ tree

## 修改守则

- Change models: edit `quant/domain/models/`
- Change events: edit `quant/domain/events/`
- Change ports: edit `quant/domain/ports/`
- Never import from infrastructure/ or features/ into domain/

## Known Pitfalls

- Adding `pd.DataFrame` return types to ports would break the zero-dependency invariant — keep ports returning `Any`
- Events must be hashable if used in sets — use frozen dataclass for all Event subclasses

## Recent Additions

- `ResearchStore` is the domain port for research persistence, including candidates, seen hashes, artifacts, and hypothesis ledger records. It exposes read-only candidate listing for dashboards and feature code depends on this port only; file-backed and DuckDB implementations live in infrastructure.
- `research_source_catalog.json` holds pure discovery source defaults: source names, adapter kinds, dashboard visibility/display names, query terms, RSS feeds/source filters, and source-quality priors. `ResearchSourceCatalogEntry` and `research_source_catalog()` load and expose that config; infrastructure builds adapters from this catalog.
- `ResearchMarketData.get_universe_symbols(market)` supports full-universe research validation while still returning dependency-free types.
- `PITData` is the point-in-time research data port: `get_universe(as_of_date, market)` and `get_bars_pit(symbols, start, end, as_of_date)`.
- `BrokerAdapter.get_trade_history(start_date=None, end_date=None)` and `get_order_history(start_date=None, end_date=None)` are optional restart-reconciliation hooks. Default implementations return `[]`; concrete brokers can override them with dependency-free list/dict payloads.
