# Agent-Friendly MD System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the documentation hierarchy following DevClaw's L0-L4 progressive disclosure model: slim root AGENTS.md (~150 lines), add ARCHITECTURE.md, add docs/AGENTS.md index, create 5 new module AGENTS.md files, extract reference docs to docs/reference/, consolidate two docs/ directories.

**Architecture:** The plan extracts reference material from root AGENTS.md into dedicated docs/reference/ files, creates a new ARCHITECTURE.md for code-map content, slims root AGENTS.md to routing/constraints only, adds docs/AGENTS.md as L1 navigation index, writes 5 new module AGENTS.md files for domain/infrastructure/api/frontend/shared, migrates historical docs, and removes outdated files.

**Tech Stack:** Markdown only — no code changes, only documentation reorganization.

---

## Task 1: Create docs/reference/ Directory and Extract Data Architecture Reference

**Files:**
- Create: `docs/reference/data-architecture.md`
- Create: `docs/reference/symbol-registry.md`
- Create: `docs/reference/commission-models.md`
- Modify: `docs/superpowers/specs/2026-04-24-agent-friendly-md-system-design.md` (add file inventory to spec)

- [ ] **Step 1: Create docs/reference/ directory**

Run: `mkdir -p docs/reference`

- [ ] **Step 2: Write docs/reference/data-architecture.md**

```markdown
# Data Architecture Reference

## Two-Port Separation

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
```

- [ ] **Step 3: Write docs/reference/symbol-registry.md**

```markdown
# Symbol Registry Reference

## Market Symbol Patterns

| Market | Code Pattern | Example | DuckDB Table |
|--------|--------------|---------|--------------|
| US | Letters | `AAPL`, `SPY` | `daily_us` / `minute_us` |
| HK | 5-digit numeric | `00700` | `daily_hk` / `minute_hk` |
| CN | 6-digit numeric (0/3/6/8/9 prefix) | `600519` | `daily_cn` / `minute_cn` |
```

- [ ] **Step 4: Write docs/reference/commission-models.md**

```markdown
# Commission Models Reference

## Per-Market Commission

| Market | Commission | Stamp Duty | Other Fees |
|--------|-----------|------------|------------|
| US | per-share $0.005 min $1 | — | — |
| HK | 0.03% min HK$3 | 0.13% on SELL | SFC levy + clearing + trading fee |
| CN | 0.025% min ¥5 | 0.05% on SELL | Transfer fee 0.001% |

## CN Market Notes

- Lot size: 100 shares (backtester enforces lot rounding)
- CN stocks (e.g. 600519 茅台 ~¥1700/share) require higher `initial_cash` (500K+)
- Default 100K is insufficient for high-price CN stocks
```

- [ ] **Step 5: Commit**

```bash
git add docs/reference/
git commit -m "docs: extract data architecture reference docs"
```

---

## Task 2: Create docs/reference/ Import and Deprecation References

**Files:**
- Create: `docs/reference/import-paths.md`
- Create: `docs/reference/deprecated-paths.md`

- [ ] **Step 1: Write docs/reference/import-paths.md**

