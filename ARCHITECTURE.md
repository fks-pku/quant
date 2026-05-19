# Quant Trading System — Architecture

## Hexagonal Architecture Overview

Hexagonal (Ports & Adapters) + Event-Driven architecture. Domain is the center, Ports define interfaces, Infrastructure implements adapters, Features orchestrate use cases.

**Core Principle: DataFeed and Storage are separated**
- `DataFeed` port — fetches from external APIs (Tushare, Akshare, YFinance)
- `Storage` port — persists and queries cached data (DuckDBStorage)
- Providers fetch data then write to Storage; Backtest reads from Storage only
- DuckDB is not a Provider — it is a Storage implementation

## Directory Structure

```
quant/
├── domain/              # Pure business logic, zero external dependencies (CENTER)
│   ├── models/          # Domain models (Order, Position, Trade, Fill, Bar, AccountInfo)
│   ├── events/          # Domain events (EventType, Event, OrderEvents, MarketEvents...)
│   └── ports/           # Abstract interfaces (DataFeed, BrokerAdapter, Strategy, Storage, EventPublisher)
├── infrastructure/      # Implements domain ports (ADAPTERS)
│   ├── events/          # EventBus (implements EventPublisher)
│   ├── data/
│   │   ├── storage_duckdb.py  # DuckDBStorage (implements Storage port)
│   │   └── providers/        # External data fetchers (implement DataFeed port)
│   ├── execution/       # Broker adapters + order execution
│   └── var/             # Runtime data (DuckDB, gitignore)
├── features/            # Business use case orchestration (APPLICATION LAYER)
│   ├── backtest/        # Full backtest闭环
│   ├── trading/         # Live/paper trading
│   ├── portfolio/       # Position management
│   ├── cio/             # CIO market assessment
│   ├── strategies/      # Strategy framework + implementations
│   └── research/        # Quantitative strategy research
├── shared/              # Cross-feature pure shared utilities
│   ├── models/          # Compatibility layer → re-export from domain
│   ├── utils/           # Utilities (logger, config_loader, datetime_utils)
│   └── config/          # Configuration (config.yaml, brokers.yaml, strategies.yaml)
├── api/                 # Flask thin routing layer
├── frontend/            # React Dashboard UI
├── scripts/             # CLI utility scripts
└── tests/              # Tests
```

## Domain Layer

### Models (`quant.domain.models.*`)

| Model | Type | Description |
|-------|------|-------------|
| Order | frozen dataclass | Trading order, immutable value object |
| Position | mutable dataclass | Position tracking (quantity, P&L) |
| Trade | frozen dataclass | Completed round-trip trade |
| Fill | frozen dataclass | Order fill |
| Bar | frozen dataclass | OHLCV market data |
| AccountInfo | frozen dataclass | Account information |
| RiskCheckResult | frozen dataclass | Risk check result |

### Events (`quant.domain.events.*`)

| Event | Description |
|-------|-------------|
| Event (base) | Base event class (EventType, event_id, timestamp) |
| OrderSubmittedEvent | Order submitted |
| OrderFilledEvent | Order filled |
| BarEvent | Market data update |
| MarketOpenEvent / MarketCloseEvent | Market open/close |
| StrategySignalEvent | Strategy signal |
| SystemStartEvent / SystemStopEvent | System lifecycle |

### Ports (`quant.domain.ports.*`)

| Port | Description | Implementations |
|------|-------------|------------------|
| DataFeed | Data source interface (get_bars, subscribe) | TushareProvider, YfinanceProvider |
| BrokerAdapter | Broker interface (submit_order, get_positions) | PaperBroker, FutuProvider |
| Strategy | Strategy interface (on_data, buy, sell) | DailyBarStrategy + 7 active concrete strategies |
| Storage | Persistence interface (save_bars, get_bars, get_symbols, get_lot_size) | DuckDBStorage |
| EventPublisher | Event publish interface (subscribe, publish, publish_nowait) | EventBus |
| PortfolioLike | Portfolio contract (cash, positions, nav, update_position, reset_daily) | Portfolio, SubPortfolio |
| RiskEngineLike | Risk engine contract (check_order, record_order, reset_daily) | RiskEngine |
| LLMAdapterLike | LLM adapter contract (analyze) | OpenAIAdapter, ClaudeAdapter, OllamaAdapter, MiniMaxAdapter |
| ResearchStore | Research persistence interface (candidates, seen hashes, artifacts) | FileResearchStore |

## Architecture Invariants

1. **domain/ has zero external dependencies** — does not depend on any other layer
2. **features/ do not import infrastructure directly** — features depend on domain contracts and shared pure utilities; external adapters are injected by composition roots
3. **infrastructure/ implements domain ports** — depends on domain. Cannot import features. Cross-layer communication via event bus
4. **shared/ has no business semantics** — pure utilities (models/ only re-exports, no independent model classes)
5. **api/ only calls features**
6. **Cross-feature import is forbidden** — imports inside the same feature package are allowed; shared types (like RiskCheckResult) must be elevated to `domain/models/`
7. **Inter-layer communication**: direct call + Event Bus (pub/sub) + Dependency Injection (DI)
8. **domain ports return `Any` type**, not `pd.DataFrame` — keeps domain zero-dependency. pandas conversion happens in infrastructure layer

## Strategies (7 active)

All strategies extend `DailyBarStrategy` (in `features/strategies/daily_bar.py`) which provides
bar buffering, price helpers, rebalance gating, position liquidation, and serialization.

| Strategy | Directory | CN Compatible | Type |
|----------|-----------|---------------|------|
| a_share_range_contraction_breakout | `features/strategies/a_share_range_contraction_breakout/` | Yes | A-share breakout |
| a_share_liquidity_weighted_low_volatility | `features/strategies/a_share_liquidity_weighted_low_volatility/` | Yes | A-share low volatility |
| a_share_low_volatility_momentum | `features/strategies/a_share_low_volatility_momentum/` | Yes | A-share momentum |
| a_share_volatility_scaled_reversal | `features/strategies/a_share_volatility_scaled_reversal/` | Yes | A-share reversal |
| a_share_volume_exhaustion_reversal | `features/strategies/a_share_volume_exhaustion_reversal/` | Yes | A-share volume exhaustion |
| joinquant_small_cap_ma_stop | `features/strategies/joinquant_small_cap_ma_stop/` | Yes | A-share small-cap MA stop |
| worldquant_101_alpha_010 | `features/strategies/worldquant_101_alpha_010/` | Yes | WorldQuant Alpha 010 |

## Key Conventions

- Python 3.10+ with type hints
- No comments unless explicitly requested
- Frozen dataclasses for immutable value objects (domain models)
- ABC + abstract methods for ports (domain interfaces)
- Thread safety: `threading.RLock()` for shared state
- Logging: `from quant.shared.utils.logger import setup_logger`
- DuckDB readers must use `read_only=True` to prevent write-lock conflicts

## Module Boundaries

| Module | Responsibility | Key Files |
|--------|---------------|-----------|
| `domain/` | Pure business logic | models/, events/, ports/ |
| `infrastructure/` | External adapter implementations | events/, data/, execution/, research/ |
| `features/` | Use case orchestration | backtest/, trading/, portfolio/, cio/, strategies/, research/ |
| `shared/` | Cross-cutting utilities | utils/, config/, models/ |
| `api/` | HTTP routing | *_bp.py, state/runtime.py |
| `frontend/` | React UI | src/*.js |
