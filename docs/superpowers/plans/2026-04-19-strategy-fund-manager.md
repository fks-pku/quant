# Strategy as Fund Manager — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each strategy tracks which positions it owns. Unattributed positions fall into a `default` strategy (手动交易). Per-strategy NAV/P&L history stored in DuckDB. Frontend shows strategy position cards with sparkline charts.

**Architecture:** Order-level attribution — each Order carries `strategy_name`, propagates to Fill, and a new `StrategyPositionTracker` service derives per-strategy positions from fills. Futu positions are calibrated against tracker state to detect external (manual) trades.

**Tech Stack:** Python 3.10+, dataclasses, DuckDB, Flask, React 18, pure SVG sparklines

---

### Task 1: Fix OrderManager to write strategy_name into Order

**Files:**
- Modify: `quant/execution/order_manager.py:117-126`

- [ ] **Step 1: Fix submit_order to pass strategy_name**

In `quant/execution/order_manager.py`, change the Order constructor at line 117-126 to include `strategy_name`:

```python
        order = Order(
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=order_type,
            order_id=order_id,
            status=OrderStatus.PENDING,
            price=price,
            timestamp=datetime.now(),
            strategy_name=strategy_name,
        )
```

- [ ] **Step 2: Run tests to verify nothing breaks**

Run: `python -m pytest quant/tests/test_order_manager.py -q`
Expected: All pass

---

### Task 2: Add strategy_name to Trade model

**Files:**
- Modify: `quant/models/trade.py`

- [ ] **Step 1: Add strategy_name field**

In `quant/models/trade.py`, add after `cost_breakdown`:

```python
    strategy_name: Optional[str] = None
```

The full file becomes:

```python
"""Canonical Trade model (completed round-trip)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class Trade:
    """A completed trade with entry and exit."""

    entry_time: datetime
    exit_time: datetime
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    signal_date: Optional[datetime] = None
    fill_date: Optional[datetime] = None
    fill_price: float = 0.0
    intended_qty: float = 0.0
    cost_breakdown: Optional[Dict] = None
    strategy_name: Optional[str] = None
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest quant/tests/ -q -k "trade or model"`
Expected: All pass

---

### Task 3: Propagate strategy_name from Order to Fill in FillHandler

**Files:**
- Modify: `quant/execution/fill_handler.py:42-67`

- [ ] **Step 1: Add order_id to Fill lookup and inherit strategy_name**

The `process_fill` method currently creates a Fill without strategy_name. We need to accept an optional `strategy_name` parameter and pass it through.

In `quant/execution/fill_handler.py`, change the `process_fill` signature and Fill construction:

```python
    def process_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        commission: float = 0.0,
        timestamp: Optional[datetime] = None,
        strategy_name: Optional[str] = None,
    ) -> Fill:
        if timestamp is None:
            timestamp = datetime.now()

        fill = Fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            timestamp=timestamp,
            strategy_name=strategy_name,
        )
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest quant/tests/test_fill_handler.py -q`
Expected: All pass

---

### Task 4: Create StrategyPosition dataclass and StrategyPositionTracker

**Files:**
- Create: `quant/execution/strategy_position_tracker.py`

- [ ] **Step 1: Write the tracker module**

Create `quant/execution/strategy_position_tracker.py`:

