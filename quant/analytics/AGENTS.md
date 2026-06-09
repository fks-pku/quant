# Analytics Package

## 职责

跨回测和实盘复用的数值计算。只接收标准化净值曲线、`domain.models.Trade` 或调用方已准备好的行情面板，不读取数据源、不下单、不持久化。

## 对外契约

- `calculate_performance_metrics(equity_curve, trades, initial_cash=None, benchmark_returns=None)` — 统一绩效指标入口
- `PerformanceMetrics` — 指标结果 dataclass
- `calculate_round_trip_pnls()` / `calculate_round_trip_returns()` — FIFO round-trip 口径工具
- `signal_kernels.compute_signal(formula_key, data, lookback)` — 研究验证和研究生成策略共用的日线信号计算内核；调用方负责准备 point-in-time 数据和执行约束

## 依赖

- 可依赖 `domain.models`
- 可依赖 pandas/numpy/scipy 等纯计算库
- 不得依赖 `features/` 或 `infrastructure/`

## 不变量

- 回测和实盘必须调用同一套 analytics 入口计算 Sharpe、Sortino、MaxDD、win rate、profit factor 等核心指标
- 研究生成策略和 Rank IC 验证必须调用同一套 `signal_kernels` 信号公式；不得在 `integrator.py` 里重新手写一套与验证不同的公式实现
- BUY 佣金作为入场成本，SELL 侧 PnL 扣卖出佣金；round-trip 统计按 FIFO 分摊入场佣金

## 修改守则

- 指标口径变化必须同步更新 `quant/tests/test_analytics.py`
- 信号公式口径变化必须同步更新 `quant/tests/test_research_validation.py` 和 `quant/tests/test_research_codegen.py`
- 实盘指标适配变化必须同步更新 `quant/tests/test_live_trading_records.py`