```markdown
# Import Path Reference

## Domain Layer (preferred)

```python
from quant.domain.models.order import Order, OrderSide, OrderType, OrderStatus
from quant.domain.models.position import Position
from quant.domain.models.trade import Trade
from quant.domain.models.fill import Fill
from quant.domain.models.bar import Bar
from quant.domain.models.account import AccountInfo
from quant.domain.models.risk_check import RiskCheckResult
from quant.domain.events.base import Event, EventType
from quant.domain.ports.data_feed import DataFeed
from quant.domain.ports.broker import BrokerAdapter
from quant.domain.ports.strategy import Strategy, StrategyContext
from quant.domain.ports.storage import Storage
from quant.domain.ports.event_publisher import EventPublisher
```

## Infrastructure (implements domain ports)

```python
from quant.infrastructure.events import EventBus
from quant.infrastructure.execution.brokers.paper import PaperBroker
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.infrastructure.data.providers.akshare import AkshareProvider
```

## Features (orchestrators)

```python
from quant.features.backtest.engine import Backtester
from quant.features.trading.engine import Engine, SystemMode, Context
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.strategies import Strategy, StrategyRegistry
from quant.features.cio import CIOEngine
from quant.features.research import ResearchEngine, CandidatePool, ResearchScheduler
```

## Backward Compatibility (re-exports from domain)

```python
from quant.shared.models import Order, Position, Trade, Fill, Bar, AccountInfo
from quant.shared.utils import setup_logger, ConfigLoader
```

## Python Package

- Package name: `quant`
- Import format: `from quant.domain.models.order import Order`
- No relative imports — always use full `quant.*` path
```

- [ ] **Step 2: Write docs/reference/deprecated-paths.md**

```markdown
# Deprecated Import Paths

The following old import paths have been migrated. Do not use them — they will be removed.

| Old Path | New Path |
|----------|----------|
| `quant.core.*` | `quant.features.trading.*` / `quant.features.backtest.*` |
| `quant.data.*` | `quant.infrastructure.data.*` |
| `quant.execution.*` | `quant.infrastructure.execution.*` |
| `quant.models.*` | `quant.domain.models.*` |
| `quant.utils.*` | `quant.shared.utils.*` |
| `quant.strategies.*` | `quant.features.strategies.*` |
| `quant.cio.*` | `quant.features.cio.*` |
| `quant.config.*` | `quant.shared.config.*` |
| `DuckDBProvider` | Use `DuckDBStorage` directly (no longer a provider) |
| `quant.shared.models.trade` (file) | `quant.domain.models.trade` (shared/models/ only re-exports) |
| `quant.shared.models.order` (file) | `quant.domain.models.order` |
| `quant.shared.models.fill` (file) | `quant.domain.models.fill` |
| `quant.shared.models.position` (file) | `quant.domain.models.position` |
| `from quant.features.trading.risk import RiskCheckResult` | `from quant.domain.models.risk_check import RiskCheckResult` |

## Deprecated Event Types

| Old | New |
|-----|-----|
| `EventType.ORDER_SUBMIT` | `EventType.ORDER_SUBMITTED` |
| `EventType.ORDER_FILL` | `EventType.ORDER_FILLED` |
| `EventType.ORDER_CANCEL` | `EventType.ORDER_CANCELLED` |
| `EventType.ORDER_REJECT` | `EventType.ORDER_REJECTED` |
```

- [ ] **Step 3: Commit**

```bash
git add docs/reference/
git commit -m "docs: extract import paths and deprecated paths references"
```

---

## Task 3: Write ARCHITECTURE.md

**Files:**
- Create: `ARCHITECTURE.md`

- [ ] **Step 1: Write ARCHITECTURE.md (~200 lines)**

```markdown
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
| DataFeed | Data source interface (get_bars, subscribe) | TushareProvider, AkshareProvider, YfinanceProvider |
| BrokerAdapter | Broker interface (submit_order, get_positions) | PaperBroker, FutuProvider |
| Strategy | Strategy interface (on_bar, buy, sell) | VolatilityRegime, SimpleMomentum, CrossSectionalMeanReversion |
| Storage | Persistence interface (save_bars, get_bars, get_symbols, get_lot_size) | DuckDBStorage |
| EventPublisher | Event publish interface (subscribe, publish, publish_nowait) | EventBus |

## Architecture Invariants

1. **domain/ has zero external dependencies** — does not depend on any other layer
2. **features/ only depend on domain** — communicate through ports injection, not direct infrastructure access
3. **infrastructure/ implements domain ports** — depends on domain. Cannot import features. Cross-layer communication via event bus
4. **shared/ has no business semantics** — pure utilities (models/ only re-exports, no independent model classes)
5. **api/ only calls features**
6. **Feature-to-feature import is forbidden** — shared types (like RiskCheckResult) must be elevated to `domain/models/`
7. **Inter-layer communication**: direct call + Event Bus (pub/sub) + Dependency Injection (DI)
8. **domain ports return `Any` type**, not `pd.DataFrame` — keeps domain zero-dependency. pandas conversion happens in infrastructure layer

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
| `infrastructure/` | External adapter implementations | events/, data/, execution/ |
| `features/` | Use case orchestration | backtest/, trading/, portfolio/, cio/, strategies/, research/ |
| `shared/` | Cross-cutting utilities | utils/, config/, models/ |
| `api/` | HTTP routing | *\_bp.py, state/runtime.py |
| `frontend/` | React UI | src/*.js |
```