```python
"""Per-strategy position tracking via order-level attribution."""

import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quant.utils.logger import setup_logger

_STRATEGY_POSITIONS_FILE = Path(__file__).parent.parent / "data" / "strategy_positions.json"
DEFAULT_STRATEGY = "default"


@dataclass
class StrategyPosition:
    symbol: str
    strategy_name: str
    qty: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategySnapshot:
    date: str
    strategy_name: str
    nav: float = 0.0
    market_value: float = 0.0
    cash: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class StrategyPositionTracker:
    def __init__(self, persist_path: Optional[Path] = None):
        self._path = persist_path or _STRATEGY_POSITIONS_FILE
        self._positions: Dict[str, Dict[str, StrategyPosition]] = {}
        self._order_strategy_map: Dict[str, str] = {}
        self._lock = threading.RLock()
        self.logger = setup_logger("StrategyPositionTracker")
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for strat, positions in data.get("positions", {}).items():
                    self._positions[strat] = {}
                    for sym, pos_data in positions.items():
                        self._positions[strat][sym] = StrategyPosition(**pos_data)
                self._order_strategy_map = data.get("order_map", {})
                self.logger.info(f"Loaded {sum(len(v) for v in self._positions.values())} strategy positions from {self._path}")
            except Exception as e:
                self.logger.warning(f"Failed to load strategy positions: {e}")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "positions": {
                    strat: {sym: pos.to_dict() for sym, pos in positions.items()}
                    for strat, positions in self._positions.items()
                },
                "order_map": self._order_strategy_map,
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save strategy positions: {e}")

    def record_order(self, order_id: str, strategy_name: Optional[str]) -> None:
        with self._lock:
            self._order_strategy_map[order_id] = strategy_name or DEFAULT_STRATEGY

    def get_strategy_for_order(self, order_id: str) -> str:
        with self._lock:
            return self._order_strategy_map.get(order_id, DEFAULT_STRATEGY)

    def update_from_fill(
        self,
        strategy_name: Optional[str],
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> None:
        strategy = strategy_name or DEFAULT_STRATEGY
        with self._lock:
            if strategy not in self._positions:
                self._positions[strategy] = {}
            positions = self._positions[strategy]

            if symbol not in positions:
                positions[symbol] = StrategyPosition(
                    symbol=symbol, strategy_name=strategy
                )
            pos = positions[symbol]

            if side.upper() == "BUY":
                total_cost = pos.avg_cost * pos.qty + price * qty
                pos.qty += qty
                pos.avg_cost = total_cost / pos.qty if pos.qty > 0 else 0.0
            elif side.upper() == "SELL":
                pos.qty -= qty
                if pos.qty <= 0:
                    del positions[symbol]

            self._save()

    def calibrate(self, broker_positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            tracker_totals: Dict[str, float] = {}
            for strat, positions in self._positions.items():
                for sym, pos in positions.items():
                    tracker_totals[sym] = tracker_totals.get(sym, 0.0) + pos.qty

            for bp in broker_positions:
                symbol = bp.get("symbol", bp.get("code", ""))
                actual_qty = float(bp.get("qty", bp.get("quantity", 0)))
                tracked_qty = tracker_totals.get(symbol, 0.0)
                diff = actual_qty - tracked_qty

                if diff > 0.001:
                    if DEFAULT_STRATEGY not in self._positions:
                        self._positions[DEFAULT_STRATEGY] = {}
                    if symbol not in self._positions[DEFAULT_STRATEGY]:
                        self._positions[DEFAULT_STRATEGY][symbol] = StrategyPosition(
                            symbol=symbol,
                            strategy_name=DEFAULT_STRATEGY,
                        )
                    self._positions[DEFAULT_STRATEGY][symbol].qty += diff
                    cost = float(bp.get("cost_price", bp.get("avg_cost", 0)))
                    if cost > 0:
                        self._positions[DEFAULT_STRATEGY][symbol].avg_cost = cost
                elif diff < -0.001:
                    self._reduce_proportionally(symbol, abs(diff))

            self._save()
            return self.get_breakdown(broker_positions)

    def _reduce_proportionally(self, symbol: str, qty_to_reduce: float) -> None:
        remaining = qty_to_reduce
        for strat, positions in self._positions.items():
            if symbol in positions and remaining > 0:
                pos = positions[symbol]
                reduce_qty = min(pos.qty, remaining)
                pos.qty -= reduce_qty
                remaining -= reduce_qty
                if pos.qty <= 0:
                    del positions[symbol]

    def get_breakdown(self, broker_positions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            price_map: Dict[str, float] = {}
            if broker_positions:
                for bp in broker_positions:
                    sym = bp.get("symbol", bp.get("code", ""))
                    price_map[sym] = float(bp.get("nominal_price", bp.get("market_val", 0)))

            breakdown: Dict[str, Dict[str, Any]] = {}
            for strat, positions in self._positions.items():
                holdings = []
                total_mv = 0.0
                total_pnl = 0.0
                for sym, pos in positions.items():
                    market_value = pos.market_value or (pos.qty * price_map.get(sym, 0))
                    unrealized_pnl = pos.unrealized_pnl or (market_value - pos.avg_cost * pos.qty)
                    holdings.append({
                        "symbol": sym,
                        "strategy": strat,
                        "qty": pos.qty,
                        "avg_cost": round(pos.avg_cost, 4),
                        "market_value": round(market_value, 2),
                        "unrealized_pnl": round(unrealized_pnl, 2),
                    })
                    total_mv += market_value
                    total_pnl += unrealized_pnl
                breakdown[strat] = {
                    "holdings": holdings,
                    "total_market_value": round(total_mv, 2),
                    "total_unrealized_pnl": round(total_pnl, 2),
                }
            return breakdown

    def get_all_strategies(self) -> List[str]:
        with self._lock:
            return list(self._positions.keys())

    def snapshot_all(self, total_nav: float) -> List[StrategySnapshot]:
        with self._lock:
            total_mv = sum(
                sum(pos.market_value or 0 for pos in positions.values())
                for positions in self._positions.values()
            )
            total_mv = max(total_mv, 1.0)
            snapshots = []
            today = date.today().isoformat()
            for strat, positions in self._positions.items():
                strat_mv = sum(pos.market_value or 0 for pos in positions.values())
                strat_pnl = sum(pos.unrealized_pnl or 0 for pos in positions.values())
                cash_share = total_nav * (strat_mv / total_mv) - strat_mv
                snapshots.append(StrategySnapshot(
                    date=today,
                    strategy_name=strat,
                    nav=round(strat_mv + cash_share, 2),
                    market_value=round(strat_mv, 2),
                    cash=round(cash_share, 2),
                    unrealized_pnl=round(strat_pnl, 2),
                    realized_pnl=0.0,
                ))
            return snapshots

    def clear(self) -> None:
        with self._lock:
            self._positions.clear()
            self._order_strategy_map.clear()
            self._save()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "from quant.execution.strategy_position_tracker import StrategyPositionTracker; print('OK')"`
