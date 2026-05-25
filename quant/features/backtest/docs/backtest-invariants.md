# 回测引擎不变量 & 逐日状态测试用例

所有自动化测试必须基于此文档的 CASE 构造。修改引擎后运行对应 CASE 验证。

---

## 通用约定

| 参数     | 值                                                                                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 信号执行   | **T+1**：当日 Step7 `on_after_trading` 信号，次日 Step4 以 **open** 价执行                                                                                               |
| NAV 记录 | Step 9（Step6 持仓市价更新之后，用当日 **close** 计市值）                                                                                                                     |
| 日循环    | (1) on_before_trading (2) load bars (3) dividends (4) exec deferred (5) on_data (6) update prices (7) on_after_trading (8) pending->deferred (9) NAV + reset |

### 核心不变式

- I1 `NAV == cash + market_value` (每日恒等)
- I2 `cash` 仅在成交日/分红日改变
- I3 `final_nav == initial_cash + sum(trade.pnl) + sum(dividend_net)`
- I4 `diag.total_gross_pnl == sum(trade.pnl) + diag.total_commission`
- I5 `equity_curve[t] == NAV of day t`
- I6 `position.realized_pnl == sum(trade.realized_pnl)` (每个仓位平仓后)
- I7 `NAV[t] == NAV[t-1]` 在停牌日 (无成交、无除权)
- I8 被拒绝订单不影响 `cash` 和 `NAV` (拒绝当天 NAV 不变)
- I9 最后一个真实交易日 after-close 产生的 deferred order 没有下一交易日时必须过期，不能用 synthetic bar 成交
- I10 一个回测实例内不得混合币种；USD/CNY/HKD 混合标的必须拒绝，除非未来引入显式 FX 层
- I11 多策略回测必须按策略隔离现金、持仓、风控与成交回调；master 不承载策略持仓
- I12 LIMIT 订单只在 next open 可成交时成交：BUY 要求 `open <= limit`，SELL 要求 `open >= limit`
- I13 CN 涨跌停按买卖方向约束：涨停拒绝 BUY、跌停拒绝 SELL；反方向不因涨跌停规则拒绝
- I14 停牌 bar 不更新 `last_prices`/`prev_bars`；无成交、无除权时持仓估值沿用最后有效价
- I15 `on_stop` 清仓是显式 forced close-out 语义：默认按最后有效 close 清算并记录 diagnostics；禁用时订单必须过期丢弃
- I16 SubPortfolio 模式下，送股 synthetic fill 必须只分发给对应策略；策略内部仓位必须与对应 sub-portfolio 持仓一致
- I17 交易级 round-trip 指标必须包含按 FIFO 分摊的 BUY 佣金，不能只用 SELL trade 的 `pnl` 判断输赢

### 市场参数

| 市场  | 手数     | T+1 | 佣金                | 印花税                   | 其他费                      |
| --- | ------ | --- | ----------------- | --------------------- | ------------------------ |
| US  | 无      | 无   | per_share/percent | SEC+FINRA (SELL)      | -                        |
| CN  | 100    | 有   | 0.025% min 5      | 0.05% (SELL)          | transfer+regulator       |
| HK  | config | 无   | 0.03% min 3       | 0.1% (BUY+SELL, ceil) | SFC+clear+trade+sys 0.50 |

### 测试辅助函数

    def _signal_strategy(name, symbol, buy_on, sell_on, qty=100):
        class S(Strategy):
            def __init__(self): super().__init__(name); self._day = 0
            @property
            def symbols(self): return [symbol]
            def on_after_trading(self, ctx, td):
                if self._day in buy_on: self.buy(symbol, qty)
                if self._day in sell_on: self.sell(symbol, qty)
                self._day += 1
        return S()

---

## CASE-1: US 零摩擦

### 配置