- [ ] **Step 2: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: add ARCHITECTURE.md code map"
```

---

## Task 4: Slim Down Root AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Read current AGENTS.md**

Read the full file (already done, 275 lines)

- [ ] **Step 2: Rewrite root AGENTS.md (~150 lines)**

The new AGENTS.md keeps: architecture overview, directory structure, dependency rules, feature index table, commands, key conventions. Everything else moves to ARCHITECTURE.md and docs/reference/.

New content:

```markdown
# AGENTS.md — Quant Trading System

## Architecture

Hexagonal (Ports & Adapters) + Event-Driven architecture. Domain is the center, Ports define interfaces, Infrastructure implements adapters, Features orchestrate use cases.

**See `ARCHITECTURE.md`** for full code map, module boundaries, domain models/events/ports tables.

**See `docs/AGENTS.md`** for categorized navigation of all reference documentation.

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

## Dependency Rules (铁律)

1. **domain/ zero external dependencies** — does not depend on any other layer
2. **features/ only depend on domain** — through ports injection. If EventBus is needed, accept `EventPublisher` port via constructor
3. **infrastructure/ implements domain ports** — depends on domain. Cannot import features. Cross-layer via event bus
4. **shared/ has no business semantics** — pure utilities (models/ only re-exports)
5. **api/ only calls features**
6. **Feature-to-feature import is forbidden** — shared types must be elevated to `domain/models/`
7. **Inter-layer communication**: direct call + Event Bus + DI
8. **domain ports return `Any` type**, not `pd.DataFrame` — pandas conversion in infrastructure layer

## Feature Index

| Feature | Path | Description |
|---------|------|-------------|
| backtest | features/backtest/ | 回测引擎、步进验证、绩效分析 |
| trading | features/trading/ | 交易引擎、风控、调度器、组合管理 |
| portfolio | features/portfolio/ | 仓位管理 |
| cio | features/cio/ | CIO 市场评估 |
| strategies | features/strategies/ | 策略框架 + 实现 |
| research | features/research/ | 量化策略研究 |

### Module AGENTS.md

Each module has its own `AGENTS.md` for local constraints:

- `quant/domain/AGENTS.md` — domain models, events, ports contracts
- `quant/infrastructure/AGENTS.md` — adapter patterns, DuckDB rules
- `quant/features/backtest/AGENTS.md`
- `quant/features/trading/AGENTS.md`
- `quant/features/strategies/AGENTS.md`
- `quant/features/research/AGENTS.md`
- `quant/features/portfolio/AGENTS.md`
- `quant/features/cio/AGENTS.md`
- `quant/api/AGENTS.md` — Flask route conventions
- `quant/frontend/AGENTS.md` — React conventions
- `quant/shared/AGENTS.md` — re-export compatibility layer

## Reference Documentation

