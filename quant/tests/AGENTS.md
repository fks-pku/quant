# Tests — 回测测试套件

## 结构

```
quant/tests/
├── conftest.py                # 共享 fixtures 和数据工厂函数
├── test_cn_market.py          # A股市场规则测试 (34 tests)
├── test_hk_market.py          # 港股市场规则测试 (21 tests)
├── test_us_market.py          # 美股市场规则测试 (19 tests)
├── test_backtest_core.py      # 回测引擎核心测试 (32 tests)
├── test_strategies.py         # 策略注册与单元测试 (25 tests)
├── test_analytics.py          # 分析指标测试 (41 tests)
└── test_infrastructure.py     # 基础设施测试 (25 tests)
```

## 运行

```bash
python -m pytest quant/tests/ -q                    # 全部 (197 tests, ~0.4s)
python -m pytest quant/tests/test_cn_market.py -q   # 只跑 A 股
python -m pytest quant/tests/test_hk_market.py -q   # 只跑港股
python -m pytest quant/tests/test_us_market.py -q   # 只跑美股
```

## 各文件测试内容

### conftest.py — 共享 Fixtures

| 函数/fixture | 用途 |
|-------------|------|
| `base_config` | 标准 backtest 配置 (零滑点、三市场佣金) |
| `make_bars_df()` | 生成合成 OHLCV 数据 (可控价格、收益、噪声) |
| `make_cn_bars()` | 批量生成 A 股多标的 DataFrame |
| `make_hk_bars()` | 批量生成港股多标的 DataFrame |
| `make_us_bars()` | 批量生成美股多标的 DataFrame |
| `make_dividends_df()` | 生成除权除息数据 |
| `make_backtester()` | 快速创建 Backtester 实例 |
| `run_simple_backtest()` | 一行运行完整回测 |

### test_cn_market.py — A 股市场

| 测试类 | 覆盖点 |
|--------|--------|
| `TestCNMarketDetection` | 6 位代码识别 (沪/深/创业板/科创板/北交)、5 位排除、字母排除、CNY 货币、混合市场 USD 回退 |
| `TestCNCommission` | 0.025% 最低 ¥5、买入无印花税、卖出印花税 0.05%、过户费双边、规管费双边、卖方总成本 > 买方 |
| `TestCNLotSize` | 100 股整手、低于一手被拒绝 |
| `TestCNT1Settlement` | 当日买入不可卖出、T+1 诊断计数、次日可卖出验证 |
| `TestCNPriceLimit` | ±10% 主板、±20% 创业板/科创板 (300xxx/688xxx)、±30% 北交所 (8xxxxx)、IPO 5 天无限制 |
| `TestCNDividendTax` | ≤30 天 20%、31-365 天 10%、>365 天 0% |
| `TestCNEndToEnd` | DailyReturnAnomaly 回测、RegimeFilteredMomentum 回测、T+1 强制执行端到端验证 |

### test_hk_market.py — 港股市场

| 测试类 | 覆盖点 |
|--------|--------|
| `TestHKMarketDetection` | 5 位数字 → HK、HK. 前缀 → HK、HKD 货币 |
| `TestHKCommission` | 0.03% 最低 HK$3、买入无印花税、卖出印花税 0.13%、SFC 征费、结算费、交易费、系统费 HK$0.50、6 项费用 breakdown keys |
| `TestHKLotSize` | 默认 100 股、自定义手数、低于一手被拒绝 |
| `TestHKT0DayTrading` | T+0 可日内交易、t1_rejected_sells == 0 |
| `TestHKNoPriceLimit` | 无涨跌停限制 |
| `TestHKEndToEnd` | SimpleMomentum 港股回测、佣金 cost_breakdown 验证 |

### test_us_market.py — 美股市场

| 测试类 | 覆盖点 |
|--------|--------|
| `TestUSMarketDetection` | 字母代码 → US、ETF → US、USD 货币、混合市场 USD 回退 |
| `TestUSCommission` | $0.005/股最低 $1、买入无 SEC/FINRA、卖出 SEC fee + FINRA TAF、3 项费用 breakdown keys |
| `TestUSNoLotSize` | 可买 1 股 |
| `TestUST0DayTrading` | T+0 可日内交易、t1_rejected_sells == 0 |
| `TestUSSlippage` | 买入滑点加价、卖出滑点减价 (相对 raw open 验证) |
| `TestUSEndToEnd` | SimpleMomentum 美股回测、VolatilityRegime 美股回测、cost_breakdown 验证 |

