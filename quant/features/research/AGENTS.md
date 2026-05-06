# Research Feature

## 职责

量化策略研究 (Quant Researcher)。主动从 arXiv/SSRN 等来源搜索策略，评估日线适用性，自动生成代码，回测验证，管理候选池。

## 对外契约

- `ResearchEngine(config, scout, evaluator, integrator, pool, backtest_fn, strategies_dir)` - 研究引擎主类，backtest_fn 由调用方注入（API 层）避免 feature 间交叉导入
- `StrategyScout` - 策略搜索器 (arXiv/SSRN 适配器)
- `StrategyEvaluator` - 策略评估器 (LLM 驱动)
- `StrategyIntegrator` - 策略集成器 (代码生成 + 注册)
- `CandidatePool` - 候选池生命周期管理
- `ResearchScheduler` - 定时调度器 (后台守护线程)
- `ResearchConfig` / `ResearchResult` - 配置与结果数据类

## 依赖

- `domain/ports/llm` - LLMAdapterLike（实现由 cio/llm_adapters 提供，通过 DI 注入）
- `shared/utils` - logger, config_loader
- 回测和策略依赖通过 `backtest_fn` 和 `strategies_dir` 由调用方注入（API 层）—— research feature 不直接导入其他 features 或 infrastructure

## 不变量

- 所有新策略以 `status: candidate` 注册，不会自动进入 active
- 评估阈值 (suitability >= 6.0) 和回测 Sharpe 阈值 (>= 0.5) 必须同时通过才能保留候选
- 高频策略只有在 `daily_adaptable: true` 时才会被接受
- 线程安全: ResearchScheduler 使用 daemon thread + RLock

## 修改守则

- 改搜索逻辑：只动 `scout.py`
- 改评估逻辑：只动 `evaluator.py`
- 改代码生成：只动 `integrator.py`
- 改候选池：只动 `pool.py`
- 改调度：只动 `scheduler.py`
- 改流水线：只动 `research_engine.py`

## Known Pitfalls

- `StrategyIntegrator` 会写文件到 `features/strategies/<name>/`，确保目录权限正常
- LLM 返回非 JSON 时 evaluator 会 fallback 到 neutral report，不会 crash
- arXiv API 有 rate limit，scout 已内置 3-5s jitter delay
- 自动回测需要 DuckDB 中有数据，否则策略会被 reject

## Hexagonal Boundary

- `ResearchEngine`, `StrategyIntegrator`, and `CandidatePool` depend on `quant.domain.ports.ResearchStore`.
- File persistence is an infrastructure adapter (`quant.infrastructure.research.FileResearchStore`) injected by the API/composition root.
- Do not write filesystem, DuckDB, HTTP client, or other external adapter logic directly in this feature unless it is hidden behind an injected port.

## Research Pipeline Upgrade Notes

- `ResearchEngine` also accepts injected `ExperimentStore`, `ResearchArtifactStore`, validator, and rigor hub dependencies. Do not instantiate infrastructure adapters inside feature code.
- `discovery/` contains pure source orchestration. External HTTP adapters live under `quant/infrastructure/research/sources/`.
- `validation/` contains strategy spec generation and statistical validation. Market and factor data must arrive through injected domain ports.
- `rigor/` contains purged walk-forward, cost/capacity checks, regime labels, and metric recording. Backtest runners are supplied by API/script composition roots.
- `tracking/` contains pure run hashing and comparison helpers. Run and artifact persistence stays in infrastructure adapters.
- `ensemble/` builds research-time ensemble recommendations from experiment metrics and return/equity artifacts. It must not import or modify `features/portfolio/`.
- Feature-to-feature imports remain forbidden. Shared research result/config types belong in `models.py`; runtime wiring belongs in API/scripts.