Expected: `OK`

---

### Task 5: Add strategy_snapshots table to DuckDB

**Files:**
- Modify: `quant/data/storage_duckdb.py:39-107`

- [ ] **Step 1: Add strategy_snapshots to _init_database and _ensure_table**

In `quant/data/storage_duckdb.py`:

1. Change line 42 to include `"strategy_snapshots"` in the table list:
```python
        for table in ("orders", "trades", "portfolio_snapshots", "strategy_snapshots", "instrument_meta"):
```

2. Add a new `elif` block in `_ensure_table` after the `instrument_meta` block (after line 107):

```python
        elif table_name == "strategy_snapshots":
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_snapshots (
                    date VARCHAR,
                    strategy_name VARCHAR,
                    nav DOUBLE,
                    market_value DOUBLE,
                    cash DOUBLE,
                    unrealized_pnl DOUBLE,
                    realized_pnl DOUBLE
                )
            """)
```

- [ ] **Step 2: Add save and load methods for strategy snapshots**

Add these methods to `DuckDBStorage` (before the end of the class):

```python
    def save_strategy_snapshot(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute("""
                INSERT INTO strategy_snapshots (date, strategy_name, nav, market_value, cash, unrealized_pnl, realized_pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                snapshot["date"],
                snapshot["strategy_name"],
                snapshot["nav"],
                snapshot["market_value"],
                snapshot["cash"],
                snapshot["unrealized_pnl"],
                snapshot["realized_pnl"],
            ])

    def get_strategy_snapshots(self, strategy_name: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if strategy_name:
                rows = self.conn.execute(
                    "SELECT * FROM strategy_snapshots WHERE strategy_name = ? ORDER BY date",
                    [strategy_name],
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM strategy_snapshots ORDER BY date",
                ).fetchall()
        cols = ["date", "strategy_name", "nav", "market_value", "cash", "unrealized_pnl", "realized_pnl"]
        return [dict(zip(cols, row)) for row in rows]
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest quant/tests/test_data.py -q`
Expected: All pass

