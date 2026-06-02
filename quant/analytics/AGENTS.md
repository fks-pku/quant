# Analytics Package

## 职责

跨回测和实盘复用的绩效指标计算。只接收标准化净值曲线和 `domain.models.Trade`，不读取数据源、不下单、不持久化。

## 对外契约

- `calculate_performance_metrics(equity_curve, trades, initial_cash=None, benchmark_returns=None)` — 统一绩效指标入口
- `PerformanceMetrics` — 指标结果 dataclass
- `calculate_round_trip_pnls()` / `calculate_round_trip_returns()` — FIFO round-trip 口径工具

## 依赖

- 可依赖 `domain.models`
- 可依赖 pandas/numpy/scipy 等纯计算库
- 不得依赖 `features/` 或 `infrastructure/`

## 不变量

- 回测和实盘必须调用同一套 analytics 入口计算 Sharpe、Sortino、MaxDD、win rate、profit factor 等核心指标
- BUY 佣金作为入场成本，SELL 侧 PnL 扣卖出佣金；round-trip 统计按 FIFO 分摊入场佣金

## 修改守则

- 指标口径变化必须同步更新 `quant/tests/test_analytics.py`
- 实盘指标适配变化必须同步更新 `quant/tests/test_live_trading_records.py`