### test_backtest_core.py — 引擎核心

| 测试类 | 覆盖点 |
|--------|--------|
| `TestBacktestDiagnostics` | 平均延迟、成本拖拽计算 |
| `TestCommissionConfig` | 默认 US/HK/CN 配置 |
| `TestBacktesterExecution` | 停牌检测、BUY 创建 Trade (负 PnL)、无仓位 SELL 为空、资金不足拒绝 |
| `TestDataFrameProvider` | 索引构建、日期查询、缺失查询、交易日期集合、数据校验、分红查询 |
| `TestPositionModel` | 初始仓位、T+1 settled_quantity、remove_sell_lots、flat 检测、update_from_fill 买卖 |
| `TestTradeModel` | is_win/is_loss、return_pct、from_entry_exit、duration_days |
| `TestWalkForwardEngine` | 不足数据返回 not_viable、结果结构验证 |

### test_strategies.py — 策略

| 测试类 | 覆盖点 |
|--------|--------|
| `TestStrategyRegistry` | 注册检测、列表、创建实例、不存在策略 |
| `TestStrategyBase` | on_fill BUY 累加、SELL 减少、新标的从 0 开始 |
| `TestDailyReturnAnomalyStrategy` | 默认参数、连续涨跌计数、混合方向、短期信号阈值、中期反转信号 |
| `TestRegimeFilteredMomentumStrategy` | 默认参数、动量计算、regime 检测 |
| `TestVolatilityScaledTrendStrategy` | 默认参数、SMA 计算、价格>SMA 纳入权重、价格<SMA 排除 |
| `TestSimpleMomentumStrategy` | 动量评分、数据累积、最新价格 |
| `TestVolatilityRegimeStrategy` | 初始化、回测可运行 |

### test_analytics.py — 分析指标

| 测试类 | 覆盖点 |
|--------|--------|
| `TestSharpe` | 空/零标准差/正值/负值 |
| `TestSortino` | 空/全正收益 inf/混合 |
| `TestMaxDrawdown` | 空/单调增长/回撤检测/大幅回撤值 |
| `TestCalmar` | 零回撤/正值 |
| `TestWinRate` | 无交易/全赢/全输/混合/BUY 不计入 |
| `TestProfitFactor` | 无交易/全赢 inf/计算 |
| `TestPayoffRatio` | 无交易/只有赢/计算 |
| `TestExpectancy` | 无交易/正值 |
| `TestRollingSharpe` | 数据不足/足够数据 |
| `TestUlcerIndex` | 空/单调增长 |
| `TestGainToPainRatio` | 无交易/只有亏损 |
| `TestTailRatio` | 数据不足/对称分布 |
| `TestRecoveryFactor` | 无交易/零回撤 inf |
| `TestStatisticalSignificance` | 空/显著正值/零标准差 |
| `TestPerformanceMetrics` | 空/正常数据 |

### test_infrastructure.py — 基础设施

| 测试类 | 覆盖点 |
|--------|--------|
| `TestEventBus` | subscribe/publish、unsubscribe、多订阅者、publish_nowait |
| `TestPortfolio` | 初始 NAV、买入/卖出更新、平仓、带仓位 NAV、全部仓位、CN 标的识别、行业暴露、日损失检查、日重置、to_dict |
| `TestPositionModel` | is_long/is_short、成本基础、市价更新 |
| `TestBarModel` | 有效 Bar、无效 Bar 抛异常、典型价格 |
| `TestFillModel` | Fill 创建 |
| `TestOrderModel` | Order 创建 |

## 修改守则

- 新增市场规则测试 → 对应 `test_<market>_market.py`
- 新增策略测试 → `test_strategies.py` 对应类
- 新增通用引擎功能 → `test_backtest_core.py`
- 新增分析指标 → `test_analytics.py`
- 共享数据生成逻辑 → `conftest.py`