---

### Task 6: Wire tracker into API server — strategy endpoints

**Files:**
- Modify: `quant/api_server.py`

- [ ] **Step 1: Add tracker import and singleton**

Near the top of `api_server.py`, after the existing imports, add:

```python
from quant.execution.strategy_position_tracker import StrategyPositionTracker

_strategy_tracker = StrategyPositionTracker()
```

- [ ] **Step 2: Add new strategy API endpoints**

Add these endpoints before the `serve_frontend` catch-all route (before line 1254):

```python
@app.route('/api/strategy-positions', methods=['GET'])
def strategy_positions():
    return jsonify(_strategy_tracker.get_breakdown())


@app.route('/api/strategy/<name>/history', methods=['GET'])
def strategy_history(name):
    try:
        from quant.data.storage_duckdb import DuckDBStorage
        db = DuckDBStorage()
        snapshots = db.get_strategy_snapshots(strategy_name=name)
        return jsonify({"strategy": name, "snapshots": snapshots})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/strategy/all-history', methods=['GET'])
def all_strategy_history():
    try:
        from quant.data.storage_duckdb import DuckDBStorage
        db = DuckDBStorage()
        snapshots = db.get_strategy_snapshots()
        by_strategy: dict = {}
        for s in snapshots:
            by_strategy.setdefault(s["strategy_name"], []).append(s)
        return jsonify(by_strategy)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 3: Modify futu_positions to include strategy_breakdown**

In the `futu_positions` function (line 1189), change the return block (lines 1197-1205) to include strategy_breakdown. Before the `return jsonify({...})`, add tracker calibration:

```python
        holdings = broker.get_positions_enriched()
        detail = broker.get_account_detail()
        strategy_breakdown = _strategy_tracker.calibrate(
            [{"symbol": h.get("symbol", ""), "qty": h.get("quantity", 0), "nominal_price": h.get("current_price", h.get("nominal_price", 0)), "cost_price": h.get("cost_price", h.get("avg_cost", 0))} for h in holdings]
        )
        return jsonify({
            'nav': detail.get('total_assets', 0),
            'total_unrealized_pnl': detail.get('unrealized_pl', 0),
            'total_realized_pnl': detail.get('realized_pl', 0),
            'account': detail,
            'hk': detail.get('hk', {}),
            'us': detail.get('us', {}),
            'holdings': holdings,
            'strategy_breakdown': strategy_breakdown,
        })
```

- [ ] **Step 4: Modify futu_orders to add strategy field**

In the `futu_orders` function, add `'strategy': _strategy_tracker.get_strategy_for_order(o.order_id)` to the order dict at line 1220-1230, and `'strategy': DEFAULT_STRATEGY` to the deals dict at lines 1235-1245.

Import DEFAULT_STRATEGY at top:
```python
from quant.execution.strategy_position_tracker import StrategyPositionTracker, DEFAULT_STRATEGY
```

For the order loop (line 1220):
```python
            orders.append({
                'order_id': o.order_id,
                'symbol': o.symbol,
                'side': o.side,
                'quantity': o.quantity,
                'price': o.price,
                'status': o.status.name if hasattr(o.status, 'name') else str(o.status),
                'filled_qty': o.filled_qty,
                'avg_fill_price': o.avg_fill_price,
                'time': o.update_time.isoformat() if o.update_time else None,
                'strategy': _strategy_tracker.get_strategy_for_order(o.order_id),
            })
```

For the deals loop (line 1235):
```python
                    orders.append({
                        'order_id': str(r.get('deal_id', '')),
                        'symbol': r.get('code', ''),
                        'side': r.get('trd_side', ''),
                        'quantity': float(r.get('qty', 0)),
                        'price': float(r.get('price', 0)),
                        'status': 'DEAL',
                        'filled_qty': float(r.get('qty', 0)),
                        'avg_fill_price': float(r.get('price', 0)),
                        'time': str(r.get('create_time', '')),
                        'strategy': DEFAULT_STRATEGY,
                    })