| Doc | Path | Description |
|-----|------|-------------|
| Architecture | `ARCHITECTURE.md` | Code map, module boundaries, invariants |
| Data Architecture | `docs/reference/data-architecture.md` | Two-port separation, providers, storage |
| Symbol Registry | `docs/reference/symbol-registry.md` | Market code patterns |
| Commission Models | `docs/reference/commission-models.md` | Per-market fees |
| Import Paths | `docs/reference/import-paths.md` | All import reference |
| Deprecated Paths | `docs/reference/deprecated-paths.md` | Old paths to avoid |
| Docs Index | `docs/AGENTS.md` | Categorized reference navigation |

## Commands

```bash
python quant/api_server.py                                        # 启动 API 服务
python -m pytest quant/tests/ -q                                  # 运行测试
python quant/backtest_runner.py --strategy SimpleMomentum ...     # CLI 回测
python quant/quant_system.py --mode paper                         # CLI 实盘/模拟
python quant/scripts/ingest_akshare.py --symbol 600519 ...        # 从 akshare 抓取 A-share 数据存入 DuckDB
python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2025-01-01
```

## Key Conventions

- Python 3.10+ with type hints
- No comments unless explicitly requested
- Frozen dataclasses for immutable value objects
- ABC + abstract methods for ports
- Thread safety: `threading.RLock()` for shared state
- Logging: `from quant.shared.utils.logger import setup_logger`
- DuckDB readers must use `read_only=True`
```

- [ ] **Step 3: Verify line count ≤ 160**

Run: `wc -l AGENTS.md`
Expected: ≤ 160

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: slim root AGENTS.md to ~150 lines, extract refs to docs/"
```

---

## Task 5: Write docs/AGENTS.md L1 Navigation Index

**Files:**
- Create: `docs/AGENTS.md`

- [ ] **Step 1: Write docs/AGENTS.md (~80 lines)**

```markdown
# docs/ — Quant System Reference Documentation

## Reference Documents (docs/reference/)

| Document | Description |
|----------|-------------|
| `data-architecture.md` | Two-port data architecture, providers, DuckDB connection rules |
| `symbol-registry.md` | Market code patterns (US/HK/CN), DuckDB table mapping |
| `commission-models.md` | Per-market commission, stamp duty, fees (US/HK/CN) |
| `import-paths.md` | Complete import reference for domain/infrastructure/features/shared |
| `deprecated-paths.md` | Old import paths to avoid, deprecated event types |

## Superpowers (docs/superpowers/)

| Directory | Description |
|-----------|-------------|
| `specs/` | Design specifications (YYYY-MM-DD-*-design.md) |
| `plans/` | Implementation plans (YYYY-MM-DD-*-*.md) |

### Active Specs
<!-- list from docs/superpowers/specs/ -->

### Active Plans
<!-- list from docs/superpowers/plans/ -->

## Historical Docs (quant/docs/md/)

| Document | Status | Description |
|----------|--------|-------------|
| `system_architecture_0419.md` | Keep | Historical architecture doc |
| `quant-system-prd.md` | Keep | Product requirements |
| `technical_manual.md` | Extract | Still-relevant content migrated to reference docs |
| `framework_0416.md` | Remove | Superseded |
| `v1.md` | Remove | Superseded |
| `evaluate_v1.md` | Remove | Superseded |

## Skills (.agents/skills/)

| Skill | Purpose |
|-------|---------|
| `quantitative-research/` | Backtesting, alpha generation, factor models, statistical arbitrage |
| `karpathy-guidelines/` | Reduce common LLM coding mistakes |
| `frontend-design/` | Production-grade frontend interfaces |
| `find-skills/` | Discover and install agent skills |

## Agent Navigation

1. Start at root `AGENTS.md` for task routing and constraints
2. See `ARCHITECTURE.md` for code map and module boundaries
3. See `docs/AGENTS.md` (this file) for reference document navigation
4. See `<module>/AGENTS.md` for module-local constraints
```

- [ ] **Step 2: Commit**

```bash
git add docs/AGENTS.md
git commit -m "docs: add docs/AGENTS.md L1 navigation index"
```

---

