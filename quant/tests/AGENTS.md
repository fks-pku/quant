# Tests — 回测测试套件

## 结构

```
quant/tests/
├── conftest.py                # 共享 fixtures 和数据工厂函数
├── test_market_rules.py       # 市场规则单元测试 (~30 tests)
├── test_commission.py         # 佣金引擎单元测试 (~31 tests)
├── test_cn_market.py          # A股集成测试 (~10 tests)
├── test_hk_market.py          # 港股集成测试 (~5 tests)
├── test_us_market.py          # 美股集成测试 (~8 tests)
├── test_backtest_core.py      # 回测引擎核心集成测试 (~32 tests)
├── test_backtest_invariants.py # 回测不变量 CASE 套件
├── test_strategies.py         # 策略注册与单元测试 (~25 tests)
├── test_analytics.py          # 分析指标测试 (~41 tests)
├── test_infrastructure.py     # 基础设施测试 (~25 tests)
├── test_sub_portfolio.py      # SubPortfolio 测试
├── test_domain_invariants.py  # Domain 不变量 CASE 套件
├── test_trading_invariants.py # Trading 不变量 CASE 套件
├── test_strategies_invariants.py # Strategy 不变量 CASE 套件
├── test_research_*.py         # Research pipeline/rigor/report contract 测试
├── test_*ingest*.py           # 数据导入/sidecar 测试
├── test_backtest_fuzz.py      # Hypothesis fuzz 测试
├── test_text_encoding.py      # 文本编码检查
└── test_symbol_utils.py       # Symbol 工具测试
```

## 运行

```bash
python3 -m pytest quant/tests/ -q                      # 全部 (~900 tests)
python3 -m pytest quant/tests/test_market_rules.py -q  # 市场规则单元测试
python3 -m pytest quant/tests/test_commission.py -q    # 佣金单元测试
python3 -m pytest quant/tests/test_cn_market.py -q     # A股集成测试
python3 -m pytest quant/tests/test_backtest_core.py -q # 引擎核心
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

### test_market_rules.py — 市场规则单元测试

直接测试 `market_rules.py` 中的函数，不经过 Backtester。

| 测试类 | 覆盖点 |
|--------|--------|
| `TestMarketDetection` | CN/HK/US 市场识别 |
| `TestLotSize` | US=1、CN 默认 100、自定义、零值回退 |
| `TestPriceLimit` | CN ±10%/20%/30%、IPO 豁免、US/HK 无限制、零 prev_close |
| `TestSettlement` | US T+0、CN T+1 当日不可卖/次日可卖 |
| `TestCurrency` | CN=CNY、HK=HKD、US=USD、混合回退 USD |
| `TestSuspended` | volume=0、close=open=0、正常 bar |
| `TestFIFO` | 单 lot 全卖、多 lot 部分卖、精确匹配 |
| `TestEarliestLotTime` | 有 lots、无 lots |

### test_commission.py — 佣金引擎单元测试

直接测试 `commission.py` 中的函数，不经过 Backtester。

| 测试类 | 覆盖点 |
|--------|--------|
| `TestCNCommission` | 最低 ¥5、买入无印花税、卖出印花税、过户费、规管费、4 项 keys |
| `TestHKCommission` | 最低 HK$3、SFC 征费、结算费、交易费、系统费、6 项 keys |
| `TestUSCommission` | $0.005/股、最低 $1、SEC fee、FINRA TAF、percent 模式 |
| `TestCalculateCommissionBySymbol` | 通过 symbol 自动路由到正确市场 |

### test_cn_market.py — A 股集成测试

通过完整 Backtester 管线测试 A 股特有行为。

| 测试类 | 覆盖点 |
|--------|--------|
| `TestCNLotSize` | 低于一手被拒绝 (集成) |
| `TestCNT1Settlement` | 当日买入不可卖出、T+1 诊断验证 |
| `TestCNDividendTax` | ≤30 天 20%、31-365 天 10%、>365 天 0% |
| `TestCNEndToEnd` | DailyReturnAnomaly、RegimeFilteredMomentum、T+1 端到端 |

### test_hk_market.py — 港股集成测试

| 测试类 | 覆盖点 |
|--------|--------|
| `TestHKLotSizeIntegration` | 低于一手被拒绝 (集成) |
| `TestHKT0DayTrading` | T+0 可日内交易 |
| `TestHKEndToEnd` | 测试内 buy-and-hold 港股回测、佣金 cost_breakdown |

### test_us_market.py — 美股集成测试

| 测试类 | 覆盖点 |
|--------|--------|
| `TestUSNoLotSize` | 可买 1 股 |
| `TestUST0DayTrading` | T+0 可日内交易 |
| `TestUSSlippage` | 买入滑点加价、卖出滑点减价 |
| `TestUSEndToEnd` | 测试内 buy-and-hold、cost_breakdown |

### test_backtest_core.py — 引擎核心集成测试

| 测试类 | 覆盖点 |
|--------|--------|
| `TestBacktestDiagnostics` | 成本拖拽计算 |
| `TestCommissionConfig` | 默认 US/HK/CN 配置 |
| `TestBacktesterExecution` | 停牌检测、BUY/SELL/资金不足 |
| `TestDataFrameProvider` | 索引构建、日期查询、交易日期、分红查询 |
| `TestPositionModel` | T+1 settled_quantity、remove_sell_lots、update_from_fill |
| `TestTradeModel` | is_win/is_loss、return_pct、duration_days |
| `TestWalkForwardEngine` | 不足数据返回 not_viable |
| `TestAdjustedPriceSeparation` | adj_close vs real close 分离 |
| `TestStockDividendLotTracking` | 送股 lot 追踪 |
| `TestMarketOrderRiskCheck` | 风控预检 |
| `TestDailyLossRiskCheck` | 日损失风控 |

## 修改守则

- 新增市场规则单元测试 → `test_market_rules.py`
- 新增佣金单元测试 → `test_commission.py`
- 新增市场集成测试 → 对应 `test_<market>_market.py`
- 新增通用引擎功能 → `test_backtest_core.py`
- 新增回测不变量/跨模块执行语义 → `test_backtest_invariants.py`，并同步 `quant/features/backtest/docs/backtest-invariants.md`
- 新增分析指标 → `test_analytics.py`
- 新增回归测试 → 对应功能测试文件（如 `test_backtest_core.py`、`test_analytics.py`）
- 共享数据生成逻辑 → `conftest.py`
