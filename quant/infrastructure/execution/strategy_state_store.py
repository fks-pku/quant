"""Strategy dashboard state store — 5 DuckDB tables: states, positions, signals, capital_events, snapshots."""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import duckdb


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "var" / "strategy_dashboard.duckdb"
VALID_MODES = {"live", "paper"}
VALID_LIFECYCLE_STATES = {"running", "paused", "stopped", "liquidating"}
VALID_CAPITAL_EVENT_TYPES = {"DEPOSIT", "WITHDRAW", "DIVIDEND_CASH", "DIVIDEND_STOCK", "ADJUSTMENT"}


class StrategyStateStore:
    def __init__(self, db_path: Optional[Any] = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._lock = threading.RLock()

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.execute("""
                create table if not exists strategy_states (
                    event_id integer primary key,
                    strategy_name text not null,
                    mode text not null,
                    from_state text not null default '',
                    to_state text not null,
                    signal_enabled boolean not null default false,
                    submit_enabled boolean not null default false,
                    liquidation_requested boolean not null default false,
                    initial_cash double not null default 0.0,
                    error_message text not null default '',
                    note text not null default '',
                    recorded_at text not null
                )
            """)
            con.execute("""
                create table if not exists strategy_positions (
                    strategy_name text not null,
                    mode text not null,
                    symbol text not null,
                    quantity double not null default 0.0,
                    avg_cost double not null default 0.0,
                    realized_pnl double not null default 0.0,
                    updated_at text not null default '',
                    primary key (strategy_name, mode, symbol)
                )
            """)
            con.execute("""
                create table if not exists strategy_signals (
                    signal_id text primary key,
                    strategy_name text not null,
                    mode text not null,
                    timestamp text not null,
                    signal_date text not null,
                    symbol text not null,
                    side text not null,
                    quantity double not null default 0.0,
                    order_type text not null default '',
                    reference_price double,
                    status text not null default 'generated',
                    order_id text not null default '',
                    broker_order_id text not null default '',
                    fill_quantity double not null default 0.0,
                    fill_price double,
                    commission double not null default 0.0,
                    fill_time text not null default '',
                    failure_reason text not null default '',
                    submit_date text not null default '',
                    cost_bps double,
                    record_date text not null
                )
            """)
            con.execute("""
                alter table strategy_signals add column if not exists submit_date text default ''
            """)
            con.execute("""
                alter table strategy_signals add column if not exists cost_bps double
            """)
            con.execute("""
                create index if not exists idx_states_strategy_mode
                on strategy_states (strategy_name, mode)
            """)
            con.execute("""
                create index if not exists idx_signals_strategy_mode
                on strategy_signals (strategy_name, mode, record_date)
            """)
            con.execute("""
                create index if not exists idx_signals_record_date
                on strategy_signals (record_date)
            """)
            con.execute("""
                create table if not exists strategy_capital_events (
                    event_id integer primary key,
                    strategy_name text not null,
                    mode text not null,
                    event_type text not null,
                    symbol text not null default '',
                    amount double not null default 0.0,
                    quantity double not null default 0.0,
                    price double,
                    effective_date text not null,
                    note text not null default '',
                    recorded_at text not null
                )
            """)
            con.execute("""
                create index if not exists idx_capital_events_strategy_mode
                on strategy_capital_events (strategy_name, mode)
            """)
            con.execute("""
                create table if not exists strategy_snapshots (
                    snapshot_id text primary key,
                    strategy_name text not null,
                    mode text not null,
                    snapshot_date text not null,
                    nav double not null default 0.0,
                    cash double not null default 0.0,
                    market_value double not null default 0.0,
                    realized_pnl double not null default 0.0,
                    unrealized_pnl double not null default 0.0,
                    total_pnl double not null default 0.0,
                    source text not null default '',
                    recorded_at text not null
                )
            """)
            con.execute("""
                create index if not exists idx_snapshots_strategy_mode_date
                on strategy_snapshots (strategy_name, mode, snapshot_date)
            """)
        finally:
            con.close()

    def record_state(
        self,
        *,
        strategy_name: str,
        mode: str,
        from_state: str,
        to_state: str,
        signal_enabled: bool,
        submit_enabled: bool,
        liquidation_requested: bool = False,
        initial_cash: float = 0.0,
        error_message: str = "",
        note: str = "",
        recorded_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = _mode(mode)
        ts = recorded_at or datetime.now().isoformat()
        if to_state not in VALID_LIFECYCLE_STATES:
            raise ValueError(f"Invalid to_state: {to_state}")
        self.ensure_schema()
        with self._lock:
            con = self._connect()
            try:
                max_id = con.execute(
                    "select coalesce(max(event_id), 0) from strategy_states"
                ).fetchone()
                event_id = int((max_id or [0])[0]) + 1
                con.execute(
                    """insert into strategy_states
                    (event_id, strategy_name, mode, from_state, to_state, signal_enabled,
                     submit_enabled, liquidation_requested, initial_cash,
                     error_message, note, recorded_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        event_id, strategy_name, mode, from_state, to_state,
                        bool(signal_enabled), bool(submit_enabled),
                        bool(liquidation_requested), float(initial_cash),
                        str(error_message or ""), str(note or ""), ts,
                    ],
                )
            finally:
                con.close()
        return {
            "event_id": event_id,
            "strategy_name": strategy_name,
            "mode": mode,
            "from_state": from_state,
            "to_state": to_state,
            "signal_enabled": bool(signal_enabled),
            "submit_enabled": bool(submit_enabled),
            "liquidation_requested": bool(liquidation_requested),
            "initial_cash": float(initial_cash),
            "error_message": str(error_message or ""),
            "note": str(note or ""),
            "recorded_at": ts,
        }

    def get_current_state(self, *, strategy_name: str, mode: str) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                """select event_id, strategy_name, mode, from_state, to_state,
                   signal_enabled, submit_enabled, liquidation_requested,
                   initial_cash, error_message, note, recorded_at
                from strategy_states
                where strategy_name = ? and mode = ?
                order by event_id desc
                limit 1""",
                [strategy_name, mode],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, [
            "event_id", "strategy_name", "mode", "from_state", "to_state",
            "signal_enabled", "submit_enabled", "liquidation_requested",
            "initial_cash", "error_message", "note", "recorded_at",
        ])

    def get_state_history(
        self, *, strategy_name: str, mode: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select event_id, strategy_name, mode, from_state, to_state,
                   signal_enabled, submit_enabled, liquidation_requested,
                   initial_cash, error_message, note, recorded_at
                from strategy_states
                where strategy_name = ? and mode = ?
                order by event_id desc
                limit ?""",
                [strategy_name, mode, limit],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "event_id", "strategy_name", "mode", "from_state", "to_state",
            "signal_enabled", "submit_enabled", "liquidation_requested",
            "initial_cash", "error_message", "note", "recorded_at",
        ]
        return [_row_to_dict(row, columns) for row in reversed(rows)]

    def get_all_strategy_names(self) -> List[str]:
        if not self.db_path.exists():
            return []
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                "select distinct strategy_name from strategy_states order by strategy_name"
            ).fetchall()
        finally:
            con.close()
        return [str(row[0]) for row in rows if row and row[0]]

    def get_all_known_strategy_names(self) -> List[str]:
        if not self.db_path.exists():
            return []
        con = self._connect(read_only=True)
        try:
            rows = con.execute("""
                select distinct strategy_name from (
                    select strategy_name from strategy_states
                    union
                    select strategy_name from strategy_signals
                    union
                    select strategy_name from strategy_positions
                ) order by strategy_name
            """).fetchall()
        finally:
            con.close()
        return [str(row[0]) for row in rows if row and row[0]]

    def get_latest_recorded_at(self, *, strategy_name: str, mode: str) -> Optional[str]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                """select recorded_at from strategy_states
                where strategy_name = ? and mode = ?
                order by event_id desc limit 1""",
                [strategy_name, mode],
            ).fetchone()
        finally:
            con.close()
        return str(row[0]) if row and row[0] else None

    def get_all_positions_grouped(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if not self.db_path.exists():
            return {}
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select strategy_name, mode, symbol, quantity, avg_cost, realized_pnl, updated_at
                from strategy_positions order by strategy_name, mode, symbol"""
            ).fetchall()
        finally:
            con.close()
        result: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for row in rows:
            strategy_name = str(row[0])
            mode = str(row[1])
            symbol = str(row[2])
            if strategy_name not in result:
                result[strategy_name] = {}
            if mode not in result[strategy_name]:
                result[strategy_name][mode] = {}
            result[strategy_name][mode][symbol] = {
                "qty": float(row[3] or 0),
                "avg_cost": float(row[4] or 0),
                "realized_pnl": float(row[5] or 0),
            }
        return result

    def get_all_position_symbols(self) -> set[str]:
        if not self.db_path.exists():
            return set()
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                "select distinct symbol from strategy_positions"
            ).fetchall()
        finally:
            con.close()
        return {str(row[0]) for row in rows if row and row[0]}

    def upsert_position(
        self,
        *,
        strategy_name: str,
        mode: str,
        symbol: str,
        quantity: float,
        avg_cost: float = 0.0,
        realized_pnl: float = 0.0,
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = _mode(mode)
        ts = updated_at or datetime.now().isoformat()
        self.ensure_schema()
        row = {
            "strategy_name": strategy_name,
            "mode": mode,
            "symbol": symbol,
            "quantity": float(quantity),
            "avg_cost": float(avg_cost),
            "realized_pnl": float(realized_pnl),
            "updated_at": ts,
        }
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """insert or replace into strategy_positions
                    (strategy_name, mode, symbol, quantity, avg_cost, realized_pnl, updated_at)
                    values (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["strategy_name"], row["mode"], row["symbol"],
                        row["quantity"], row["avg_cost"], row["realized_pnl"], row["updated_at"],
                    ],
                )
            finally:
                con.close()
        return row

    def get_position(
        self, *, strategy_name: str, mode: str, symbol: str
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                """select strategy_name, mode, symbol, quantity, avg_cost, realized_pnl, updated_at
                from strategy_positions
                where strategy_name = ? and mode = ? and symbol = ?""",
                [strategy_name, mode, symbol],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, [
            "strategy_name", "mode", "symbol", "quantity",
            "avg_cost", "realized_pnl", "updated_at",
        ])

    def get_positions(self, *, strategy_name: str, mode: str) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select strategy_name, mode, symbol, quantity, avg_cost, realized_pnl, updated_at
                from strategy_positions
                where strategy_name = ? and mode = ?
                order by symbol""",
                [strategy_name, mode],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "strategy_name", "mode", "symbol", "quantity",
            "avg_cost", "realized_pnl", "updated_at",
        ]
        return [_row_to_dict(row, columns) for row in rows]

    def get_all_positions_for_mode(self, *, mode: str) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select strategy_name, mode, symbol, quantity, avg_cost, realized_pnl, updated_at
                from strategy_positions
                where mode = ?
                order by strategy_name, symbol""",
                [mode],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "strategy_name", "mode", "symbol", "quantity",
            "avg_cost", "realized_pnl", "updated_at",
        ]
        return [_row_to_dict(row, columns) for row in rows]

    def delete_position(
        self, *, strategy_name: str, mode: str, symbol: str
    ) -> None:
        if not self.db_path.exists():
            return
        mode = _mode(mode)
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "delete from strategy_positions where strategy_name = ? and mode = ? and symbol = ?",
                    [strategy_name, mode, symbol],
                )
            finally:
                con.close()

    def clear_positions_for_mode(self, *, mode: str) -> int:
        if not self.db_path.exists():
            return 0
        mode = _mode(mode)
        with self._lock:
            con = self._connect()
            try:
                count = con.execute(
                    "select count(*) from strategy_positions where mode = ?",
                    [mode],
                ).fetchone()
                con.execute("delete from strategy_positions where mode = ?", [mode])
            finally:
                con.close()
        return int((count or [0])[0])

    def upsert_signal(self, *, signal: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_schema()
        signal_id = str(signal.get("signal_id") or "")
        if not signal_id:
            raise ValueError("signal_id is required")
        row = {
            "signal_id": signal_id,
            "strategy_name": str(signal.get("strategy_name") or ""),
            "mode": _mode(signal.get("mode") or "live"),
            "timestamp": str(signal.get("timestamp") or ""),
            "signal_date": str(signal.get("signal_date") or signal.get("record_date") or "")[:10],
            "symbol": str(signal.get("symbol") or ""),
            "side": str(signal.get("side") or ""),
            "quantity": float(signal.get("quantity", 0.0)),
            "order_type": str(signal.get("order_type") or ""),
            "reference_price": _nullable_float(signal.get("reference_price")),
            "status": str(signal.get("status") or "generated"),
            "order_id": str(signal.get("order_id") or ""),
            "broker_order_id": str(signal.get("broker_order_id") or ""),
            "fill_quantity": float(signal.get("fill_quantity", 0.0)),
            "fill_price": _nullable_float(signal.get("fill_price")),
            "commission": float(signal.get("commission", 0.0)),
            "fill_time": str(signal.get("fill_time") or ""),
            "failure_reason": str(signal.get("failure_reason") or ""),
            "submit_date": str(signal.get("submit_date") or "")[:10],
            "cost_bps": _nullable_float(signal.get("cost_bps") or signal.get("execution_cost_bps")),
            "record_date": str(signal.get("record_date") or signal.get("signal_date") or "")[:10],
        }
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """insert or replace into strategy_signals
                    (signal_id, strategy_name, mode, timestamp, signal_date, symbol, side,
                     quantity, order_type, reference_price, status, order_id, broker_order_id,
                     fill_quantity, fill_price, commission, fill_time, failure_reason,
                     submit_date, cost_bps, record_date)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["signal_id"], row["strategy_name"], row["mode"],
                        row["timestamp"], row["signal_date"], row["symbol"], row["side"],
                        row["quantity"], row["order_type"], row["reference_price"],
                        row["status"], row["order_id"], row["broker_order_id"],
                        row["fill_quantity"], row["fill_price"], row["commission"],
                        row["fill_time"], row["failure_reason"],
                        row["submit_date"], row["cost_bps"], row["record_date"],
                    ],
                )
            finally:
                con.close()
        return row

    def get_signal(self, *, signal_id: str) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                """select * from strategy_signals where signal_id = ?""",
                [signal_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, _signal_columns())

    def get_signals(
        self,
        *,
        strategy_name: str,
        mode: str,
        limit: int = 200,
        after_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            if after_date:
                rows = con.execute(
                    """select * from strategy_signals
                    where strategy_name = ? and mode = ? and record_date >= ?
                    order by timestamp desc limit ?""",
                    [strategy_name, mode, str(after_date)[:10], limit],
                ).fetchall()
            else:
                rows = con.execute(
                    """select * from strategy_signals
                    where strategy_name = ? and mode = ?
                    order by timestamp desc limit ?""",
                    [strategy_name, mode, limit],
                ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _signal_columns()) for row in reversed(rows)]

    def get_recent_signals(self, *, mode: str, days: int = 30) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select * from strategy_signals
                where mode = ? and record_date >= ?
                order by timestamp desc""",
                [mode, cutoff],
            ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _signal_columns()) for row in rows]

    def delete_signals_for_orders(
        self,
        *,
        mode: str,
        order_ids: Iterable[str],
        signal_date: Optional[str] = None,
    ) -> int:
        ids = sorted({str(order_id) for order_id in order_ids if str(order_id)})
        if not ids or not self.db_path.exists():
            return 0
        mode = _mode(mode)
        placeholders = ", ".join("?" for _ in ids)
        where_date = " and signal_date = ?" if signal_date else ""
        params = [mode, *ids, *ids]
        if signal_date:
            params.append(str(signal_date)[:10])
        with self._lock:
            con = self._connect()
            try:
                count = con.execute(
                    f"""select count(*) from strategy_signals
                    where mode = ? and (order_id in ({placeholders}) or broker_order_id in ({placeholders})){where_date}""",
                    params,
                ).fetchone()
                con.execute(
                    f"""delete from strategy_signals
                    where mode = ? and (order_id in ({placeholders}) or broker_order_id in ({placeholders})){where_date}""",
                    params,
                )
            finally:
                con.close()
        return int((count or [0])[0])

    def get_all_controls(self) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select distinct strategy_name, mode from strategy_states order by strategy_name, mode"""
            ).fetchall()
        finally:
            con.close()
        results = []
        for row in rows:
            state = self.get_current_state(strategy_name=str(row[0]), mode=str(row[1]))
            if state:
                results.append(state)
        return results

    def update_signal_order(
        self, *, signal_id: str, order_id: str, broker_order_id: str = "",
        status: str = "submitted", failure_reason: str = "",
    ) -> None:
        if not self.db_path.exists():
            return
        self.ensure_schema()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """update strategy_signals
                    set order_id = ?, broker_order_id = ?, status = ?, failure_reason = ?
                    where signal_id = ?""",
                    [order_id, broker_order_id, status, failure_reason, signal_id],
                )
            finally:
                con.close()

    def update_signal_fill(
        self, *, signal_id: str, fill_quantity: float, fill_price: float,
        commission: float = 0.0, fill_time: str = "", status: str = "filled",
    ) -> None:
        if not self.db_path.exists():
            return
        self.ensure_schema()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """update strategy_signals
                    set fill_quantity = coalesce(fill_quantity, 0.0) + ?,
                        fill_price = case
                            when coalesce(fill_quantity, 0.0) <= 0 then ?
                            else (coalesce(fill_price, 0.0) * coalesce(fill_quantity, 0.0) + ? * ?) / (coalesce(fill_quantity, 0.0) + ?)
                        end,
                        commission = coalesce(commission, 0.0) + ?,
                        fill_time = ?,
                        status = ?
                    where signal_id = ?""",
                    [
                        fill_quantity,
                        fill_price,
                        fill_price, fill_quantity, fill_quantity,
                        commission,
                        fill_time, status,
                        signal_id,
                    ],
                )
            finally:
                con.close()

    def get_signal_by_order(self, *, mode: str, order_id: str, signal_date: str = "") -> Optional[Dict[str, Any]]:
        if not self.db_path.exists() or not order_id:
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            if signal_date:
                row = con.execute(
                    """select * from strategy_signals
                    where mode = ? and order_id = ? and signal_date = ?
                    order by timestamp desc limit 1""",
                    [mode, order_id, str(signal_date)[:10]],
                ).fetchone()
            else:
                row = con.execute(
                    """select * from strategy_signals
                    where mode = ? and order_id = ?
                    order by timestamp desc limit 1""",
                    [mode, order_id],
                ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, _signal_columns())

    def get_signal_by_signature(
        self, *, mode: str, strategy_name: str, symbol: str,
        side: str, quantity: float, signal_date: str, order_type: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                """select * from strategy_signals
                where mode = ? and strategy_name = ? and symbol = ? and side = ?
                and quantity = ? and signal_date = ?
                order by timestamp desc limit 1""",
                [
                    mode, strategy_name, symbol, side.upper(),
                    float(quantity), str(signal_date)[:10],
                ],
            ).fetchone()
            if row is None and order_type:
                row = con.execute(
                    """select * from strategy_signals
                    where mode = ? and strategy_name = ? and symbol = ? and side = ?
                    and abs(quantity - ?) < 0.01 and signal_date = ?
                    order by timestamp desc limit 1""",
                    [
                        mode, strategy_name, symbol, side.upper(),
                        float(quantity), str(signal_date)[:10],
                    ],
                ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, _signal_columns())

    def record_capital_event(
        self,
        *,
        strategy_name: str,
        mode: str,
        event_type: str,
        symbol: str = "",
        amount: float = 0.0,
        quantity: float = 0.0,
        price: Optional[float] = None,
        effective_date: str = "",
        note: str = "",
        recorded_at: Optional[str] = None,
        apply_to_positions: bool = True,
    ) -> Dict[str, Any]:
        mode = _mode(mode)
        if event_type not in VALID_CAPITAL_EVENT_TYPES:
            raise ValueError(f"Invalid capital event_type: {event_type}")
        ts = recorded_at or datetime.now().isoformat()
        eff_date = effective_date or ts[:10]
        self.ensure_schema()
        with self._lock:
            con = self._connect()
            try:
                max_id = con.execute(
                    "select coalesce(max(event_id), 0) from strategy_capital_events"
                ).fetchone()
                event_id = int((max_id or [0])[0]) + 1
                con.execute(
                    """insert into strategy_capital_events
                    (event_id, strategy_name, mode, event_type, symbol, amount,
                     quantity, price, effective_date, note, recorded_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        event_id, strategy_name, mode, event_type, symbol,
                        float(amount), float(quantity),
                        _nullable_float(price), eff_date, str(note or ""), ts,
                    ],
                )
            finally:
                con.close()

        if apply_to_positions and symbol and event_type in {"DIVIDEND_CASH", "DIVIDEND_STOCK", "ADJUSTMENT"}:
            self._apply_capital_event_to_position(
                strategy_name=strategy_name, mode=mode, symbol=symbol,
                event_type=event_type, amount=amount, quantity=quantity,
                price=price, updated_at=ts,
            )

        return {
            "event_id": event_id,
            "strategy_name": strategy_name,
            "mode": mode,
            "event_type": event_type,
            "symbol": symbol,
            "amount": float(amount),
            "quantity": float(quantity),
            "price": price,
            "effective_date": eff_date,
            "note": str(note or ""),
            "recorded_at": ts,
        }

    def _apply_capital_event_to_position(
        self,
        *,
        strategy_name: str,
        mode: str,
        symbol: str,
        event_type: str,
        amount: float,
        quantity: float,
        price: Optional[float],
        updated_at: str,
    ) -> None:
        current = self.get_position(strategy_name=strategy_name, mode=mode, symbol=symbol)
        if current is None:
            return
        cur_qty = float(current.get("quantity", 0.0))
        cur_avg = float(current.get("avg_cost", 0.0))
        cur_rpnl = float(current.get("realized_pnl", 0.0))
        if cur_qty <= 0:
            return

        if event_type == "DIVIDEND_CASH":
            total_dividend = abs(amount)
            new_avg = (cur_avg * cur_qty - total_dividend) / cur_qty if cur_qty > 0 else 0.0
            new_avg = max(0.0, new_avg)
            self.upsert_position(
                strategy_name=strategy_name, mode=mode, symbol=symbol,
                quantity=cur_qty, avg_cost=new_avg, realized_pnl=cur_rpnl,
                updated_at=updated_at,
            )
        elif event_type == "DIVIDEND_STOCK":
            ratio = abs(quantity) / cur_qty if cur_qty > 0 else 0.0
            if ratio > 0:
                new_qty = cur_qty + abs(quantity)
                new_avg = cur_avg / (1.0 + ratio)
                self.upsert_position(
                    strategy_name=strategy_name, mode=mode, symbol=symbol,
                    quantity=new_qty, avg_cost=new_avg, realized_pnl=cur_rpnl,
                    updated_at=updated_at,
                )
        elif event_type == "ADJUSTMENT":
            new_qty = cur_qty * abs(quantity) if abs(quantity) > 0 else cur_qty
            factor = abs(quantity) if abs(quantity) > 0 else 1.0
            if factor > 0:
                new_avg = cur_avg / factor
                self.upsert_position(
                    strategy_name=strategy_name, mode=mode, symbol=symbol,
                    quantity=new_qty, avg_cost=new_avg, realized_pnl=cur_rpnl,
                    updated_at=updated_at,
                )

    def get_capital_events(
        self,
        *,
        strategy_name: str,
        mode: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select event_id, strategy_name, mode, event_type, symbol,
                   amount, quantity, price, effective_date, note, recorded_at
                from strategy_capital_events
                where strategy_name = ? and mode = ?
                order by event_id desc
                limit ?""",
                [strategy_name, mode, limit],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "event_id", "strategy_name", "mode", "event_type", "symbol",
            "amount", "quantity", "price", "effective_date", "note", "recorded_at",
        ]
        return [_row_to_dict(row, columns) for row in reversed(rows)]

    def get_all_capital_events(self, *, mode: str, days: int = 365) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select event_id, strategy_name, mode, event_type, symbol,
                   amount, quantity, price, effective_date, note, recorded_at
                from strategy_capital_events
                where mode = ? and effective_date >= ?
                order by event_id desc""",
                [mode, cutoff],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "event_id", "strategy_name", "mode", "event_type", "symbol",
            "amount", "quantity", "price", "effective_date", "note", "recorded_at",
        ]
        return [_row_to_dict(row, columns) for row in rows]

    def upsert_snapshot(
        self,
        *,
        strategy_name: str,
        mode: str,
        snapshot_date: str,
        nav: float = 0.0,
        cash: float = 0.0,
        market_value: float = 0.0,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        total_pnl: float = 0.0,
        source: str = "",
        recorded_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = _mode(mode)
        ts = recorded_at or datetime.now().isoformat()
        snap_date = str(snapshot_date)[:10]
        snapshot_id = f"snap:{strategy_name}:{mode}:{snap_date}:{source}"
        self.ensure_schema()
        row = {
            "snapshot_id": snapshot_id,
            "strategy_name": strategy_name,
            "mode": mode,
            "snapshot_date": snap_date,
            "nav": float(nav),
            "cash": float(cash),
            "market_value": float(market_value),
            "realized_pnl": float(realized_pnl),
            "unrealized_pnl": float(unrealized_pnl),
            "total_pnl": float(total_pnl),
            "source": str(source or ""),
            "recorded_at": ts,
        }
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """insert or replace into strategy_snapshots
                    (snapshot_id, strategy_name, mode, snapshot_date, nav, cash,
                     market_value, realized_pnl, unrealized_pnl, total_pnl, source, recorded_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["snapshot_id"], row["strategy_name"], row["mode"],
                        row["snapshot_date"], row["nav"], row["cash"],
                        row["market_value"], row["realized_pnl"],
                        row["unrealized_pnl"], row["total_pnl"],
                        row["source"], row["recorded_at"],
                    ],
                )
            finally:
                con.close()
        return row

    def get_snapshots(
        self,
        *,
        strategy_name: str,
        mode: str,
        limit: int = 365,
    ) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select snapshot_id, strategy_name, mode, snapshot_date, nav, cash,
                   market_value, realized_pnl, unrealized_pnl, total_pnl, source, recorded_at
                from strategy_snapshots
                where strategy_name = ? and mode = ?
                order by snapshot_date desc
                limit ?""",
                [strategy_name, mode, limit],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "snapshot_id", "strategy_name", "mode", "snapshot_date", "nav", "cash",
            "market_value", "realized_pnl", "unrealized_pnl", "total_pnl", "source", "recorded_at",
        ]
        return [_row_to_dict(row, columns) for row in reversed(rows)]

    def get_all_snapshots_for_mode(
        self,
        *,
        mode: str,
        limit: int = 365,
    ) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select snapshot_id, strategy_name, mode, snapshot_date, nav, cash,
                   market_value, realized_pnl, unrealized_pnl, total_pnl, source, recorded_at
                from strategy_snapshots
                where mode = ?
                order by snapshot_date desc
                limit ?""",
                [mode, limit],
            ).fetchall()
        finally:
            con.close()
        columns = [
            "snapshot_id", "strategy_name", "mode", "snapshot_date", "nav", "cash",
            "market_value", "realized_pnl", "unrealized_pnl", "total_pnl", "source", "recorded_at",
        ]
        return [_row_to_dict(row, columns) for row in reversed(rows)]

    def _connect(self, *, read_only: bool = False):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if read_only and not self.db_path.exists():
            read_only = False
        return duckdb.connect(str(self.db_path), read_only=read_only)


def _mode(mode: str) -> str:
    value = str(mode or "live").lower()
    if value not in VALID_MODES:
        raise ValueError("mode must be live or paper")
    return value


def _row_to_dict(row: tuple, columns: List[str]) -> Dict[str, Any]:
    result = {}
    for i, col in enumerate(columns):
        if i < len(row) and row[i] is not None:
            result[col] = row[i]
    return result


def _signal_columns() -> List[str]:
    return [
        "signal_id", "strategy_name", "mode", "timestamp", "signal_date",
        "symbol", "side", "quantity", "order_type", "reference_price",
        "status", "order_id", "broker_order_id", "fill_quantity",
        "fill_price", "commission", "fill_time", "failure_reason",
        "submit_date", "cost_bps", "record_date",
    ]


def _nullable_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
