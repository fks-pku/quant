# Rejected Strategy Archive

## 职责

保存完整研究后最终 Go / No-Go 判定为 rejected 的生成策略代码。

## 约束

- 本目录不是策略池；`StrategyRegistry` 不会自动发现这里的策略。
- 显式导入本目录策略也不得把归档类注册进 active `StrategyRegistry`。
- 归档策略只用于审计、复现和后续人工复盘，不能进入 paper trading 或实盘。
- strict backtest / walk-forward audit 可以显式从 `research_meta.rejected_strategy_dir` 加载归档策略做复盘；这不等于重新启用策略。
- 若重启研究，应从 idea bank 或报告记录重新进入 formal research，不直接启用本目录代码。