## Task 6: Write 5 New Module AGENTS.md Files

**Files:**
- Create: `quant/domain/AGENTS.md`
- Create: `quant/infrastructure/AGENTS.md`
- Create: `quant/api/AGENTS.md`
- Create: `quant/frontend/AGENTS.md`
- Create: `quant/shared/AGENTS.md`

- [ ] **Step 1: Write quant/domain/AGENTS.md**

```markdown
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
```

- [ ] **Step 2: Write quant/infrastructure/AGENTS.md**

```markdown
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
```

- [ ] **Step 3: Write quant/api/AGENTS.md**

```markdown
# API Layer

## 职责

Flask thin routing layer. Exposes features via REST endpoints. Read-only storage access.

## 对外契约

Blueprints:
- `backtest_bp` — backtest execution and results
- `strategies_bp` — strategy registry and parameters
- `positions_bp` — position queries
- `orders_bp` — order management
- `system_bp` — system status and control
- `cio_bp` — CIO assessment endpoints
- `research_bp` — research pipeline control
- `futu_bp` — Futu broker integration

## 依赖

- `features/` — only calls feature orchestrators
- `shared/utils` — logger, config_loader
- `shared/models` — re-exports from domain

## 不变量

- API endpoints use `DuckDBStorage(read_only=True)` — never write to storage
- All state is in `api/state/runtime.py` — not in global variables
- Blueprints do not contain business logic — they only serialize/deserialize

## 修改守则

- Add route: add to appropriate `*_bp.py`
- Change state management: edit `api/state/runtime.py`

## Known Pitfalls

- `runtime.py` uses threading.RLock for thread-safe state access
- Blueprint functions must not raise — return error dicts with appropriate HTTP status codes
- Do not cache DuckDB connections across requests
```

- [ ] **Step 4: Write quant/frontend/AGENTS.md**

