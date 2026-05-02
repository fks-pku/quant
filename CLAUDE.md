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
