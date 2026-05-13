# Systematic Reversal Hedged Industry Momentum

## 职责

研究候选策略。基于 20 日均值回归信号，在 A 股默认标的池中选择价格低于近期均值的标的进行 long-only 配置。

## 文件结构

| 文件 | 职责 |
|------|------|
| `strategy.py` | 候选策略实现，注册名 `systematic_reversal_hedged_industry_momentum` |
| `config.yaml` | 策略参数与 candidate 状态配置 |
| `README.md` | 来源、信号与研究摘要 |

## 对外契约

- `SystematicReversalHedgedIndustryMomentumStrategy(symbols=None, lookback=20, holding_days=5, max_position_pct=0.10)`

## 依赖

- `quant.features.strategies.daily_bar.DailyBarStrategy`
- `quant.features.strategies.registry.strategy`
- `quant.shared.utils.logger`

## 不变量

- 策略保持 `enabled: false`、`status: candidate`，不得自动启用。
- A 股部署只能 long-only；long-short spread 仅用于研究诊断。
- 信号使用历史 bar 和 T+1 执行，不得在 `__init__` 中访问 Context。

## 修改守则

| 改什么 | 改哪一个文件 |
|--------|--------------|
| 调整交易逻辑 | `strategy.py` |
| 调整默认参数 | `config.yaml` |
| 更新研究说明 | `README.md` |

## Known Pitfalls

- 当前正式研究结论为 rejected：信号验证通过，但 purged walk-forward 失败。
- 该实现是可回测候选，不等同于论文完整的行业动量与 RSR hedge 复刻；缺少行业分类数据时只能作为均值回归代理信号。