```

- [ ] **Step 5: Verify API starts**

Run: `python -c "from quant.api_server import app; print('API OK')"`
Expected: `API OK`

---

### Task 7: Create StrategyPositionCards frontend component

**Files:**
- Create: `quant/frontend/src/StrategyPositionCards.js`

- [ ] **Step 1: Write the component**

Create `quant/frontend/src/StrategyPositionCards.js`:

```jsx
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:5000/api';

const fmtCur = (v, market) => {
  const n = parseFloat(v) || 0;
  const prefix = market === 'US' ? '$' : 'HK$';
  return prefix + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const pnlColor = (v) => v >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

function Sparkline({ data, width = 120, height = 32 }) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;
  const vals = data.map(d => d.nav || d.market_value || 0);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(' ');
  const isUp = vals[vals.length - 1] >= vals[0];
  const color = isUp ? 'var(--accent-green)' : 'var(--accent-red)';
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <polyline fill="none" stroke={color} strokeWidth="1.5" points={points} />
    </svg>
  );
}

function StrategyCard({ name, data, history, holdings }) {
  const [expanded, setExpanded] = useState(false);
  const isDefault = name === 'default';
  const displayName = isDefault ? '手动交易' : name;
  const borderColor = isDefault ? 'rgba(255,255,255,0.15)' : 'var(--accent-cyan)';
  const holdingsList = data?.holdings || holdings || [];
  const mv = data?.total_market_value || 0;
  const pnl = data?.total_unrealized_pnl || 0;

  const detectMarket = (sym) => {
    if (sym.startsWith('HK.') || /^\d{5}$/.test(sym)) return 'HK';
    return 'US';
  };

  return (
    <div style={{
      flex: '1 1 280px', minWidth: '260px', maxWidth: '400px',
      background: 'var(--bg-secondary)', borderRadius: '10px',
      borderTop: `3px solid ${borderColor}`, padding: '16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontWeight: 700, fontSize: '14px', color: 'var(--text-primary)' }}>{displayName}</span>
          <span style={{
            fontSize: '10px', padding: '1px 6px', borderRadius: '3px',
            background: 'rgba(0,200,0,0.12)', color: 'var(--accent-green)',
          }}>Active</span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '10px' }}>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>市值</div>
          <div style={{ fontWeight: 600, fontSize: '13px' }}>{fmtCur(mv, detectMarket(holdingsList[0]?.symbol || ''))}</div>
        </div>
        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>盈亏</div>
          <div style={{ fontWeight: 600, fontSize: '13px', color: pnlColor(pnl) }}>{fmtCur(pnl, detectMarket(holdingsList[0]?.symbol || ''))}</div>
        </div>
      </div>

      {history && history.length > 1 && (
        <div style={{ marginBottom: '10px' }}>
          <Sparkline data={history} />
        </div>
      )}

      <div
        onClick={() => setExpanded(!expanded)}
        style={{ cursor: 'pointer', fontSize: '12px', color: 'var(--accent-cyan)', userSelect: 'none' }}
      >
        {expanded ? '▾' : '▸'} 持仓详情 ({holdingsList.length})
      </div>

      {expanded && holdingsList.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px', marginTop: '8px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
              <th style={{ textAlign: 'left', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>股票</th>
              <th style={{ textAlign: 'right', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>数量</th>
              <th style={{ textAlign: 'right', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>市值</th>
              <th style={{ textAlign: 'right', padding: '3px 2px', color: 'var(--text-muted)', fontWeight: 500 }}>盈亏</th>
            </tr>
          </thead>
          <tbody>
            {holdingsList.map((h, i) => (
              <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <td style={{ padding: '3px 2px', fontWeight: 600 }}>{h.symbol}</td>
                <td style={{ textAlign: 'right', padding: '3px 2px' }}>{h.qty || h.quantity}</td>
                <td style={{ textAlign: 'right', padding: '3px 2px' }}>{fmtCur(h.market_value || 0, detectMarket(h.symbol))}</td>
                <td style={{ textAlign: 'right', padding: '3px 2px', color: pnlColor(h.unrealized_pnl || 0), fontWeight: 600 }}>{fmtCur(h.unrealized_pnl || 0, detectMarket(h.symbol))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function StrategyPositionCards({ broker, futuReady }) {
  const [breakdown, setBreakdown] = useState({});
  const [history, setHistory] = useState({});

  const fetchData = useCallback(async () => {
    try {
      if (broker === 'futu' && futuReady) {
        const res = await axios.get(`${API_BASE}/futu/positions`);
        if (res.data && !res.data.error) {
          setBreakdown(res.data.strategy_breakdown || {});
        }
      } else if (broker !== 'futu') {
        const res = await axios.get(`${API_BASE}/strategy-positions`);
        setBreakdown(res.data || {});
      }
    } catch { /* ignore */ }
  }, [broker, futuReady]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/strategy/all-history`);
      setHistory(res.data || {});
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchData();
    const i = setInterval(fetchData, 5000);
    return () => clearInterval(i);
  }, [fetchData]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const strategies = Object.keys(breakdown);
  if (strategies.length === 0) return null;

  const sorted = [...strategies].sort((a, b) => {
    if (a === 'default') return 1;
    if (b === 'default') return -1;
    return (breakdown[b].total_market_value || 0) - (breakdown[a].total_market_value || 0);
  });

  return (
    <div>
      <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--accent-cyan)', marginBottom: '10px', letterSpacing: '0.5px' }}>策略持仓</div>
      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
        {sorted.map(name => (
          <StrategyCard
            key={name}
            name={name}
            data={breakdown[name]}
            history={history[name]}
            holdings={breakdown[name]?.holdings || []}
          />
        ))}
      </div>
    </div>
  );
}
```

---

### Task 8: Integrate StrategyPositionCards into LiveTradingPage

**Files:**
- Modify: `quant/frontend/src/LiveTradingPage.js`

- [ ] **Step 1: Add import and component**

In `LiveTradingPage.js`, add import at top:

```jsx
import StrategyPositionCards from './StrategyPositionCards';
```

Insert a new section between 账户总览 and the CIO section (between the first `</section>` and the `{showData && (` block). Add this block:

```jsx
      {showData && (
        <section>
          <StrategyPositionCards broker={broker} futuReady={systemRunning} />
        </section>
      )}
```

The section order should now be:
1. 账户总览 (AccountOverview)
2. 策略持仓 (StrategyPositionCards) — **new**
3. CIO (StrategyManagement showCIOOnly)
4. 策略管理 (StrategyManagement showStrategiesOnly)
5. 近期交易记录 (trades table)

- [ ] **Step 2: Rebuild frontend**

Run: `cd quant/frontend && npm run build`
Expected: Compiled successfully

---

### Task 9: End-to-end verification

- [ ] **Step 1: Restart API server**

Kill the existing process on port 5000, then start fresh:

```powershell
$proc = (Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue).OwningProcess
if ($proc) { Stop-Process -Id $proc -Force }
Start-Process -FilePath "python" -ArgumentList "quant/api_server.py" -WorkingDirectory "D:\vk\quant" -WindowStyle Hidden
```

- [ ] **Step 2: Verify new endpoints**

```powershell
Invoke-WebRequest -Uri "http://localhost:5000/api/strategy-positions" -UseBasicParsing
# Expected: {"default": {...}, ...} or {}

Invoke-WebRequest -Uri "http://localhost:5000/api/strategy/all-history" -UseBasicParsing
# Expected: {"VolatilityRegime": [...], ...} or {}
```

- [ ] **Step 3: Verify frontend loads**

Navigate to `http://localhost:5000`, click LIVE TRADING tab, verify the 策略持仓 section appears when data is available.

- [ ] **Step 4: Commit all changes**

```bash
git add quant/execution/order_manager.py quant/models/trade.py quant/execution/fill_handler.py quant/execution/strategy_position_tracker.py quant/data/storage_duckdb.py quant/api_server.py quant/frontend/src/StrategyPositionCards.js quant/frontend/src/LiveTradingPage.js quant/frontend/build/
git commit -m "feat: strategy as fund manager — order attribution, position tracker, strategy cards"
```