```markdown
# Frontend Layer

## 职责

React SPA dashboard. Displays backtest results, live trading status, strategy management.

## 对外契约

Pages (src/):
- `BacktestDashboard.js` — backtest configuration and results
- `LiveTradingPage.js` — real-time trading status
- `StrategyManagement.js` — strategy lifecycle
- `ResearchPanel.js` — research pipeline control
- `CIOAssessmentPanel.js` — CIO weight allocation
- `AccountOverview.js`, `PositionOverview.js` — account/position display
- `StrategyDetailModal.js`, `StrategyPositionCards.js`, `StrategyWeightBar.js` — strategy components

State:
- `api/state/runtime.py` holds `AVAILABLE_STRATEGIES`, `STRATEGY_PARAMETERS` — these drive frontend strategy list

## 依赖

- React 18+
- Flask API (`quant/api_server.py`)
- No business logic — pure presentation

## 不变量

- Frontend strategy list is driven by `AVAILABLE_STRATEGIES` from `api/state/runtime.py`
- Only strategies with implementations in `features/strategies/*/strategy.py` appear in the frontend
- No direct database access — all data via Flask API

## 修改守则

- Change UI: edit files in `quant/frontend/src/`
- Change API integration: edit `App.js` API calls
- Change strategy display: edit `StrategyManagement.js`

## Known Pitfalls

- Build output goes to `quant/frontend/build/` — served by API server
- API endpoint changes must be reflected in frontend fetch calls
- Strategy list is cached in runtime.py at startup — restart API server after adding strategies
```

- [ ] **Step 5: Write quant/shared/AGENTS.md**

```markdown
# Shared Layer

## 职责

Cross-feature pure utilities. No business semantics.

## 对外契约

### utils/
- `setup_logger(name)` — configure logging
- `ConfigLoader` — load YAML configs
- `datetime_utils` — timezone and market calendar helpers

### models/
- Re-exports from `domain/models/` for backward compatibility
- No independent model class definitions

### config/
- `config.yaml` — main system configuration
- `brokers.yaml` — broker credentials
- `strategies.yaml` — strategy parameters

## 依赖

None — shared has no business logic, only utilities.

## 不变量

- `shared/models/` only re-exports from domain — never defines new model classes
- No feature-to-feature coupling via shared/
- All imports from domain or stdlib

## 修改守则

- Change logging: edit `shared/utils/logger.py`
- Change config: edit `shared/config/*.yaml`
- Change datetime utils: edit `shared/utils/datetime_utils.py`

## Known Pitfalls

- Adding business logic to shared/ creates hidden coupling — keep shared/ purely mechanical
- ConfigLoader uses yaml — do not mix JSON configs
```

- [ ] **Step 6: Commit all 5 files**

```bash
git add quant/domain/AGENTS.md quant/infrastructure/AGENTS.md quant/api/AGENTS.md quant/frontend/AGENTS.md quant/shared/AGENTS.md
git commit -m "docs: add 5 new module AGENTS.md files (domain, infrastructure, api, frontend, shared)"
```

---

## Task 7: Migrate Historical Docs and Remove Outdated Files

**Files:**
- Migrate: `quant/docs/md/system_architecture_0419.md` → `docs/reference/`
- Migrate: `quant/docs/md/quant-system-prd.md` → `docs/reference/`
- Remove: `quant/docs/md/framework_0416.md`, `v1.md`, `evaluate_v1.md`, `technical_manual.md`
- Remove: `quant/docs/md/` (directory after migration)
- Remove: `rule.md` (content absorbed into root AGENTS.md)
- Modify: `docs/superpowers/specs/2026-04-24-agent-friendly-md-system-design.md` (update file inventory)

- [ ] **Step 1: Migrate historical docs**

Run:
```bash
cp quant/docs/md/system_architecture_0419.md docs/reference/
cp quant/docs/md/quant-system-prd.md docs/reference/
```

- [ ] **Step 2: Remove outdated docs**

Run:
```bash
rm quant/docs/md/framework_0416.md quant/docs/md/v1.md quant/docs/md/evaluate_v1.md quant/docs/md/technical_manual.md
rm quant/docs/md/system_architecture_0419.md quant/docs/md/quant-system-prd.md
rmdir quant/docs/md
rmdir quant/docs
rm rule.md
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "docs: migrate historical docs to docs/reference/, remove outdated files"
```

---

## Task 8: Final Verification

- [ ] **Step 1: Verify root AGENTS.md ≤ 160 lines**

Run: `wc -l AGENTS.md`
Expected: ≤ 160

- [ ] **Step 2: Verify ARCHITECTURE.md ≤ 220 lines**

Run: `wc -l ARCHITECTURE.md`
Expected: ≤ 220

- [ ] **Step 3: Verify docs/AGENTS.md ≤ 90 lines**

Run: `wc -l docs/AGENTS.md`
Expected: ≤ 90

- [ ] **Step 4: Verify all module AGENTS.md files exist**

Run: `ls quant/domain/AGENTS.md quant/infrastructure/AGENTS.md quant/api/AGENTS.md quant/frontend/AGENTS.md quant/shared/AGENTS.md quant/features/*/AGENTS.md`
Expected: All files exist

- [ ] **Step 5: Verify reference docs exist**

Run: `ls docs/reference/`
Expected: data-architecture.md, symbol-registry.md, commission-models.md, import-paths.md, deprecated-paths.md, system_architecture_0419.md, quant-system-prd.md

- [ ] **Step 6: Verify removed files are gone**

Run: `ls quant/docs/ 2>/dev/null || echo "quant/docs/ removed"`
Expected: "quant/docs/ removed"

Run: `ls rule.md 2>/dev/null || echo "rule.md removed"`
Expected: "rule.md removed"

- [ ] **Step 7: Commit verification**

```bash
git add -A
git commit -m "docs: verify all AGENTS.md line counts and reference docs"
```
