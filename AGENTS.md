# AGENTS.md — Quant Trading System

Quant trading platform. Hexagonal (Ports & Adapters) + Event-Driven. Python 3.10+.

**详细架构**: `ARCHITECTURE.md` | **参考文档索引**: `docs/AGENTS.md`

## Module Index

> 渐进式披露：此文件仅做索引。进入模块前读对应 AGENTS.md，按需深入。

| Module | Path | Description | AGENTS.md |
|--------|------|-------------|-----------|
| domain | `quant/domain/` | 纯业务逻辑，零外部依赖 (CENTER) | `quant/domain/AGENTS.md` |
| infrastructure | `quant/infrastructure/` | 端口实现 (ADAPTERS): DuckDB, providers, brokers | `quant/infrastructure/AGENTS.md` |
| analytics | `quant/analytics/` | 跨回测/实盘复用的绩效指标计算 | `quant/analytics/AGENTS.md` |
| runtime | `quant/runtime/` | 跨回测/实盘复用的策略生命周期分发 helper | `quant/runtime/AGENTS.md` |
| backtest | `quant/features/backtest/` | 回测引擎、步进验证、绩效分析 | `quant/features/backtest/AGENTS.md` |
| trading | `quant/features/trading/` | 交易引擎、风控、调度器 | `quant/features/trading/AGENTS.md` |
| portfolio | `quant/features/portfolio/` | 仓位管理 | `quant/features/portfolio/AGENTS.md` |
| cio | `quant/features/cio/` | CIO 市场评估、LLM 分析 | `quant/features/cio/AGENTS.md` |
| strategies | `quant/features/strategies/` | 策略框架 + 注册表 + promoted/reject/archive 策略分区 | `quant/features/strategies/AGENTS.md` |
| research | `quant/features/research/` | 自动策略研究管线 | `quant/features/research/AGENTS.md` |
| shared | `quant/shared/` | 跨模块工具 (logger, config) | `quant/shared/AGENTS.md` |
| api | `quant/api/` | Flask REST 路由层 | `quant/api/AGENTS.md` |
| tests | `quant/tests/` | 测试套件 (~1100 collected tests) | `quant/tests/AGENTS.md` |
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
python quant/scripts/strategy_dashboard_server.py              # 策略管理看板
python quant/scripts/run_low_frequency_idea_scout.py --source config --max 5  # 低频策略 idea scout
python quant/scripts/run_research.py --mode scout_formal --source config --max 10 --max-ideas 5  # scout + formal pre-full gate
python -m pytest quant/tests/ -q                               # 测试
python quant/backtest_runner.py --strategy <RegisteredStrategy> ...  # CLI 回测
python quant/quant_system.py --mode paper                      # 实盘/模拟
python quant/scripts/check_qmt_connection.py                   # MiniQMT 连接自检（不下单）
python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2025-01-01
```

## Key Conventions

- No comments unless explicitly requested
- Frozen dataclasses for immutable value objects
- `threading.RLock()` for shared state
- DuckDB readers: `read_only=True`
- 策略研究模板契约：用户说“研究策略/策略研究/full report”时，必须使用 `quant-research-fks`，并严格按当前 7 张可展开卡片 HTML 报告契约生成：`1. Final Decision`、`2. 策略逻辑`、`3. 策略表现`、`4. 重要 Metric`、`5. Walk-forward`、`6. Stability`、`7. Risk`；`Final Decision` 段就是 active Go / No-Go checklist 加 executive snapshot，不另设顶层 `Metric Checklist`。活跃模板是 `quant/infrastructure/var/research/report_templates/full_research_report_template.html`。默认 full report 入口必须跑并持久化 Walk-forward 与 Stability 审计 payload；单阶段/快速调试才允许显式跳过。默认报告不展示止盈止损开关对照、Appendix 或 TODO，除非用户明确要求增量审计。
- Text files: UTF-8; run `python scripts/check_text_encoding.py` after doc rewrites
- Machine-specific scheduled task entrypoints live under ignored `local_scheduler/`; do not commit local scheduler scripts, role files, or task snapshots.
