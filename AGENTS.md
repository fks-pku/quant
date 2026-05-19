# AGENTS.md — Quant Trading System

Quant trading platform. Hexagonal (Ports & Adapters) + Event-Driven. Python 3.10+.

**详细架构**: `ARCHITECTURE.md` | **参考文档索引**: `docs/AGENTS.md`

## Module Index

> 渐进式披露：此文件仅做索引。进入模块前读对应 AGENTS.md，按需深入。

| Module | Path | Description | AGENTS.md |
|--------|------|-------------|-----------|
| domain | `quant/domain/` | 纯业务逻辑，零外部依赖 (CENTER) | `quant/domain/AGENTS.md` |
| infrastructure | `quant/infrastructure/` | 端口实现 (ADAPTERS): DuckDB, providers, brokers | `quant/infrastructure/AGENTS.md` |
| backtest | `quant/features/backtest/` | 回测引擎、步进验证、绩效分析 | `quant/features/backtest/AGENTS.md` |
| trading | `quant/features/trading/` | 交易引擎、风控、调度器 | `quant/features/trading/AGENTS.md` |
| portfolio | `quant/features/portfolio/` | 仓位管理 | `quant/features/portfolio/AGENTS.md` |
| cio | `quant/features/cio/` | CIO 市场评估、LLM 分析 | `quant/features/cio/AGENTS.md` |
| strategies | `quant/features/strategies/` | 策略框架 + 注册表（7 个 active 策略） | `quant/features/strategies/AGENTS.md` |
| research | `quant/features/research/` | 自动策略研究管线 | `quant/features/research/AGENTS.md` |
| shared | `quant/shared/` | 跨模块工具 (logger, config) | `quant/shared/AGENTS.md` |
| api | `quant/api/` | Flask REST 路由层 | `quant/api/AGENTS.md` |
| frontend | `quant/frontend/` | React Dashboard UI | `quant/frontend/AGENTS.md` |
| tests | `quant/tests/` | 测试套件 (~900 tests) | `quant/tests/AGENTS.md` |
| scripts | `quant/scripts/` | CLI 工具脚本 | — |

## Architecture Rules (铁律)

详见 `ARCHITECTURE.md` "Architecture Invariants" 节。核心：

1. **domain/ 零外部依赖**
2. **features/ 不直接依赖 infrastructure**；可依赖 `domain/` 与 `shared/` 纯工具
3. **infrastructure/ 实现 domain ports** (不导入 features)
4. **跨 feature 禁止直接导入** — 同一 feature 内部可分层导入，共享类型提升到 `domain/models/`
5. **domain ports 返回 `Any`**，不用 `pd.DataFrame`

## Commands

```bash
python quant/api_server.py                                     # API 服务
python -m pytest quant/tests/ -q                               # 测试
python quant/backtest_runner.py --strategy <RegisteredStrategy> ...  # CLI 回测
python quant/quant_system.py --mode paper                      # 实盘/模拟
python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2025-01-01
```

## Key Conventions

- No comments unless explicitly requested
- Frozen dataclasses for immutable value objects
- `threading.RLock()` for shared state
- DuckDB readers: `read_only=True`
- Text files: UTF-8; run `python scripts/check_text_encoding.py` after doc rewrites
