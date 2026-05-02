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