```python
{
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

### 行情

| Date       | Day | Open   | Close  | Volume    |
| ---------- | --- | ------ | ------ | --------- |
| 2024-06-03 | D0  | 180.00 | 182.50 | 5,000,000 |
| 2024-06-04 | D1  | 182.50 | 184.00 | 6,000,000 |
| 2024-06-05 | D2  | 184.50 | 185.50 | 4,500,000 |
| 2024-06-06 | D3  | 185.00 | 186.00 | 5,500,000 |
| 2024-06-07 | D4  | 186.50 | 187.00 | 7,000,000 |

### 信号

```
D0 after close → BUY  100 AAPL → D1 Step4 执行
D3 after close → SELL 100 AAPL → D4 Step4 执行
```

### 逐日状态

```
Date |    Cash($) | Qty | Close  | MktValue($)|    NAV($) | ΔNAV
D0   |100,000.000 |   0 |     —  |      0.00  |100,000.00 |    —
D1   | 81,750.000 | 100 | 184.00 | 18,400.00  |100,150.00 |+150.00
D2   | 81,750.000 | 100 | 185.50 | 18,550.00  |100,300.00 |+150.00
D3   | 81,750.000 | 100 | 186.00 | 18,600.00  |100,350.00 | +50.00
D4   |100,400.000 |   0 |     —  |      0.00  |100,400.00 | +50.00
```

### 推导

**D1 BUY:**

```
fill_price = 182.50 × (1+0/10000) = 182.50
commission = 0
total_cost = 182.50×100 + 0 = 18,250.00
cash       = 100,000 - 18,250 = 81,750.00
avg_cost   = 18,250/100 = 182.50
lot        = (D1, qty=100, price=182.50)
Step6: market_value = 100 × 184.00 = 18,400.00
Step9: NAV = 81,750 + 18,400 = 100,150.00
```

**D2～D3 持有:**

```
D2: market_value=100×185.50=18,550 → NAV=81,750+18,550=100,300
D3: market_value=100×186.00=18,600 → NAV=81,750+18,600=100,350
cash 不变=81,750
```

**D4 SELL:**

```
fill_price   = 186.50 × (1-0) = 186.50
commission   = 0
proceeds     = 186.50×100 = 18,650.00
cash         = 81,750+18,650 = 100,400.00
realized_pnl = (186.50-182.50)×100 = 400.00
position     = 0, avg_cost=0
NAV          = 100,400.00
```

### 断言

```
C1-01  nav == cash + market_value  (每日)
C1-02  cash_d1 == cash_d2 == cash_d3 == 81,750
C1-03  equity_curve == [100000, 100150, 100300, 100350, 100400]
C1-04  ΔNAV == Δmarket_value on non-trade days
C1-05  final_nav == 100000 + t_buy.pnl + t_sell.pnl  → 100,400
C1-06  total_return == 0.004
C1-07  trades[0]. BUY: pnl=0, commission=0, entry_price=182.50, qty=100
C1-08  trades[1]. SELL: pnl=400, realized_pnl=400, entry_price=182.50, exit_price=186.50
C1-09  diag.fill_count==2, diag.total_commission==0, diag.total_gross_pnl==400
C1-10  pos_d1.qty=100, avg_cost=182.50, len(lots)=1; pos_d4.qty=0, avg_cost=0, lots=[]
```

---

## CASE-2: US 含佣金滑点

### 配置

```python
{
    "backtest": {"slippage_bps": 5},
    "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

### 行情/信号（同 CASE-1）

### 逐日状态

```
Date |   Cash($)    | Qty | Close  |   NAV($)     | ΔNAV
D0   | 100,000.0000 |   0 |     —  |100,000.00000 |    —
D1   |  81,739.87500| 100 | 184.00 |100,139.87500 |+139.875
D2   |  81,739.87500| 100 | 185.50 |100,289.87500 |+150.000
D3   |  81,739.87500| 100 | 186.00 |100,339.87500 | +50.000
D4   | 100,379.01519|   0 |     —  |100,379.01519 | +39.140
```

### 推导

**D1 BUY:**

```
fill_price = 182.50 × (1+5/10000)           = 182.59125
commission = max(100×0.005, 1.00)            = 1.00
total_cost = 182.59125×100 + 1.00            = 18,260.125
cash       = 100,000-18,260.125              = 81,739.875
avg_cost   = 18,260.125/100                  = 182.60125
lot.price  = 182.59125  (不含佣金)
```

**D4 SELL:**

```
fill_price   = 186.50 × (1-5/10000)         = 186.40675
per_share    = max(100×0.005, 1.00)         = 1.00
sec_fee      = 186.40675×100×0.0000278      = 0.51821
finra_taf    = 100×0.000166                  = 0.01660
total_comm   = 1.0+0.51821+0.01660          = 1.53481
proceeds     = 186.40675×100 - 1.53481      = 18,639.14019
cash         = 81,739.875+18,639.14019      = 100,379.01519
realized_pnl_raw = (186.40675-182.59125)×100 = 381.55
sell_pnl     = 381.55-1.53481                = 380.01519
```

### 断言

```
C2-01  equity_curve ≈ [100000, 100139.875, 100289.875, 100339.875, 100379.01519] (±1e-6)
C2-02  cash_d1 == cash_d2 == cash_d3
C2-03  final_nav == initial_cash + trades[0].pnl + trades[1].pnl
C2-04  trades[0] BUY: pnl=-commission, entry_price=182.59125, cost_breakdown={"commission":1.0}
C2-05  trades[1] SELL: entry_price=182.59125, exit_price=186.40675
C2-06  trades[1].pnl == trades[1].realized_pnl - trades[1].commission
C2-07  trades[1].cost_breakdown has keys: {commission, sec_fee, finra_taf}
C2-08  all(v >= 0 for v in trades[1].cost_breakdown.values())
C2-09  diag.total_commission == trades[0].commission + trades[1].commission
C2-10  diag.total_gross_pnl == sum(t.pnl for t in trades) + diag.total_commission
```

---

## CASE-3: CN A 股

验证 T+1 结算 + CN 佣金费率。

### 配置

```python
{
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"CN": {"type": "cn_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

CN 费率: commission 0.025% min ¥5, stamp_duty 0.05% (SELL), transfer 0.001%, regulator 0.002%

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 50.00 | 51.00 | 10M    |
| 2024-06-04 | D1  | 52.00 | 53.00 | 12M    |
| 2024-06-05 | D2  | 53.00 | 52.00 | 8M     |
| 2024-06-06 | D3  | 52.50 | 54.00 | 10M    |
| 2024-06-07 | D4  | 55.00 | 56.00 | 15M    |

### 信号

```
D0 after close → BUY  100 600519 → D1
D3 after close → SELL 100 600519 → D4
```

T+1: D1 买入 → D4 卖出, `lot_date(06-04) < fill_date(06-07)` → settled=100 ✓

### 逐日状态

```
Date |   Cash(¥)    | Qty | Close | MktValue(¥)|   NAV(¥)    | ΔNAV
D0   |100,000.000000|   0 |    —  |      0.00  |100,000.0000 |    —
D1   | 94,794.844000| 100 | 53.00 |  5,300.00  |100,094.8440 |+94.844
D2   | 94,794.844000| 100 | 52.00 |  5,200.00  | 99,994.8440 |-100.00
D3   | 94,794.844000| 100 | 54.00 |  5,400.00  |100,194.8440 |+200.00
D4   |100,286.929000|   0 |    —  |      0.00  |100,286.9290 |+92.085
```

### 推导(关键)

**D1 BUY:** trade_value=5,200, commission=max(1.30,5.00)=5.00, transfer=0.052, regulator=0.104, total_comm=5.156, cash=94,794.844

**D4 SELL:** trade_value=5,500, commission=5.00, stamp=2.75(SELL), total_comm=7.915, cash=100,286.929

### 断言

```
C3-01  equity_curve == [100000, 100094.844, 99994.844, 100194.844, 100286.929]
C3-02  cash_d1==cash_d2==cash_d3
C3-03  final_nav == initial_cash + Σtrade.pnl
C3-04  trades[0] BUY: pnl==-commission, stamp_duty==0 (buy no stamp)
C3-05  trades[1] stamp_duty==2.75, cost_breakdown={commission,stamp_duty,transfer_fee,regulator_fee}
C3-06  trades[1].pnl == trades[1].realized_pnl - trades[1].commission
C3-07  diag.t1_rejected_sells==0 (3天已结算)
C3-08  diag.lot_adjusted_trades==0 (100=1手)
```

---

## CASE-4: HK 港股

验证手数取整 + 印花税双向收取。

### 配置

```python
{
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"HK": {"type": "hk_realistic"}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

HK 费率: commission 0.03% min HK$3, stamp 0.1%(BUY+SELL, ceil to integer), SFC levy 0.00278%, clearing 0.002%, trading 0.005%, system HK$0.50

### 行情

| Date       | Day | Open   | Close  | Volume |
| ---------- | --- | ------ | ------ | ------ |
| 2024-06-03 | D0  | 300.00 | 305.00 | 3M     |
| 2024-06-04 | D1  | 310.00 | 315.00 | 4M     |
| 2024-06-05 | D2  | 312.00 | 308.00 | 2.5M   |
| 2024-06-06 | D3  | 315.00 | 320.00 | 3.5M   |
| 2024-06-07 | D4  | 325.00 | 330.00 | 5M     |

### 信号

```
D0 after close → BUY  100 00700 → D1
D3 after close → SELL 100 00700 → D4
```

HK T+0，无 T+1 限制。100 股 = 1 手。

### 断言

```
C4-01  len(equity_curve) == 5
C4-02  cash_d1 == cash_d2 == cash_d3
C4-03  final_nav == initial_cash + Σtrade.pnl
C4-04  trades[0] BUY: pnl<0, stamp_duty>0 (bidirectional)
C4-05  trades[1] SELL: stamp_duty>0, system_fee==0.50
C4-06  trades[1] cost_breakdown keys: {commission,stamp_duty,sfc_levy,clearing,trading_fee,system_fee}
C4-07  all(v>=0 for v in trades[1].cost_breakdown.values())
C4-08  diag.t1_rejected_sells==0 (HK T+0)
```

---

## CASE-5: SubPortfolio 多策略隔离

两个独立策略各自持有/交易**同一标的 AAPL**，现金、持仓、风控和成交回调必须完全隔离。
master 不共享持仓池；它只负责初始资金分配、保留未分配现金，以及子组合关闭后的资金回收。

### 架构

    master(initial_cash=100,000, unallocated cash=0)
      subA(allocated=40,000): 独立 cash + 独立 AAPL lots
      subB(allocated=60,000): 独立 cash + 独立 AAPL lots
    
    NAV       = master.cash + sum(sub.nav)
    总持仓(sym) = subA.qty[sym] + subB.qty[sym]
    策略持仓    = 按 sub 独立记录，不允许其他策略卖出
    master.cash = initial_cash - sum(allocated_capital) + closed_sub_returned_cash

### 配置(零摩擦US)

    {"backtest":{"slippage_bps":0},
     "execution":{"commission":{"US":{"type":"percent","percent":0.0,"min_per_order":0.0}}},
     "risk":{"max_position_pct":1.0,"max_daily_loss_pct":1.0,"max_leverage":999,"max_orders_minute":999}}

### 数据(同CASE-1)

|D|Date|Open|Close|
|0|06-03|180.00|182.50|
|1|06-04|182.50|184.00|
|2|06-05|184.50|185.50|
|3|06-06|185.00|186.00|
|4|06-07|186.50|187.00|

### 策略

    StratA(allocated=40,000): D0 BUY 50 AAPL, D3 SELL 30 AAPL
    StratB(allocated=60,000): D0 BUY 50 AAPL, D3 SELL 20 AAPL

### 逐日状态

|D|master.cash|subA.cash|subA.q|subB.cash|subB.q|总AAPL|MktVal|NAV|
|D0|0.0000|40,000.00|0|60,000.00|0|0|0.00|100,000.0000|
|D1|0.0000|30,875.00|50|50,875.00|50|100|18,400.00|100,150.0000|
|D2|0.0000|30,875.00|50|50,875.00|50|100|18,550.00|100,300.0000|
|D3|0.0000|30,875.00|50|50,875.00|50|100|18,600.00|100,350.0000|
|D4|0.0000|36,470.00|20|54,605.00|30|50|9,350.00|100,425.0000|

### D1推导

    subA BUY 50: fill=182.50, cost=9,125.00
      subA.cash: 40,000->30,875
    subB BUY 50: fill=182.50, cost=9,125.00
      subB.cash: 60,000->50,875
    总持仓=100, mv=100x184=18,400, NAV=master.cash(0)+subA.nav(40,075)+subB.nav(60,075)=100,150

### D4推导

    subA SELL 30: fill=186.50, proceeds=5,595.00
      subA.cash: 30,875->36,470
      余20, Step6 mv=20x187=3,740
    subB SELL 20: fill=186.50, proceeds=3,730.00
      subB.cash: 50,875->54,605
      余30, Step6 mv=30x187=5,610
    总持仓=50, mv=50x187=9,350
    NAV=master.cash(0)+subA.nav(40,210)+subB.nav(60,215)=100,425

### 断言

    C5-01  equity_curve == [100000, 100150, 100300, 100350, 100425]
    C5-02  NAV_daily == master.cash + sum(sub.nav)
    C5-03  total_qty_per_symbol == sum(sub_qty)
    C5-04  allocated_sum <= initial_cash (40k+60k=100k)
    C5-05  subA/subB 独立持仓；任一策略不能卖出另一策略的仓位
    C5-06  open_positions: 2 entries (stratA 20 + stratB 30 = total 50)
    C5-07  final_nav == initial_cash + sum(trade.pnl)

---

## CASE-6: 多批次 FIFO 卖出

分两次买入，一次全卖出。验证 per-lot Trade 记录。

### 配置(零摩擦 US)

### 行情

| Date | Open   | Close  | Volume |
| ---- | ------ | ------ | ------ |
| D0   | 100.00 | 102.00 | 1M     |
| D1   | 105.00 | 108.00 | 1.2M   |
| D2   | 110.00 | 112.00 | 1.5M   |
| D3   | 115.00 | 118.00 | 1.3M   |
| D4   | 120.00 | 122.00 | 2M     |

### 信号

```
D0: BUY 40 @ D1 open=105
D1: BUY 60 @ D2 open=110
D3: SELL 100 @ D4 open=120  → FIFO: 40×105 + 60×110
```

### 逐日状态

```
Date | Cash($) | Qty | Close  | MktValue($)| NAV($)   | Note
D0   |100,000  |   0 |     —  |       0    |100,000   |
D1   | 95,800  |  40 | 108.00 |   4,320    |100,120   | buy 40×105
D2   | 89,200  | 100 | 112.00 |  11,200    |100,400   | buy 60×110
D3   | 89,200  | 100 | 118.00 |  11,800    |101,000   | hold
D4   |101,200  |   0 |     —  |       0    |101,200   | sell all
```

### 断言

```
C6-01  trades 中 SELL 产生 2 条记录（FIFO 两个 lot）
C6-02  trades[-2]: qty=40, entry_price=105.00, exit_price=120.00, realized_pnl=600.00
C6-03  trades[-1]: qty=60, entry_price=110.00, exit_price=120.00, realized_pnl=600.00
C6-04  trades[-2].entry_time < trades[-1].entry_time  (FIFO 顺序)
C6-05  sum(sell.realized_pnl) == 1,200.00
C6-06  pos._lots 在 SELL 后为空
C6-07  pos.avg_cost == 0 (全平)
C6-08  final_nav == 101,200.00
C6-09  diag.fill_count == 3 (2 buy + 1 sell)
```

## CASE-7A: US 零摩擦分红(无税)

#### 配置(同CASE-1)

#### 数据

|D|Date|Open|Close|Event|
|D0|06-03|180.00|182.00|signal BUY|
|D1|06-04|182.00|184.00|exec BUY|
|D2|06-05|183.00|183.50|ex-div $1.00|
|D3|06-06|183.00|185.00|signal SELL|
|D4|06-07|186.00|187.00|exec SELL|

#### 策略

D0->BUY 100 AAPL->D1 exec, D3->SELL 100 AAPL->D4 exec

#### 逐日状态

|D|Cash($)|Qty|Close|MktVal|NAV|dNAV|
|D0|100,000.000|0|—|0.00|100,000.000|—|
|D1| 81,800.000|100|184.00|18,400.00|100,200.000|+200|
|D2| 81,900.000|100|183.50|18,350.00|100,250.000|+50|
|D3| 81,900.000|100|185.00|18,500.00|100,400.000|+150|
|D4|100,600.000|0|—|0.00|100,600.000|+200|

#### D2除权推导(Step3在Step6之前)

    payment = $1.00 * 100 = $100.00
    portfolio.cash += 100.00  =>  81,800 -> 81,900
    pos.adjust_lots_for_cash_dividend(1.00):
      lot.price = max(0, 182-1) = 181.00
      recalc_avg_cost: 181.00
    Step6: market_value = 100 * 183.50 = 18,350
    Step9: NAV = 81,900 + 18,350 = 100,250

#### D4推导(cost basis已调整)

    fill=186.00, lot_price=181.00
    realized = (186-181)*100 = 500.00 (adjust)
    proceeds = 18,600, cash = 81,900+18,600 = 100,600
    总回报 = trading(500) + dividend(100) = 600

#### 断言

    C7-01  equity_curve == [100000,100200,100250,100400,100600]
    C7-02  trades[1].realized_pnl == 500.0 (adjusted basis)
    C7-03  final_nav == 100000 + div(100) + trading(500)

---

## CASE-7B: CN 含红利税

#### 配置(同CASE-3)

#### 数据

|D|Date|Open|Close|Event|
|D0|06-03|50.00|51.00|signal BUY|
|D1|06-04|52.00|53.00|exec BUY|
|D2|06-05|53.00|52.80|ex-div CNY 0.50|
|D3|06-06|53.00|54.00|signal SELL|
|D4|06-07|55.00|56.00|exec SELL|

#### D2除权推导

    payment = 0.50 * 100 = 50.00
    holding_days = D2-D1 = 1 day <= 30 -> tax_rate = 20%
    tax = 0.50 * 100 * 0.20 = 10.00
    net = 50.00 - 10.00 = 40.00
    cash = 94,794.844 + 40.00 = 94,834.844
    lot.price = max(0, 52 - 0.50) = 51.50

#### 断言

    C7B-01  D2 cash增加净额40.00(非50.00)
    C7B-02  D4 realized_pnl == (55-51.50)*100 = 350.0
    C7B-03  total_reward = trading(350) + net_div(40) = 390

---

## CASE-7C: Strict research provider exposes CN corporate actions

Strict A-share reports that use the streaming DuckDB daily provider must expose `get_dividend_for_date()`. If `corp_actions.cn_dividends` exists, the provider has to pass cash dividends, stock dividends, allotment data, record date, pay date, and announcement date to Backtester before NAV is recorded. Otherwise a strict report silently ignores ex-dividend and bonus-share events.

#### Assertions

    C7C-01  _DuckDBDailyDateProvider.get_dividend_for_date(symbol, ex_date) returns the corporate action row
    C7C-02  cash_dividend and stock_dividend are numeric values, not strings or NaN
    C7C-03  unknown symbol/date returns None
    C7C-04  missing corporate-action sidecar degrades to an empty lookup, not a failing backtest

---

## CASE-7D: Strict research provider normalizes CN ETF fund actions

CN ETF/LOF strict research reports use `cn_etf_ohlcv.duckdb::daily_cn_ochl` for tradable bars. Some ETF corporate actions, especially fund share splits, can appear as an 80%+ raw OHLC jump while `cn_fund_nav.duckdb::cn_fund_nav.adj_nav` remains continuous. If strict backtests use those raw ETF bars without either processing fund actions or normalizing prices, NAV and stop logic will treat a split as a real crash.

The streaming research provider therefore joins ETF bars to `fund_nav.cn_fund_nav` when available and normalizes ETF OHLC/adj_OHLC by `adj_nav / unit_nav`. It preserves `raw_open/high/low/close` and `raw_volume` for diagnostics and turnover unit detection, while execution and NAV use the total-return synthetic ETF bar. This is an ETF/LOF bridge until a dedicated fund corporate-action processor exists.

#### Assertions

    C7D-01  _DuckDBDailyDateProvider joins cn_fund_nav for ETF/LOF symbols when available
    C7D-02  close and adj_close are normalized by adj_nav / unit_nav across fund share splits
    C7D-03  raw_close is preserved for audit and turnover unit inference
    C7D-04  adv20_value still detects Tushare amount units from raw_close/raw_volume, not adjusted close

---

## CASE-8: 混合币种拒绝

验证同一个回测实例不能同时包含 USD 与 CNY/HKD 标的。跨币种组合必须先拆成不同回测，或未来显式引入 FX 汇率、换汇成本和基准币种重估层。

### 数据

|D|Date|AAPL O/C|600519 O/C|Event|
|D0|06-03|180/182|50/51|BUY signals|
|D1|06-04|182/184|52/53|would execute both|

### 断言

    C8-01  symbols=["AAPL", "600519"] -> ValueError("Mixed currencies ...")
    C8-02  不创建 portfolio，不产生 equity_curve/trades
    C8-03  旧的 US+CN 综合盈利预期废弃；需要拆成单币种 CASE 再验证

---

## CASE-9: Position realized_pnl 与 Trade realized_pnl 一致性（多批次不同成本部分卖出）

### 目的

验证 `position.realized_pnl` 与各 FIFO lot Trade 的 `realized_pnl` 总和一致。
**两批次不同买入价格 + 部分卖出**：avg_cost 路径会产生偏差，必须用 per-lot FIFO 才能保持一致。

### 前置条件

- US 市场，有佣金（per_share $0.005, min $1）
- D1 signal → D2 execute: BUY 40@100
- D2 signal → D3 execute: BUY 60@110
- D3 signal → D4 execute: SELL 50@130, position 剩 50 股
- D5: 不操作，让 position 存活

### 行情数据

| 日期  | open | close | volume |
| --- | ---- | ----- | ------ |
| D1  | 100  | 101   | 1M     |
| D2  | 100  | 101   | 1M     |
| D3  | 110  | 111   | 1M     |
| D4  | 130  | 131   | 1M     |
| D5  | 131  | 132   | 1M     |

### 推导

SELL 50 股 (FIFO: 全部来自 D2 lot 的 40 股 + D3 lot 的 10 股):

- Lot D2 (40@100): sub_realized = (130-100)*40 = 1200
- Lot D3 (10@110): sub_realized = (130-110)*10 = 200
- `total_realized = 1200 + 200 = 1400`
- `sum(trade.realized_pnl) = 1400`
- `position.realized_pnl` 应等于 1400
- **avg_cost 错误路径**: avg_cost = (4000+6600)/100 = 106, (130-106)*50 = 1200 ≠ 1400

### 断言

```
C9-01  open_positions 有 1 条（剩余 50 股 AAPL）
C9-02  sum(sell_trade.realized_pnl) == 1400.0
C9-03  position.realized_pnl == sum(sell_trade.realized_pnl)  ← 核心不变式 I6
C9-04  sell_trades 有 2 条（FIFO 跨两个 lot）
```

### 对应测试: `test_c9_*` in `test_backtest_invariants.py`

---

## CASE-10: 复权价格隔离 — 信号用后复权，下单量用真实价

### 目的

验证 **策略技术指标计算用后复权价 (`_adj`)**，**下单量计算用真实收盘价 (`_price`)**。
CN 市场 `adj_close ≈ close × adj_factor`（adj_factor 可达 100+），若误用后复权价算下单量会导致手数
静默丢弃。

**真实 Bug 回归**: 日线策略曾用 `closes[-1]`（= `_adj(bar, "close")` = 后复权 ~1700）算下单量 →
`qty=55 < 100 lot` → 39 次金叉全部静默丢弃，`fill_count=0, discarded_orders=40`。

### 前置条件

- CN 市场，初始资金 ¥100,000
- `adj_factor = 118.0` 模拟 CN 累积复权因子
- `close ≈ 12`，`adj_close ≈ 1416`（close × adj_factor）

### 行情数据

| 日期     | open   | close  | adj_close | adj_factor | volume |
| ------ | ------ | ------ | --------- | ---------- | ------ |
| D0-D20 | ~11.88 | ~11.88 | ~1402     | 118.0      | 10M    |
| D21    | 11.85  | 11.88  | 1401.84   | 118.0      | 10M    |

### 策略

```
D21 on_after_trading:
  # 信号计算 — 用 _adj() (后复权)
  closes = [_adj(b, "close") for b in bars]  # ≈ [1402, 1401, ...]

  # 下单量 — 用 _price() (真实价)
  price = _price(bars[-1])                     # ≈ 11.88
  qty   = int(nav * 0.95 / price)            # = 100000 * 0.95 / 11.88 ≈ 7996

  self.buy("000001", qty)

D22 Step4: execute_order()
  lot_qty = (7996 // 100) * 100 = 7900 ≥ 100 → ✅ 执行
```

### 反例（bug 版本）

```
# 错误：用 _adj() 算下单量
price = _adj(bars[-1], "close")               # = 1401.84 (后复权!)
qty   = int(nav * 0.95 / 1401.84)            # = 100000 * 0.95 / 1401.84 ≈ 67

D22 execute_order():
  lot_qty = (67 // 100) * 100 = 0 < 100 → return None → 静默丢弃 ❌
```

### 断言

```
C10-01  diag.discarded_orders == 0   (不应有任何丢弃)
C10-02  diag.fill_count >= 1         (至少一次成交)
C10-03  len([t for t in trades if t.side == "BUY"]) >= 1
C10-04  _price(bar) == close 而非 adj_close
```

### 对应测试

- `test_s2b_*` in `test_strategies_invariants.py` — `_price()` 单元测试
- `test_backward_adjusted_price_quantity_uses_real_close` in `test_cn_market.py` — CN 后复权集成回归测试

---

---

---

## CASE-11: CN 涨跌停方向性拒绝

验证涨停板买入和跌停板卖出被正确拒绝；涨停卖出、跌停买入不因涨跌停规则拒绝。

### 配置 (同CASE-3 CN零滑点)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 10.00 | 10.00 | 10M    |
| 2024-06-04 | D1  | 11.00 | 10.50 | 10M    |
| 2024-06-05 | D2  | 10.00 | 10.00 | 10M    |

### 信号

D0 after close → BUY 100 600519 → D1 执行

### 推导(D1 涨停拒绝)

```
prev_close_bars["600519"].close = 10.00 (D0 bar)
get_price_limit_direction("600519", 11.00, 10.00, ...)
  upper = _round_half_up(10.00 * 1.10) = 11.00
  open_rounded = _round_half_up(11.00) = 11.00
  open_rounded >= upper → "UP"
  order.side == BUY → PRICE_AT_LIMIT
diag.limit_rejected_orders += 1
NAV 不变 = 100,000
```

### 断言

```
C11-01  limit-up BUY -> PRICE_AT_LIMIT
C11-02  limit-up SELL -> allowed if position/settlement checks pass
C11-03  limit-down BUY -> allowed if cash/risk checks pass
C11-04  limit-down SELL -> PRICE_AT_LIMIT
```

---

## CASE-12: 停牌日处理

验证停牌日延迟订单丢弃 + NAV 不变 + diag 计数；停牌 bar 不能刷新 `last_prices` 或 `prev_bars`。

### 配置 (零摩擦 US)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 100.0 | 102.0 | 1M     |
| 2024-06-04 | D1  | 102.0 | 102.0 | 0      |
| 2024-06-05 | D2  | 105.0 | 106.0 | 1M     |
| 2024-06-06 | D3  | 107.0 | 108.0 | 1M     |

### 信号

- D0: BUY 100 AAPL → D1 执行 (停牌 → 丢弃)
- D2: BUY 100 AAPL → D3 执行 (正常)

### 推导

```
D1 Step2: D1 bar volume=0 → is_suspended → _suspended=True
  diag.suspended_days += 1
  last_prices/prev_bars 沿用 D0 最后有效 bar
D1 Step4: deferred BUY → bar._suspended → diag.discarded_orders += 1
D1 Step9: NAV = D0 NAV = 100,000 (无持仓、无成交)
D3 Step4: BUY 执行 @ open=107.00
```

### 断言

```
C12-01  diag.suspended_days == 1
C12-02  diag.discarded_orders >= 1
C12-03  equity_curve[0] == equity_curve[1] (D1 NAV 不变)
C12-04  diag.fill_count == 1 (仅 D3 成交)
C12-05  equity_curve[-1] > equity_curve[0] (D3 BUY 执行后)
C12-06  若已有持仓，停牌日错误 close/填充值不得改变 NAV
```

---

## CASE-13: 风控拒绝 (仓位上限)

验证 `max_position_pct` 风控拒绝在完整回测管线中生效。

### 配置

```python
{
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 0.05, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 100.0 | 102.0 | 1M     |
| 2024-06-04 | D1  | 105.0 | 106.0 | 1M     |

### 信号

D0: BUY 200 AAPL → submit_order → _passes_risk:
  value = 200×100 = 20,000, limit = 100,000×0.05 = 5,000
  → approved=False → _risk_rejected_count += 1 → OrderRejectedError(RISK_REJECTED)

### 断言

```
C13-01  diag.risk_skipped_orders >= 1
C13-02  diag.fill_count == 0
C13-03  final_nav == initial_cash (NAV 不变)
C13-04  diag.rejection_counts["risk_rejected"] >= 1
```

---

## CASE-14: on_stop 清仓

验证 `on_stop()` 生成的清仓订单在 `backtest.force_close_on_stop=True` 时按最后有效 close 执行，循环后最终仓位清零；设为 `False` 时不生成 synthetic fill，订单计入过期/丢弃。

### 配置 (零摩擦 US)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 100.0 | 102.0 | 1M     |
| 2024-06-04 | D1  | 105.0 | 108.0 | 1M     |
| 2024-06-05 | D2  | 110.0 | 112.0 | 1M     |

### 信号

D0: BUY 100 AAPL → D1 执行
on_stop: SELL 100 AAPL → 循环后以 last_prices 强制清算

### 推导

```
D1: BUY @ 105.00, NAV ≈ 100,300 (持仓 100, close=108)
循环结束:
  last_prices["AAPL"] = 112.0 (D2 close)
  on_stop → SELL 100
  清仓逻辑: fill_price = 112.0 (无滑点), 成交
  cash 增加, position 清零, fill_count = 2
```

### 断言

```
C14-01  diag.fill_count == 2
C14-02  len(open_positions) == 0 (全部平仓)
C14-03  final_nav == initial_cash + sum(trade.pnl) (I3)
C14-04  final_nav > initial_cash
C14-05  diag.forced_closeout_orders == 1
C14-06  diag.forced_closeout_trades == 1
C14-07  force_close_on_stop=False 时 trades 仅含 BUY, expired_orders/discarded_orders 各 +1
```

---

## CASE-15: 送股 (Stock Dividend)

验证送股除权 + synthetic fills + 卖出 PnL 正确。

### 配置 (零摩擦 US)

### 行情

| Date       | Day | Open  | Close | Volume | Event            |
| ---------- | --- | ----- | ----- | ------ | ---------------- |
| 2024-06-03 | D0  | 100.0 | 102.0 | 1M     | signal BUY       |
| 2024-06-04 | D1  | 105.0 | 108.0 | 1M     | exec BUY         |
| 2024-06-05 | D2  | 106.0 | 106.0 | 1M     | ex-div stock 0.5 |
| 2024-06-06 | D3  | 110.0 | 112.0 | 1M     | exec SELL        |

### 信号

D0: BUY 100 → D1 执行, D2: 无信号, D3: SELL 150 → D4 (不在范围内)

D0: BUY 100, D2: SELL 150 → D3 执行

### 推导

```
D1: BUY 100 @ 105.00, lot(D1, qty=100, price=105), avg_cost=105
D2 Step3: stock_dividend=0.5
  pos.adjust_lots_for_stock_dividend(0.5):
    qty = 100*1.5 = 150, lot_price = 105/1.5 = 70.00
    avg_cost = (150*70)/150 = 70.00
  synthetic fill → strategy._positions["AAPL"] += 50
D2 Step6: market_value = 150 * 106.0 = 15,900
D3 Step4: SELL 150 @ 110.00
  FIFO: 150 * (110-70) = 6,000 realized → fill_count=2
```

### 断言

```
C15-01  diag.fill_count == 2 (1 buy + 1 sell)
C15-02  trades SELL: qty=150, entry_price=70.00 (adjusted)
C15-03  trades SELL: realized_pnl == 150 * (110 - 70) == 6000
C15-04  strategy._positions["AAPL"] == 150 after D2 (synthetic fill synced)
C15-05  final_nav > initial_cash
```

---

## CASE-16: CN T+1 同日买卖拒绝

验证同一天信号 BUY+SELL 同一 CN 标的时，SELL 在 submit_order 阶段即被风控的 T+1 结算检查拦截。
（注：execute_order 中另有 t1_rejected_sells 计数器作为安全网，但同日场景下风控先拦截。）

### 配置 (同 CASE-3 CN 零滑点)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 50.00 | 51.00 | 10M    |
| 2024-06-04 | D1  | 52.00 | 53.00 | 12M    |
| 2024-06-05 | D2  | 53.00 | 54.00 | 10M    |

### 信号

D0: BUY 100 600519, SELL 100 600519 (同天两个信号)

### 推导

```
D0 Step7: submit_order("600519", 100, "BUY") → buffer
          submit_order("600519", 100, "SELL")
            → _passes_risk: _check_cn_t1_settlement
              settled_qty(D1): 当前无持仓 → settled=0
              100 <= 0? No → _risk_rejected_count++ → OrderRejectedError(RISK_REJECTED)
            → return None (SELL 未进入 buffer)
D1 Step4: deferred_orders = [BUY(600519,qty=100)]
  BUY 成交, fill_count=1, position 仍持有 100 股
```

### 断言

```
C16-01  diag.risk_skipped_orders >= 1 (SELL 被风控拒绝)
C16-02  diag.fill_count == 1 (仅 BUY)
C16-03  open_positions 有 1 条 (600519, qty=100)
C16-04  D2 NAV > D0 NAV (持仓有市值)
```

---

## CASE-17: 成交量上限 (Volume Participation Limit)

验证订单量超过日成交量 5% 时被截断。

### 配置 (零摩擦 US)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 100.0 | 102.0 | 1000   |
| 2024-06-04 | D1  | 105.0 | 106.0 | 1000   |

### 信号

D0: BUY 500 AAPL → D1 执行

### 推导

```
D1 Step4 execute_order:
  bar_volume = 1000, quantity = 500
  500 > 1000 * 0.05 = 50 → max_qty = 50
  quantity = 50, diag.volume_limited_trades += 1
  实际成交 50 股 (非 500)
```

### 断言

```
C17-01  diag.volume_limited_trades >= 1
C17-02  trades[0].quantity == 50 (截断后数量)
C17-03  trades[0].intended_qty == 500 (原始订单量保留)
C17-04  diag.fill_count == 1
```

---

## CASE-17A: ADV 单笔订单上限 (ADV Order Participation Limit)

验证订单量超过 20 日 ADV 的 5% 时被截断；当行情携带 `adv20_volume` 时，回测执行层必须优先使用 ADV 股数口径，而不是只看当天成交量。

### 配置 (零摩擦 US + execution_cost_model)

`max_participation_rate = 0.05`

### 行情

| Date       | Day | Open  | Close | Volume    | adv20_volume |
| ---------- | --- | ----- | ----- | --------- | ------------ |
| 2024-06-03 | D0  | 100.0 | 102.0 | 1,000,000 | 200          |
| 2024-06-04 | D1  | 105.0 | 106.0 | 1,000,000 | 200          |

### 信号

D0: BUY 500 AAPL → D1 执行

### 推导

```
D1 Step4 execute_order:
  adv20_volume = 200, quantity = 500
  500 > 200 * 0.05 = 10 → max_qty = 10
  quantity = 10, diag.volume_limited_trades += 1
  execution_observations[0].adv_volume_participation <= 0.05
```

### 断言

```
C17A-01  diag.volume_limited_trades >= 1
C17A-02  trades[0].quantity == 10 (ADV 5% 截断)
C17A-03  execution_observations[0].adv_volume == 200
C17A-04  execution_observations[0].adv_volume_participation <= 0.05
```

---

## CASE-18: 价格偏离拒绝 (Price Deviation)

验证执行价与风控检查价偏差 >15% 时拒绝。

### 配置 (零摩擦 US)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 100.0 | 100.0 | 1M     |
| 2024-06-04 | D1  | 120.0 | 120.0 | 1M     |

### 信号

D0: BUY 100 AAPL → D1 执行 (D0 close=100 → risk_price=100)

### 推导

```
D0 Step7 submit_order: _resolve_price(None, AAPL) → last_prices["AAPL"]=100
  risk_check_price = 100
D1 Step4: fill_price = apply_slippage(120, BUY, 0) = 120
  abs(120-100)/100 = 0.20 > 0.15 → PRICE_DEVIATION rejected
  diag.record_rejection(PRICE_DEVIATION)
```

### 断言

```
C18-01  diag.rejection_counts["price_deviation"] >= 1
C18-02  diag.fill_count == 0
C18-03  final_nav == initial_cash
```

---

## CASE-19: 空回测 (No-Trade)

验证策略不产生任何订单时所有指标和状态正确。

### 配置 (零摩擦 US)

### 行情

D0-D4: 同 CASE-1

### 信号

（无）

### 断言

```
C19-01  equity_curve == [100000, 100000, 100000, 100000, 100000]
C19-02  diag.fill_count == 0
C19-03  len(trades) == 0
C19-04  final_nav == initial_cash
C19-05  total_return == 0.0
C19-06  max_drawdown == 0.0
```

---

## CASE-20: CN 碎股卖出 (Odd-lot Pass-through)

验证 CN <100 股卖出可以通过（与 HK 不同）。

### 配置 (同 CASE-3 CN 零滑点)

### 行情

| Date       | Day | Open  | Close | Volume | Event            |
| ---------- | --- | ----- | ----- | ------ | ---------------- |
| 2024-06-03 | D0  | 50.00 | 51.00 | 10M    | signal BUY       |
| 2024-06-04 | D1  | 52.00 | 53.00 | 10M    | exec BUY         |
| 2024-06-05 | D2  | 53.00 | 53.00 | 10M    | ex-div stock 0.5 |
| 2024-06-06 | D3  | 54.00 | 55.00 | 10M    | exec SELL 50     |
| 2024-06-07 | D4  | 56.00 | 57.00 | 10M    | exec SELL 100    |

### 信号

D0: BUY 100 → D1
D2: (除权 → 150 shares)
D3: SELL 50 (碎股) → D4
D4: SELL 100 → D5

### 推导

```
D2: stock_dividend 0.5 → pos.qty=150
D4 Step4: SELL 50, CN apply_lot_rounding(50,100,"SELL","CN"):
  50 >= 100? No → return float(50), False (pass through!)
  settled_qty=150 → sell_qty=min(50,150)=50 → 成交
D5 Step4: SELL 100, apply_lot_rounding(100,100,"SELL","CN"):
  100 >= 100 → lot_qty=100 → 成交
```

### 断言

```
C20-01  diag.fill_count == 3 (1 buy + 2 sells)
C20-02  trades[SELL1].quantity == 50 (碎股通过)
C20-03  trades[SELL2].quantity == 100
C20-04  open_positions 为空
```

---

## CASE-21: HK 碎股卖出拒绝 (LOT_IMPOSSIBLE)

验证 HK 卖出 <1 手被 LOT_IMPOSSIBLE 拒绝（与 CN 不同）。

### 配置 (零滑点 HK)

### 行情

| Date       | Day | Open   | Close  | Volume |
| ---------- | --- | ------ | ------ | ------ |
| 2024-06-03 | D0  | 300.00 | 305.00 | 1M     |
| 2024-06-04 | D1  | 310.00 | 315.00 | 1M     |
| 2024-06-05 | D2  | 312.00 | 312.00 | 1M     |
| 2024-06-06 | D3  | 315.00 | 320.00 | 1M     |

### 信号

D0: BUY 100 00700 → D1, D2: SELL 50 00700 → D3

### 推导

```
D1: BUY 100 → 成交 (1手), position.qty=100
D3 Step4: SELL 50, HK apply_lot_rounding(50,100,"SELL","HK"):
  lot_qty = (50//100)*100 = 0 < 100 → return None → LOT_IMPOSSIBLE
  diag.rejection_counts["lot_impossible"] += 1
```

### 断言

```
C21-01  diag.rejection_counts["lot_impossible"] >= 1
C21-02  diag.fill_count == 1 (仅 BUY)
C21-03  open_positions 有 1 条 (HK.00700, qty=100)
```

---

## CASE-27: BUY 去重拒绝 (DUPLICATE_BUY)

验证同一策略同一天对同一标的两次 BUY，第二次被去重拒绝。

### 配置 (零摩擦 US)

### 行情

D0-D4: 同 CASE-1

### 信号

D0: BUY 100 AAPL, BUY 50 AAPL (两次 BUY 同标的同天)

### 推导

```
D0 Step7: submit_order("AAPL", 100, "BUY", ...)
  → _passes_dedup("AAPL", "BUY"): AAPL not in set → pass
  → buffer: [BUY 100]
  → _buy_dedup_set.add("AAPL")

D0 Step7: submit_order("AAPL", 50, "BUY", ...)
  → _passes_dedup("AAPL", "BUY"): AAPL in set → OrderRejectedError(DUPLICATE_BUY)
  → return None (静默拒绝)
```

### 断言

```
C27-01  diag.fill_count == 1 (仅第一次 BUY)
C27-02  trades[0].quantity == 100 (非 150)
C27-03  trades[0].intended_qty == 100
```

---

## CASE-28: 资金不足拒绝 (INSUFFICIENT_CASH)

验证下单金额超过可用现金时被拒绝。

### 配置 (零摩擦 US, max_position_pct=3.0 让风控放行)

### 行情

| Date       | Day | Open  | Close | Volume |
| ---------- | --- | ----- | ----- | ------ |
| 2024-06-03 | D0  | 100.0 | 102.0 | 10M    |
| 2024-06-04 | D1  | 105.0 | 106.0 | 10M    |

### 信号

D0: BUY 2000 AAPL → D1

### 推导

```
Risk check: value=2000*100=200,000, nav=100,000, max_pos_pct=1.0 → pass (200K<100K*1.0)
但 execute_order._execute_buy:
  total_cost = 105*2000 + 0 = 210,000 > cash(100,000) → INSUFFICIENT_CASH
  diag.record_rejection(INSUFFICIENT_CASH)
```

### 断言

```
C28-01  diag.rejection_counts["insufficient_cash"] >= 1
C28-02  diag.fill_count == 0
C28-03  final_nav == initial_cash
```

---

## CASE-29: LIMIT 订单可成交性

验证 LIMIT 不再被当作 MARKET 单成交。

### 断言

    C29-01  BUY LIMIT 120, next open 110 -> 成交价 110
    C29-02  BUY LIMIT 100, next open 110 -> LIMIT_NOT_MARKETABLE
    C29-03  SELL LIMIT 105, next open 110 -> 成交价 110
    C29-04  SELL LIMIT 120, next open 110 -> LIMIT_NOT_MARKETABLE
    C29-05  市场冲击后的成交价不得穿越 limit

---

## CASE-30: CN 涨跌停方向性

验证涨跌停规则只约束无法成交的方向。

### 断言

    C30-01  涨停 BUY -> PRICE_AT_LIMIT
    C30-02  涨停 SELL -> allowed
    C30-03  跌停 BUY -> allowed
    C30-04  跌停 SELL -> PRICE_AT_LIMIT

---

## CASE-31: 混合币种拒绝

验证 `select_currency()` 和 `Backtester.run()` 对 USD/CNY/HKD 混合标的直接拒绝。

### 断言

    C31-01  ["AAPL", "600519"] -> ValueError("Mixed currencies ...")
    C31-02  ["AAPL", "HK.00700"] -> ValueError("Mixed currencies ...")

---

## CASE-32: 停牌 bar 不刷新有效价格

验证已有持仓在停牌日不会被错误 close 或复权填充值重估。

### 断言

    C32-01  suspended_days == 1
    C32-02  停牌日 NAV == 前一交易日 NAV
    C32-03  后续非停牌交易日的涨跌停 prev_bar 仍来自最后有效 bar

---

## CASE-33: 多策略默认隔离

验证未传 `strategy_allocations` 时，多策略自动等权创建独立 `SubPortfolio`，不共享 master 持仓池。

### 断言

    C33-01  Strategy B 不能卖出 Strategy A 的持仓，记录 no_position 拒绝
    C33-02  open_positions 包含 strategy owner
    C33-03  master 不持有策略仓位，仅保留未分配/回收现金

---

## CASE-34: 多策略送股同步

验证两个策略在 SubPortfolio 模式下各自持有同一 CN 标的时，送股 synthetic fill 只回调对应策略，不能广播给所有持有同一 symbol 的策略。

### 配置

```python
{
    "backtest": {"slippage_bps": 0, "force_close_on_stop": False},
    "execution": {"commission": {"CN": {"type": "percent", "percent": 0.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

### 行情

| Date       | Day | Open   | Close  | Volume | Event            |
| ---------- | --- | ------ | ------ | ------ | ---------------- |
| 2024-06-03 | D0  | 100.00 | 100.00 | 5M     | signal BUY       |
| 2024-06-04 | D1  | 101.00 | 101.00 | 5M     | exec BUY         |
| 2024-06-05 | D2  | 102.00 | 102.00 | 5M     | ex-div stock 0.5 |
| 2024-06-06 | D3  | 103.00 | 103.00 | 5M     | hold             |

### 策略

```text
StockDivA: D0 BUY 100 600519 -> D1
StockDivB: D0 BUY 100 600519 -> D1
D2 stock_dividend=0.5
```

### 推导

```text
D1:
  StockDivA sub-portfolio: qty=100
  StockDivB sub-portfolio: qty=100

D2 Step3:
  对 StockDivA 的 sub-portfolio process_dividends -> additional_shares=50, strategy=StockDivA
  对 StockDivB 的 sub-portfolio process_dividends -> additional_shares=50, strategy=StockDivB
  synthetic fill 只分发给对应 strategy

正确结果:
  StockDivA._positions["600519"] = 150
  StockDivB._positions["600519"] = 150
  open_positions: StockDivA qty=150, StockDivB qty=150

错误广播会导致:
  每个策略收到两次 50 股 synthetic fill -> 100 + 50 + 75 = 225
```

### 断言

```text
C34-01  每个策略的内部 position == 150
C34-02  每个 open_position 的 quantity == 对应策略内部 position
C34-03  synthetic fill 不跨策略广播
```

---

## CASE-35: Round-trip 交易统计包含买入佣金

验证交易级指标用完整 round-trip PnL 计算。BUY 佣金记录在 BUY trade 的 `pnl=-commission`；SELL trade 的 `pnl` 只扣卖出侧佣金，因此 `win_rate/profit_factor/expectancy/payoff` 必须把对应 BUY 佣金按 FIFO 分摊后再判断输赢。

### 配置

```python
{
    "backtest": {"slippage_bps": 0},
    "execution": {"commission": {"US": {"type": "per_share", "per_share": 2.0, "min_per_order": 0.0}}},
    "risk": {"max_position_pct": 1.0, "max_daily_loss_pct": 1.0, "max_leverage": 999, "max_orders_minute": 999},
}
```

### 行情

| Date       | Day | Open   | Close  | Volume |
| ---------- | --- | ------ | ------ | ------ |
| 2024-06-03 | D0  | 100.00 | 100.00 | 5M     |
| 2024-06-04 | D1  | 100.00 | 100.00 | 5M     |
| 2024-06-05 | D2  | 103.00 | 103.00 | 5M     |
| 2024-06-06 | D3  | 103.00 | 103.00 | 5M     |

### 信号

```text
D0 after close -> BUY  100 AAPL -> D1 open=100
D1 after close -> SELL 100 AAPL -> D2 open=103
```

### 推导

```text
D1 BUY:
  buy_commission = 100 * 2.0 = 200
  buy_trade.pnl = -200

D2 SELL:
  gross = (103 - 100) * 100 = 300
  sell_commission = 100 * 2.0 + SEC/FINRA
  sell_trade.pnl > 0

完整 round-trip:
  round_trip_pnl = sell_trade.pnl - 分摊的 buy_commission
  round_trip_pnl < 0
```

### 断言

```text
C35-01  sell_trade.pnl > 0 但 sum(trade.pnl) < 0
C35-02  win_rate == 0.0
C35-03  profit_factor == 0.0
C35-04  expectancy == sum(trade.pnl)
C35-05  winning_trades/losing_trades 使用完整 round-trip PnL 分类
```

---

## CASE-36: CN status 表驱动 ST/停牌语义

验证 `cn_security_status_daily` 接入后，ST 与停牌的含义在回测中保持可交易语义一致。

### 规则

```text
ST 不自动剔除，也不自动禁止交易；它只改变涨跌停约束。
若 bar 带 up_limit/down_limit，则使用 status 表精确涨跌停价。
若没有 up_limit/down_limit 但 is_st=True，则 fallback 到 5% 涨跌停。
tradable=False、has_daily_bar=False、_suspended=True 任一成立时，视为停牌/不可交易。
停牌 synthetic bar 只用于让到期订单丢弃与记录 suspended_days，不刷新 last_prices/prev_bars。
停牌日当日提交该标的订单应在 submission 阶段拒绝，不得顺延到复牌日。
```

### 断言

```text
C36-01  is_st=True 且无显式 up/down limit 时，CN BUY 在 +5% 开盘价被 PRICE_AT_LIMIT 拒绝
C36-02  显式 up_limit/down_limit 优先于 symbol 规则，status limit-up BUY 被 PRICE_AT_LIMIT 拒绝
C36-03  synthetic 停牌日上的到期订单 discarded_orders += 1，fill_count 保持 0，订单不顺延到复牌日
C36-04  synthetic 停牌日当日提交订单 submission_rejected += 1，不产生次日 deferred order
```

---

## CASE-37: 小市值低价策略退市风险护栏

验证研究生成的小市值低价策略在 strict Backtester 中执行同一套退市风险护栏，而不是只在报告或 fast validation 中过滤。

### 规则

```text
买入候选必须满足：
  2 <= close <= 20
  近 liquidity_lookback 日平均 turnover >= min_avg_turnover
  is_st=False
  tradable=True
  is_listed=True
  list_status == 'L'
  近期停牌天数 <= max_recent_suspended_days
  market_cap/total_mv/circ_mv 等 point-in-time 市值字段有效

信号：
  signal = 1 / market_cap
  信号越高，市值越小，优先级越高

正常调仓：
  holding_days=5
  Top 20 或 max_positions 内等权目标

退市风险退出：
  持仓触发价格下限、ST、停牌、不可交易、非上市、流动性不足时，每日先尝试 SELL
  风险退出不得被 holding_days 调仓门控阻挡
  SELL 仍走普通 Backtester T+1、涨跌停、成交量、现金/持仓、佣金滑点路径
```

### 行情

| Date       | Day | Open | Close | Turnover | 状态 |
| ---------- | --- | ---- | ----- | -------- | ---- |
| 2024-06-03 | D0  | 3.00 | 3.00  | 30000    | 正常，可买 |
| 2024-06-04 | D1  | 3.00 | 1.80  | 30000    | 先按 D0 信号买入，收盘跌破价格下限 |
| 2024-06-05 | D2  | 1.80 | 1.80  | 30000    | 执行 D1 风险退出卖单 |

### 断言

```text
C37-01  D1 收盘风险退出提交 SELL，D2 开盘成交；不等待 holding_days=5
C37-02  风险退出后 open_positions 为空，final_suspended_holding_nav/count 为 0
C37-03  close<2、turnover 不足、is_st=True、is_listed=False、list_status='D' 均不产生 BUY
```

---

## Regression B1: 结束日 deferred order 过期

验证最后一个真实交易日 after-close 产生的订单没有下一交易日时不会用 synthetic bar 成交。

### 断言

    B1-01  fill_count == 0
    B1-02  expired_orders == 1
    B1-03  open_positions 为空
    B1-04  trades 为空

---

## Regression W1: Walk-forward aggregate max drawdown

Walk-forward `aggregate_max_dd` must preserve the backtest analytics sign convention:
drawdown percentages are negative, and the most severe window is the minimum value.

### Assertions

    W1-01  window max_drawdown_pct == [-0.02, -0.25, -0.08]
    W1-02  aggregate_max_dd == -0.25

---

## Regression R2: Data and execution guardrails

Backtest inputs and fill-time prices must fail closed before mutating portfolio state or analytics state.

### Assertions

    R2-01  DataValidator.validate() returns ValidationReport, not raw pandas exceptions, for unparseable timestamp values.
    R2-02  DataValidator.validate() rejects NaN/null and non-numeric OHLCV values.
    R2-03  execute_order() rejects non-finite or non-positive fill prices with PRICE_INVALID before cash/position mutation.
    R2-04  on_stop forced close-out can sell same-day CN lots by explicit close-out semantics, not by normal T+1 deferred semantics.
    R2-05  equity_curve index must contain real trading dates only; post-loop close-out updates final real-date NAV instead of appending end+1.
    R2-06  CN SELL volume caps must keep sellable odd-lot quantities; board-lot rounding applies to CN BUY and HK lot-constrained trades.
    R2-07  benchmark statistical significance is a one-sided outperformance test; negative excess-return t-stat is not significant outperformance.
    R2-08  CN IPO price-limit exemption accepts both date and datetime IPO metadata.

---

## CASE索引

|#|市场|核心验证|
|1|US|NAV=C+M, cash不变, equity匹配|
|2|US|佣金拆解, SEC+FINRA, realized一致性|
|3|CN|T+1结算, stamp仅SELL, CN费率全套|
|4|HK|stamp仅SELL, system_fee=0.50, T+0|
|5|US|双策略同标的, SubPortfolio 隔离, master 仅分配/回收资金|
|6|US|FIFO per-lot Trade精确性|
|7A|US|现金分红, cost basis调整, 经济验证|
|7B|CN|红利税阶梯, cost basis调整|
|8|US+CN|混合币种拒绝|
|9|US|position.realized_pnl == sum(trade.realized_pnl) 部分卖出一致性|
|10|CN|策略 _adj(复权) vs _price(真实价) 隔离, 防止下单量静默丢弃|
|11|CN|涨跌停方向性拒绝 (limit_rejected_orders)|
|12|US|停牌日 (suspended_days + discarded + NAV 不变 + 不刷新 last_prices)|
|13|US|风控拒绝管线 (risk_skipped_orders)|
|14|US|on_stop 清仓 (最终仓位清零)|
|15|US|送股 + synthetic fills (stock dividend)|
|16|CN|T+1 同日买卖拒绝 (T1_SETTLEMENT)|
|17|US|成交量上限 (volume_limited_trades)|
|18|US|价格偏离拒绝 (PRICE_DEVIATION)|
|19|US|空回测 (no-trade edge case)|
|20|CN|碎股卖出通过 (odd-lot pass-through)|
|21|HK|碎股卖出拒绝 (LOT_IMPOSSIBLE)|
|27|US|BUY 去重拒绝 (DUPLICATE_BUY)|
|28|US|资金不足拒绝 (INSUFFICIENT_CASH)|
|29|US|LIMIT 订单可成交性 (LIMIT_NOT_MARKETABLE)|
|30|CN|涨跌停买卖方向约束|
|31|Mixed|拒绝混合币种|
|32|US|停牌 bar 不刷新有效价格|
|33|US|多策略无 allocation 时默认隔离|
|34|CN|多策略同标的送股 synthetic fill 只同步对应策略|
|35|US|round-trip 交易统计包含买入佣金|
|36|CN|status 表驱动 ST 5% fallback、显式涨跌停、停牌 synthetic bar|
|37|CN|小市值低价策略退市风险护栏：价格/流动性/status 买入过滤 + 每日风险退出|
|B1|US|结束日 deferred order 过期|
|W1|N/A|Walk-forward aggregate_max_dd uses worst negative drawdown|
|R2|N/A|Data/execution guardrails for malformed input, close-out, dates, and benchmark significance|
