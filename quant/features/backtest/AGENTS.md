# Backtest Feature

## 职责

回测从数据加载到结果输出的完整闭环。包含回测引擎、步进验证、绩效分析。

## 对外契约

- `Backtester(config, event_bus=None, lot_sizes=None)` - 回测引擎
- `BacktestResult` - 回测结果数据类
- `BacktestResultExporter.to_csv(result, output_path)` - 结果导出
- `WalkForwardEngine` - 步进验证引擎
- `DataFrameProvider(data)` - DataFrame 数据包装器

## 依赖

- `infrastructure/events` - EventBus
- `features/trading` - Portfolio, RiskEngine
- `shared/models` - Trade, Position
- `shared/utils` - logger

## 不变量

- 回测使用 T+1 成交（信号日次日开盘价成交）
- 港股有手数限制，美股无
- 成交量限制：单订单不超过当日成交量的 5%

## 修改守则

- 改回测逻辑：只动 `engine.py`
- 改绩效计算：只动 `analytics.py`
- 改步进验证：只动 `walkforward.py`

## Known Pitfalls

- `Backtester._create_context()` 使用了内部类 `BacktestOrderManager`，不要暴露到外部
- `lot_sizes` 字典的 key 格式要与 symbol 一致（包含 HK./US. 前缀）
