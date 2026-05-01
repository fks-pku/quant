# Domain 不变量

Domain 层是纯业务逻辑，零外部依赖。包含 Position、Trade、Fill 等值对象。

---

## 通用约定

- 所有 model 用 frozen dataclass（不可变）或显式 mutable dataclass
- Ports 返回 `Any`，不返回 `pd.DataFrame`
- domain/ 下无任何外部 import

### 核心不变式

- D1 `Position.update_from_fill` 买入时 `avg_cost = (old_cost_basis + fill_cost) / new_qty`
- D2 `Position.update_from_fill` 卖出时使用 **per-lot FIFO** 计算 realized_pnl（非 avg_cost）
- D3 `Position.remove_sell_lots` FIFO 顺序，卖完清空 lots
- D4 `Position.settled_quantity` 只计算 `lot_date < as_of` 的 lot
- D5 `Trade.from_entry_exit` 的 `pnl = (exit - entry) * qty - commission`
- D6 `Position.adjust_lots_for_stock_dividend` qty * factor, price / factor
- D7 `Position.adjust_lots_for_cash_dividend` qty 不变, price -= cash_per_share
- D8 `Position.win_count/loss_count/win_rate` 逐笔 FIFO 胜率跟踪

---

## CASE-1: Position BUY 更新 avg_cost

### 前置条件

`Position(symbol="AAPL")` 空

### 操作

1. `update_from_fill(+100, 150.0, D1)` → qty=100, avg=150
2. `update_from_fill(+100, 170.0, D2)` → qty=200, avg=(15000+17000)/200=160

### 断言

```
D1-01  after fill1: qty==100, avg_cost==150.0
D1-02  after fill2: qty==200, avg_cost==160.0
D1-03  lots: {D1: (100,150), D2: (100,170)}
```

### 对应测试: `test_d1_*` in `test_domain_invariants.py`

---

## CASE-2: Position SELL FIFO + realized_pnl

### 前置条件

`Position(symbol="AAPL")`, 先 BUY 100@100 (D1), 再 BUY 100@120 (D2)

### 操作

1. `update_from_fill(-150, 130.0)` — SELL 150 股

### 预期

`update_from_fill` 使用 **per-lot FIFO** 计算 realized_pnl:
- Lot D1 (100@100): (130-100)*100 = 3000
- Lot D2 (50@120): (130-120)*50 = 500
- `realized_pnl = 3000 + 500 = 3500`

### 断言

```
D2-01  qty==50
D2-02  realized_pnl==3500 (per-lot FIFO)
D2-03  remaining qty==50
D2-04  win_count==2 (both lots profitable)
D2-05  win_rate==1.0
```

### 对应测试: `test_d2_*` in `test_domain_invariants.py`

---

## CASE-3: Position settled_quantity (T+1)

### 前置条件

`Position`, BUY 100@50 on D1

### 断言

```
D3-01  settled_quantity(D1) == 0 (当天不可卖)
D3-02  settled_quantity(D2) == 100 (次日已结算)
D3-03  settled_quantity(D3) == 100 (之后仍可)
```

### 对应测试: `test_d3_*` in `test_domain_invariants.py`

---

## CASE-4: Position 全平后状态清零

### 前置条件

`Position`, qty=100, avg_cost=150, lots={D1: (100,150)}

### 操作

`update_from_fill(-100, 160.0)`

### 断言

```
D4-01  quantity==0, avg_cost==0
D4-02  lots 为空
D4-03  is_flat==True
D4-04  realized_pnl == (160-150)*100 == 1000
```

### 对应测试: `test_d4_*` in `test_domain_invariants.py`

---

## CASE-5: Position 送股调整

### 前置条件

`Position`, qty=100, lots={D1: (100, 100.0)}

### 操作

`adjust_lots_for_stock_dividend(1.0)` — 每 10 送 10

### 断言

```
D5-01  quantity==200
D5-02  lots: {D1: (200, 50.0)} (qty*2, price/2)
D5-03  avg_cost==50.0
```

### 对应测试: `test_d5_*` in `test_domain_invariants.py`

---

## CASE-6: Position 现金分红调整

### 前置条件

`Position`, qty=100, lots={D1: (100, 100.0)}

### 操作

`adjust_lots_for_cash_dividend(2.0)` — 每股派 $2

### 断言

```
D6-01  quantity==100 (不变)
D6-02  lots: {D1: (100, 98.0)} (price-2)
D6-03  avg_cost==98.0
```

### 对应测试: `test_d6_*` in `test_domain_invariants.py`

---

## CASE-7: Trade.from_entry_exit 计算

### 操作

`Trade.from_entry_exit("AAPL", 100, 150.0, 160.0, D1, D2, "SELL", commission=10.0)`

### 断言

```
D7-01  pnl == (160-150)*100 - 10 == 990
D7-02  realized_pnl == pnl == 990
D7-03  is_win == True (realized_pnl > 0)
D7-04  duration_days == 1.0
```

### 对应测试: `test_d7_*` in `test_domain_invariants.py`
