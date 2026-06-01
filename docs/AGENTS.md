# docs/ — Quant System Reference Documentation

## Reference Documents (docs/reference/)

| Document | Description |
|----------|-------------|
| `data-architecture.md` | Two-port data architecture, providers, DuckDB connection rules |
| `parquet-lake.md` | Publish live DuckDB sidecars as an OSS-backed partitioned Parquet lake |
| `symbol-registry.md` | Market code patterns (US/HK/CN), DuckDB table mapping |
| `commission-models.md` | Per-market commission, stamp duty, fees (US/HK/CN) |
| `import-paths.md` | Complete import reference for domain/infrastructure/features/shared |
| `deprecated-paths.md` | Old import paths to avoid, deprecated event types |

## Superpowers (docs/superpowers/)

| Directory | Description |
|-----------|-------------|
| `specs/` | Design specifications |
| `plans/` | Implementation plans |

### Active Specs

| File | Description |
|------|-------------|
| `2026-04-24-agent-friendly-md-system-design.md` | Agent-friendly MD system redesign |
| `2026-04-22-tushare-provider-design.md` | Tushare provider implementation |
| `2026-04-22-research-panel-design.md` | Research panel design |
| `2026-04-22-quant-researcher-design.md` | Quant researcher design |
| `2026-04-20-feature-slice-refactoring-design.md` | Feature slice refactoring |
| `2026-05-08-research-statistical-engine-design.md` | Cross-sectional statistical validation engine |
| `2026-05-08-research-multiple-testing-factors-design.md` | Multiple testing and factor attribution |
| `2026-05-08-research-regime-rigor-design.md` | Regime, capacity, SSRN, and IC decay rigor |
| `2026-05-08-research-production-hardening-design.md` | Sensitivity, PIT data, survival bias, and ERC hardening |

### Active Plans

| File | Description |
|------|-------------|
| `2026-04-24-agent-friendly-md-system-implementation.md` | Agent-friendly MD system implementation |
| `2026-04-22-tushare-provider.md` | Tushare provider implementation |
| `2026-04-22-quant-researcher.md` | Quant researcher implementation |
| `2026-04-20-feature-slice-implementation.md` | Feature slice implementation |
| `2026-05-09-research-rigor-hardening.md` | Implementation plan for the 2026-05-08 research rigor specs |

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
