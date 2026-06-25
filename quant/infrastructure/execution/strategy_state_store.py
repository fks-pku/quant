"""Strategy dashboard state store for control state, positions, ledgers, and snapshots."""

import hashlib
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
                    record_date text not null,
                    execution_reference_price double
                )
            """)
            con.execute("""
                create table if not exists strategy_orders (
                    order_row_id text primary key,
                    signal_id text not null default '',
                    strategy_name text not null,
                    mode text not null,
                    timestamp text not null,
                    signal_date text not null default '',
                    submit_date text not null default '',
                    record_date text not null,
                    symbol text not null,
                    side text not null,
                    quantity double not null default 0.0,
                    order_type text not null default '',
                    limit_price double,
                    status text not null default 'submitted',
                    order_id text not null default '',
                    broker_order_id text not null default '',
                    failure_reason text not null default '',
                    cost_bps double,
                    execution_reference_price double
                )
            """)
            con.execute("""
                create table if not exists strategy_fills (
                    fill_id text primary key,
                    order_row_id text not null default '',
                    signal_id text not null default '',
                    strategy_name text not null,
                    mode text not null,
                    timestamp text not null,
                    signal_date text not null default '',
                    record_date text not null,
                    symbol text not null,
                    side text not null,
                    quantity double not null default 0.0,
                    price double not null default 0.0,
                    commission double not null default 0.0,
                    order_id text not null default '',
                    broker_order_id text not null default '',
                    source text not null default ''
                )
            """)
            con.execute("""
                alter table strategy_signals add column if not exists submit_date text default ''
            """)
            con.execute("""
                alter table strategy_signals add column if not exists cost_bps double
            """)
            con.execute("""
                alter table strategy_signals add column if not exists execution_reference_price double
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
                create index if not exists idx_orders_strategy_mode
                on strategy_orders (strategy_name, mode, record_date)
            """)
            con.execute("""
                create index if not exists idx_orders_order_id
                on strategy_orders (mode, order_id, record_date)
            """)
            con.execute("""
                create index if not exists idx_orders_broker_order_id
                on strategy_orders (mode, broker_order_id, record_date)
            """)
            con.execute("""
                create index if not exists idx_fills_strategy_mode
                on strategy_fills (strategy_name, mode, record_date)
            """)
            con.execute("""
                create index if not exists idx_fills_order_id
                on strategy_fills (mode, order_id, record_date)
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
            con.execute("""
                create table if not exists strategy_runtime_states (
                    state_id text primary key,
                    strategy_name text not null,
                    mode text not null,
                    as_of_date text not null,
                    stage text not null,
                    strategy_class text not null default '',
                    schema_version integer not null default 1,
                    state_json text not null,
                    state_hash text not null,
                    previous_state_hash text not null default '',
                    config_hash text not null default '',
                    run_id text not null default '',
                    recorded_at text not null
                )
            """)
            con.execute("""
                create index if not exists idx_runtime_states_strategy_mode_date
                on strategy_runtime_states (strategy_name, mode, as_of_date, stage)
            """)
            con.execute("""
                create index if not exists idx_runtime_states_latest
                on strategy_runtime_states (strategy_name, mode, recorded_at)
            """)
            self._backfill_split_ledgers(con)
        finally:
            con.close()

    def _backfill_split_ledgers(self, con: Any) -> None:
        con.execute("""
            insert into strategy_orders
            (order_row_id, signal_id, strategy_name, mode, timestamp, signal_date,
             submit_date, record_date, symbol, side, quantity, order_type,
             limit_price, status, order_id, broker_order_id, failure_reason,
             cost_bps, execution_reference_price)
            select
                'ord:' || signal_id,
                signal_id,
                strategy_name,
                mode,
                timestamp,
                signal_date,
                submit_date,
                coalesce(nullif(submit_date, ''), nullif(record_date, ''), signal_date),
                symbol,
                side,
                quantity,
                order_type,
                reference_price,
                status,
                order_id,
                broker_order_id,
                failure_reason,
                cost_bps,
                execution_reference_price
            from strategy_signals s
            where (
                coalesce(broker_order_id, '') <> ''
                or lower(coalesce(status, '')) in (
                    'submitted', 'filled', 'partial', 'cancelled', 'canceled',
                    'rejected', 'failed', 'error', 'dropped', 'expired'
                )
            )
            and not exists (
                select 1 from strategy_orders o where o.order_row_id = 'ord:' || s.signal_id
            )
        """)
        con.execute("""
            insert into strategy_fills
            (fill_id, order_row_id, signal_id, strategy_name, mode, timestamp,
             signal_date, record_date, symbol, side, quantity, price, commission,
             order_id, broker_order_id, source)
            select
                'fill:' || signal_id,
                case
                    when coalesce(order_id, '') <> '' or coalesce(broker_order_id, '') <> ''
                    then 'ord:' || signal_id
                    else ''
                end,
                signal_id,
                strategy_name,
                mode,
                coalesce(nullif(fill_time, ''), timestamp),
                signal_date,
                coalesce(nullif(substr(fill_time, 1, 10), ''), nullif(submit_date, ''), nullif(record_date, ''), signal_date),
                symbol,
                side,
                fill_quantity,
                coalesce(fill_price, 0.0),
                commission,
                order_id,
                broker_order_id,
                'legacy_strategy_signals'
            from strategy_signals s
            where coalesce(fill_quantity, 0.0) > 0
            and not exists (
                select 1 from strategy_fills f where f.fill_id = 'fill:' || s.signal_id
            )
        """)

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

    def upsert_runtime_state(
        self,
        *,
        strategy_name: str,
        mode: str,
        as_of_date: str,
        stage: str,
        state: Dict[str, Any],
        strategy_class: str = "",
        schema_version: int = 1,
        previous_state_hash: str = "",
        config_hash: str = "",
        run_id: str = "",
        recorded_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = _mode(mode)
        ts = recorded_at or datetime.now().isoformat()
        state_json = _canonical_json(state or {})
        state_hash = hashlib.sha256(state_json.encode("utf-8")).hexdigest()
        row = {
            "state_id": _make_runtime_state_id(
                strategy_name=strategy_name,
                mode=mode,
                as_of_date=str(as_of_date)[:10],
                stage=stage,
                schema_version=schema_version,
                state_hash=state_hash,
            ),
            "strategy_name": str(strategy_name or ""),
            "mode": mode,
            "as_of_date": str(as_of_date)[:10],
            "stage": str(stage or ""),
            "strategy_class": str(strategy_class or ""),
            "schema_version": int(schema_version or 1),
            "state_json": state_json,
            "state": json.loads(state_json),
            "state_hash": state_hash,
            "previous_state_hash": str(previous_state_hash or ""),
            "config_hash": str(config_hash or ""),
            "run_id": str(run_id or ""),
            "recorded_at": ts,
        }
        self.ensure_schema()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """insert or replace into strategy_runtime_states
                    (state_id, strategy_name, mode, as_of_date, stage, strategy_class,
                     schema_version, state_json, state_hash, previous_state_hash,
                     config_hash, run_id, recorded_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["state_id"], row["strategy_name"], row["mode"],
                        row["as_of_date"], row["stage"], row["strategy_class"],
                        row["schema_version"], row["state_json"], row["state_hash"],
                        row["previous_state_hash"], row["config_hash"],
                        row["run_id"], row["recorded_at"],
                    ],
                )
            finally:
                con.close()
        return row

    def get_latest_runtime_state(
        self,
        *,
        strategy_name: str,
        mode: str,
        stage: Optional[str] = None,
        as_of_date: Optional[str] = None,
        before_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            if not _table_exists(con, "strategy_runtime_states"):
                return None
            where = ["strategy_name = ?", "mode = ?"]
            params: List[Any] = [strategy_name, mode]
            if stage:
                where.append("stage = ?")
                params.append(str(stage))
            if as_of_date:
                where.append("as_of_date = ?")
                params.append(str(as_of_date)[:10])
            if before_date:
                where.append("as_of_date < ?")
                params.append(str(before_date)[:10])
            row = con.execute(
                f"""select * from strategy_runtime_states
                where {' and '.join(where)}
                order by as_of_date desc, recorded_at desc
                limit 1""",
                params,
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _decode_runtime_state_row(row)

    def get_latest_signal_date(
        self,
        *,
        strategy_name: str,
        mode: str,
        before_date: Optional[str] = None,
    ) -> Optional[str]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            if not _table_exists(con, "strategy_signals"):
                return None
            where = ["strategy_name = ?", "mode = ?", "signal_date <> ''"]
            params: List[Any] = [strategy_name, mode]
            if before_date:
                where.append("signal_date < ?")
                params.append(str(before_date)[:10])
            row = con.execute(
                f"""select signal_date from strategy_signals
                where {' and '.join(where)}
                order by signal_date desc, timestamp desc
                limit 1""",
                params,
            ).fetchone()
        finally:
            con.close()
        if row is None or not row[0]:
            return None
        return str(row[0])[:10]

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
            tables = [
                table for table in (
                    "strategy_states",
                    "strategy_signals",
                    "strategy_orders",
                    "strategy_fills",
                    "strategy_positions",
                )
                if _table_exists(con, table)
            ]
            if not tables:
                return []
            union_sql = "\nunion\n".join(f"select strategy_name from {table}" for table in tables)
            rows = con.execute(
                f"select distinct strategy_name from ({union_sql}) order by strategy_name"
            ).fetchall()
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
            "execution_reference_price": _nullable_float(signal.get("execution_reference_price")),
        }
        with self._lock:
            con = self._connect()
            try:
                row = self._reuse_open_pending_signal_identity(con, row)
                con.execute(
                    """insert or replace into strategy_signals
                    (signal_id, strategy_name, mode, timestamp, signal_date, symbol, side,
                     quantity, order_type, reference_price, status, order_id, broker_order_id,
                     fill_quantity, fill_price, commission, fill_time, failure_reason,
                     submit_date, cost_bps, record_date, execution_reference_price)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["signal_id"], row["strategy_name"], row["mode"],
                        row["timestamp"], row["signal_date"], row["symbol"], row["side"],
                        row["quantity"], row["order_type"], row["reference_price"],
                        row["status"], row["order_id"], row["broker_order_id"],
                        row["fill_quantity"], row["fill_price"], row["commission"],
                        row["fill_time"], row["failure_reason"],
                        row["submit_date"], row["cost_bps"], row["record_date"],
                        row["execution_reference_price"],
                    ],
                )
                self._backfill_split_ledgers(con)
            finally:
                con.close()
        return row

    def _reuse_open_pending_signal_identity(self, con: Any, row: Dict[str, Any]) -> Dict[str, Any]:
        status = str(row.get("status") or "").lower()
        if status not in {"accepted", "pending", "queued", "pending_submit"}:
            return row
        if not str(row.get("submit_date") or ""):
            return row
        if str(row.get("broker_order_id") or "") or float(row.get("fill_quantity") or 0.0) > 0:
            return row
        existing = con.execute(
            """select signal_id, order_id from strategy_signals
            where mode = ? and strategy_name = ? and signal_date = ? and submit_date = ?
              and symbol = ? and upper(side) = upper(?) and abs(quantity - ?) < 0.0000001
              and upper(order_type) = upper(?)
              and lower(status) in ('accepted', 'pending', 'queued', 'pending_submit')
              and coalesce(broker_order_id, '') = '' and coalesce(fill_quantity, 0) <= 0
            order by timestamp asc limit 1""",
            [
                row["mode"], row["strategy_name"], row["signal_date"], row["submit_date"],
                row["symbol"], row["side"], float(row["quantity"]), row["order_type"],
            ],
        ).fetchone()
        if existing is None:
            return row
        reused = dict(row)
        reused["signal_id"] = str(existing[0] or row["signal_id"])
        if existing[1]:
            reused["order_id"] = str(existing[1])
        return reused

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

    def get_pending_signals_for_submit(
        self,
        *,
        mode: str,
        signal_date: str,
        submit_date: str,
    ) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                """select * from strategy_signals s
                where s.mode = ?
                  and s.signal_date = ?
                  and s.submit_date = ?
                  and lower(s.status) in ('accepted', 'pending', 'queued', 'pending_submit')
                  and not exists (
                      select 1 from strategy_orders o
                      where o.mode = s.mode
                        and o.strategy_name = s.strategy_name
                        and o.submit_date = ?
                        and (
                            (s.signal_id <> '' and o.signal_id = s.signal_id)
                            or (
                                o.symbol = s.symbol
                                and upper(o.side) = upper(s.side)
                                and abs(o.quantity - s.quantity) < 0.01
                            )
                        )
                        and lower(o.status) in ('submitted', 'partial', 'filled', 'cancelled', 'canceled', 'rejected', 'failed')
                  )
                  and not exists (
                      select 1 from strategy_fills f
                      where f.mode = s.mode
                        and f.strategy_name = s.strategy_name
                        and f.record_date = ?
                        and (
                            (s.signal_id <> '' and f.signal_id = s.signal_id)
                            or (
                                f.symbol = s.symbol
                                and upper(f.side) = upper(s.side)
                                and abs(f.quantity - s.quantity) < 0.01
                            )
                        )
                  )
                order by s.strategy_name, s.timestamp, s.symbol""",
                [mode, str(signal_date)[:10], str(submit_date)[:10], str(submit_date)[:10], str(submit_date)[:10]],
            ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _signal_columns()) for row in rows]

    def upsert_order(self, *, order: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_schema()
        record_date = str(
            order.get("record_date")
            or order.get("submit_date")
            or order.get("execution_date")
            or order.get("timestamp")
            or datetime.now().isoformat()
        )[:10]
        row = {
            "signal_id": str(order.get("signal_id") or ""),
            "strategy_name": str(order.get("strategy_name") or ""),
            "mode": _mode(order.get("mode") or "live"),
            "timestamp": str(order.get("timestamp") or ""),
            "signal_date": str(order.get("signal_date") or "")[:10],
            "submit_date": str(order.get("submit_date") or order.get("execution_date") or record_date)[:10],
            "record_date": record_date,
            "symbol": str(order.get("symbol") or ""),
            "side": str(order.get("side") or ""),
            "quantity": float(order.get("quantity", 0.0)),
            "order_type": str(order.get("order_type") or ""),
            "limit_price": _nullable_float(
                order.get("limit_price")
                if "limit_price" in order
                else order.get("price", order.get("reference_price"))
            ),
            "status": str(order.get("status") or "submitted"),
            "order_id": str(order.get("order_id") or ""),
            "broker_order_id": str(order.get("broker_order_id") or ""),
            "failure_reason": str(order.get("failure_reason") or order.get("reason") or ""),
            "cost_bps": _nullable_float(order.get("cost_bps") or order.get("execution_cost_bps")),
            "execution_reference_price": _nullable_float(order.get("execution_reference_price")),
        }
        if not row["signal_date"] and row["signal_id"]:
            signal = self.get_signal(signal_id=row["signal_id"])
            row["signal_date"] = str((signal or {}).get("signal_date") or "")[:10]
        row["order_row_id"] = str(order.get("order_row_id") or _make_order_row_id(row))
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """insert or replace into strategy_orders
                    (order_row_id, signal_id, strategy_name, mode, timestamp,
                     signal_date, submit_date, record_date, symbol, side, quantity,
                     order_type, limit_price, status, order_id, broker_order_id,
                     failure_reason, cost_bps, execution_reference_price)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["order_row_id"], row["signal_id"], row["strategy_name"], row["mode"],
                        row["timestamp"], row["signal_date"], row["submit_date"], row["record_date"],
                        row["symbol"], row["side"], row["quantity"], row["order_type"],
                        row["limit_price"], row["status"], row["order_id"], row["broker_order_id"],
                        row["failure_reason"], row["cost_bps"], row["execution_reference_price"],
                    ],
                )
            finally:
                con.close()
        return row

    def get_orders(
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
            if not _table_exists(con, "strategy_orders"):
                return self._legacy_orders(con, strategy_name=strategy_name, mode=mode, limit=limit, after_date=after_date)
            if after_date:
                rows = con.execute(
                    """select * from strategy_orders
                    where strategy_name = ? and mode = ? and record_date >= ?
                    order by timestamp desc limit ?""",
                    [strategy_name, mode, str(after_date)[:10], limit],
                ).fetchall()
            else:
                rows = con.execute(
                    """select * from strategy_orders
                    where strategy_name = ? and mode = ?
                    order by timestamp desc limit ?""",
                    [strategy_name, mode, limit],
                ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _order_columns()) for row in reversed(rows)]

    def get_recent_orders(self, *, mode: str, days: int = 30) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        con = self._connect(read_only=True)
        try:
            if not _table_exists(con, "strategy_orders"):
                return self._legacy_recent_orders(con, mode=mode, cutoff=cutoff)
            rows = con.execute(
                """select * from strategy_orders
                where mode = ? and record_date >= ?
                order by timestamp desc""",
                [mode, cutoff],
            ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _order_columns()) for row in rows]

    def get_order_by_order_id(
        self,
        *,
        mode: str,
        order_id: str,
        record_date: str = "",
        strategy_name: str = "",
        symbol: str = "",
        side: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists() or not order_id:
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            if not _table_exists(con, "strategy_orders"):
                return None
            filters = ["mode = ?", "(order_id = ? or broker_order_id = ?)"]
            params: List[Any] = [mode, order_id, order_id]
            if record_date:
                filters.append("record_date = ?")
                params.append(str(record_date)[:10])
            if strategy_name:
                filters.append("strategy_name = ?")
                params.append(strategy_name)
            if symbol:
                filters.append("symbol = ?")
                params.append(symbol)
            if side:
                filters.append("upper(side) = upper(?)")
                params.append(side)
            row = con.execute(
                f"""select * from strategy_orders
                where {' and '.join(filters)}
                order by timestamp desc limit 1""",
                params,
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, _order_columns())

    def upsert_fill(self, *, fill: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_schema()
        record_date = str(fill.get("record_date") or fill.get("timestamp") or datetime.now().isoformat())[:10]
        row = {
            "order_row_id": str(fill.get("order_row_id") or ""),
            "signal_id": str(fill.get("signal_id") or ""),
            "strategy_name": str(fill.get("strategy_name") or ""),
            "mode": _mode(fill.get("mode") or "live"),
            "timestamp": str(fill.get("timestamp") or ""),
            "signal_date": str(fill.get("signal_date") or "")[:10],
            "record_date": record_date,
            "symbol": str(fill.get("symbol") or ""),
            "side": str(fill.get("side") or ""),
            "quantity": float(fill.get("quantity", fill.get("fill_quantity", 0.0))),
            "price": float(fill.get("price", fill.get("fill_price", 0.0))),
            "commission": float(fill.get("commission", 0.0)),
            "order_id": str(fill.get("order_id") or ""),
            "broker_order_id": str(fill.get("broker_order_id") or ""),
            "source": str(fill.get("source") or ""),
        }
        row["fill_id"] = str(fill.get("fill_id") or _make_fill_id(row))
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """insert or replace into strategy_fills
                    (fill_id, order_row_id, signal_id, strategy_name, mode, timestamp,
                     signal_date, record_date, symbol, side, quantity, price,
                     commission, order_id, broker_order_id, source)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        row["fill_id"], row["order_row_id"], row["signal_id"],
                        row["strategy_name"], row["mode"], row["timestamp"],
                        row["signal_date"], row["record_date"], row["symbol"],
                        row["side"], row["quantity"], row["price"], row["commission"],
                        row["order_id"], row["broker_order_id"], row["source"],
                    ],
                )
            finally:
                con.close()
        return row

    def get_fills(
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
            if not _table_exists(con, "strategy_fills"):
                return self._legacy_fills(con, strategy_name=strategy_name, mode=mode, limit=limit, after_date=after_date)
            if after_date:
                rows = con.execute(
                    """select * from strategy_fills
                    where strategy_name = ? and mode = ? and record_date >= ?
                    order by timestamp desc limit ?""",
                    [strategy_name, mode, str(after_date)[:10], limit],
                ).fetchall()
            else:
                rows = con.execute(
                    """select * from strategy_fills
                    where strategy_name = ? and mode = ?
                    order by timestamp desc limit ?""",
                    [strategy_name, mode, limit],
                ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _fill_columns()) for row in reversed(rows)]

    def get_recent_fills(self, *, mode: str, days: int = 30) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        mode = _mode(mode)
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        con = self._connect(read_only=True)
        try:
            if not _table_exists(con, "strategy_fills"):
                return self._legacy_recent_fills(con, mode=mode, cutoff=cutoff)
            rows = con.execute(
                """select * from strategy_fills
                where mode = ? and record_date >= ?
                order by timestamp desc""",
                [mode, cutoff],
            ).fetchall()
        finally:
            con.close()
        return [_row_to_dict(row, _fill_columns()) for row in rows]

    def get_signal_for_submission(
        self,
        *,
        mode: str,
        strategy_name: str,
        symbol: str,
        side: str,
        quantity: float,
        submit_date: str,
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        mode = _mode(mode)
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                """select * from strategy_signals
                where mode = ? and strategy_name = ? and symbol = ?
                  and upper(side) = upper(?) and abs(quantity - ?) < 0.01
                  and submit_date = ?
                order by timestamp desc limit 1""",
                [
                    mode, strategy_name, symbol, side,
                    float(quantity), str(submit_date)[:10],
                ],
            ).fetchone()
            if row is None:
                row = con.execute(
                    """select * from strategy_signals
                    where mode = ? and strategy_name = ? and symbol = ?
                      and upper(side) = upper(?) and abs(quantity - ?) < 0.01
                      and signal_date = ?
                    order by timestamp desc limit 1""",
                    [
                        mode, strategy_name, symbol, side,
                        float(quantity), str(submit_date)[:10],
                    ],
                ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return _row_to_dict(row, _signal_columns())

    def _legacy_orders(
        self,
        con: Any,
        *,
        strategy_name: str,
        mode: str,
        limit: int,
        after_date: Optional[str],
    ) -> List[Dict[str, Any]]:
        if after_date:
            rows = con.execute(
                """select * from strategy_signals
                where strategy_name = ? and mode = ? and record_date >= ?
                  and order_id <> ''
                order by timestamp desc limit ?""",
                [strategy_name, mode, str(after_date)[:10], limit],
            ).fetchall()
        else:
            rows = con.execute(
                """select * from strategy_signals
                where strategy_name = ? and mode = ? and order_id <> ''
                order by timestamp desc limit ?""",
                [strategy_name, mode, limit],
            ).fetchall()
        return [_legacy_order_from_signal(_row_to_dict(row, _signal_columns())) for row in reversed(rows)]

    def _legacy_recent_orders(self, con: Any, *, mode: str, cutoff: str) -> List[Dict[str, Any]]:
        rows = con.execute(
            """select * from strategy_signals
            where mode = ? and record_date >= ? and order_id <> ''
            order by timestamp desc""",
            [mode, cutoff],
        ).fetchall()
        return [_legacy_order_from_signal(_row_to_dict(row, _signal_columns())) for row in rows]

    def _legacy_fills(
        self,
        con: Any,
        *,
        strategy_name: str,
        mode: str,
        limit: int,
        after_date: Optional[str],
    ) -> List[Dict[str, Any]]:
        if after_date:
            rows = con.execute(
                """select * from strategy_signals
                where strategy_name = ? and mode = ? and record_date >= ?
                  and fill_quantity > 0
                order by timestamp desc limit ?""",
                [strategy_name, mode, str(after_date)[:10], limit],
            ).fetchall()
        else:
            rows = con.execute(
                """select * from strategy_signals
                where strategy_name = ? and mode = ? and fill_quantity > 0
                order by timestamp desc limit ?""",
                [strategy_name, mode, limit],
            ).fetchall()
        return [_legacy_fill_from_signal(_row_to_dict(row, _signal_columns())) for row in reversed(rows)]

    def _legacy_recent_fills(self, con: Any, *, mode: str, cutoff: str) -> List[Dict[str, Any]]:
        rows = con.execute(
            """select * from strategy_signals
            where mode = ? and record_date >= ? and fill_quantity > 0
            order by timestamp desc""",
            [mode, cutoff],
        ).fetchall()
        return [_legacy_fill_from_signal(_row_to_dict(row, _signal_columns())) for row in rows]

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
        self.ensure_schema()
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
                order_where_date = " and (signal_date = ? or record_date = ?)" if signal_date else ""
                order_params = [mode, *ids, *ids]
                if signal_date:
                    order_params.extend([str(signal_date)[:10], str(signal_date)[:10]])
                order_count = con.execute(
                    f"""select count(*) from strategy_orders
                    where mode = ? and (order_id in ({placeholders}) or broker_order_id in ({placeholders})){order_where_date}""",
                    order_params,
                ).fetchone()
                con.execute(
                    f"""delete from strategy_orders
                    where mode = ? and (order_id in ({placeholders}) or broker_order_id in ({placeholders})){order_where_date}""",
                    order_params,
                )
                fill_count = con.execute(
                    f"""select count(*) from strategy_fills
                    where mode = ? and (order_id in ({placeholders}) or broker_order_id in ({placeholders})){order_where_date}""",
                    order_params,
                ).fetchone()
                con.execute(
                    f"""delete from strategy_fills
                    where mode = ? and (order_id in ({placeholders}) or broker_order_id in ({placeholders})){order_where_date}""",
                    order_params,
                )
            finally:
                con.close()
        return int((count or [0])[0]) + int((order_count or [0])[0]) + int((fill_count or [0])[0])

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
        execution_reference_price: Optional[float] = None,
        cost_bps: Optional[float] = None,
    ) -> None:
        if not self.db_path.exists():
            return
        self.ensure_schema()
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """update strategy_signals
                    set order_id = ?, broker_order_id = ?, status = ?, failure_reason = ?,
                        execution_reference_price = coalesce(?, execution_reference_price),
                        cost_bps = coalesce(?, cost_bps)
                    where signal_id = ?""",
                    [
                        order_id, broker_order_id, status, failure_reason,
                        _nullable_float(execution_reference_price),
                        _nullable_float(cost_bps),
                        signal_id,
                    ],
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

    def get_signal_by_order(
        self,
        *,
        mode: str,
        order_id: str,
        signal_date: str = "",
        strategy_name: str = "",
        symbol: str = "",
        side: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists() or not order_id:
            return None
        mode = _mode(mode)
        order = self.get_order_by_order_id(
            mode=mode,
            order_id=order_id,
            record_date=signal_date,
            strategy_name=strategy_name,
            symbol=symbol,
            side=side,
        )
        if order and order.get("signal_id"):
            signal = self.get_signal(signal_id=str(order.get("signal_id") or ""))
            if signal:
                merged = dict(signal)
                merged["order_id"] = order.get("order_id", "")
                merged["broker_order_id"] = order.get("broker_order_id", "")
                merged["status"] = order.get("status", merged.get("status", ""))
                merged["failure_reason"] = order.get("failure_reason", "")
                merged["submit_date"] = order.get("submit_date", merged.get("submit_date", ""))
                merged["record_date"] = order.get("record_date", merged.get("record_date", ""))
                merged["execution_reference_price"] = order.get(
                    "execution_reference_price",
                    merged.get("execution_reference_price"),
                )
                return merged
        filters = ["mode = ?", "order_id = ?"]
        params: List[Any] = [mode, order_id]
        if signal_date:
            filters.append("signal_date = ?")
            params.append(str(signal_date)[:10])
        if strategy_name:
            filters.append("strategy_name = ?")
            params.append(strategy_name)
        if symbol:
            filters.append("symbol = ?")
            params.append(symbol)
        if side:
            filters.append("upper(side) = upper(?)")
            params.append(side)
        where_clause = " and ".join(filters)
        con = self._connect(read_only=True)
        try:
            row = con.execute(
                f"""select * from strategy_signals
                where {where_clause}
                order by timestamp desc limit 1""",
                params,
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
        "submit_date", "cost_bps", "record_date", "execution_reference_price",
    ]


def _order_columns() -> List[str]:
    return [
        "order_row_id", "signal_id", "strategy_name", "mode", "timestamp",
        "signal_date", "submit_date", "record_date", "symbol", "side",
        "quantity", "order_type", "limit_price", "status", "order_id",
        "broker_order_id", "failure_reason", "cost_bps",
        "execution_reference_price",
    ]


def _fill_columns() -> List[str]:
    return [
        "fill_id", "order_row_id", "signal_id", "strategy_name", "mode",
        "timestamp", "signal_date", "record_date", "symbol", "side",
        "quantity", "price", "commission", "order_id", "broker_order_id",
        "source",
    ]


def _runtime_state_columns() -> List[str]:
    return [
        "state_id", "strategy_name", "mode", "as_of_date", "stage",
        "strategy_class", "schema_version", "state_json", "state_hash",
        "previous_state_hash", "config_hash", "run_id", "recorded_at",
    ]


def _decode_runtime_state_row(row: tuple) -> Dict[str, Any]:
    decoded = _row_to_dict(row, _runtime_state_columns())
    state_json = str(decoded.get("state_json") or "{}")
    try:
        decoded["state"] = json.loads(state_json)
    except json.JSONDecodeError:
        decoded["state"] = {}
    return decoded


def _table_exists(con: Any, table_name: str) -> bool:
    row = con.execute(
        "select count(*) from information_schema.tables where table_name = ?",
        [table_name],
    ).fetchone()
    return bool(row and int(row[0] or 0) > 0)


def _make_order_row_id(row: Dict[str, Any]) -> str:
    parts = [
        row.get("strategy_name", ""),
        row.get("mode", ""),
        row.get("signal_id", ""),
        row.get("record_date", ""),
        row.get("symbol", ""),
        row.get("side", ""),
        row.get("quantity", ""),
        row.get("order_id", ""),
        row.get("broker_order_id", ""),
        row.get("timestamp", ""),
    ]
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return f"ord:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _make_fill_id(row: Dict[str, Any]) -> str:
    parts = [
        row.get("strategy_name", ""),
        row.get("mode", ""),
        row.get("order_row_id", ""),
        row.get("signal_id", ""),
        row.get("record_date", ""),
        row.get("symbol", ""),
        row.get("side", ""),
        row.get("quantity", ""),
        row.get("price", ""),
        row.get("order_id", ""),
        row.get("broker_order_id", ""),
        row.get("timestamp", ""),
    ]
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return f"fill:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _make_runtime_state_id(
    *,
    strategy_name: str,
    mode: str,
    as_of_date: str,
    stage: str,
    schema_version: int,
    state_hash: str,
) -> str:
    parts = [
        str(strategy_name or ""),
        str(mode or ""),
        str(as_of_date or "")[:10],
        str(stage or ""),
        int(schema_version or 1),
        str(state_hash or ""),
    ]
    raw = _canonical_json(parts)
    return f"state:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def _legacy_order_from_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "order_row_id": f"ord:{signal.get('signal_id', '')}",
        "signal_id": signal.get("signal_id", ""),
        "strategy_name": signal.get("strategy_name", ""),
        "mode": signal.get("mode", ""),
        "timestamp": signal.get("timestamp", ""),
        "signal_date": signal.get("signal_date", ""),
        "submit_date": signal.get("submit_date", ""),
        "record_date": signal.get("submit_date") or signal.get("record_date") or signal.get("signal_date", ""),
        "symbol": signal.get("symbol", ""),
        "side": signal.get("side", ""),
        "quantity": signal.get("quantity", 0.0),
        "order_type": signal.get("order_type", ""),
        "limit_price": signal.get("reference_price"),
        "status": signal.get("status", ""),
        "order_id": signal.get("order_id", ""),
        "broker_order_id": signal.get("broker_order_id", ""),
        "failure_reason": signal.get("failure_reason", ""),
        "cost_bps": signal.get("cost_bps"),
        "execution_reference_price": signal.get("execution_reference_price"),
    }


def _legacy_fill_from_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "fill_id": f"fill:{signal.get('signal_id', '')}",
        "order_row_id": f"ord:{signal.get('signal_id', '')}" if signal.get("order_id") else "",
        "signal_id": signal.get("signal_id", ""),
        "strategy_name": signal.get("strategy_name", ""),
        "mode": signal.get("mode", ""),
        "timestamp": signal.get("fill_time") or signal.get("timestamp", ""),
        "signal_date": signal.get("signal_date", ""),
        "record_date": str(signal.get("fill_time") or signal.get("submit_date") or signal.get("record_date") or signal.get("signal_date", ""))[:10],
        "symbol": signal.get("symbol", ""),
        "side": signal.get("side", ""),
        "quantity": signal.get("fill_quantity", 0.0),
        "price": signal.get("fill_price", 0.0),
        "commission": signal.get("commission", 0.0),
        "order_id": signal.get("order_id", ""),
        "broker_order_id": signal.get("broker_order_id", ""),
        "source": "legacy_strategy_signals",
    }


def _nullable_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
