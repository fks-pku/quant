"""Strict strategy dashboard state tables backed by DuckDB."""

import hashlib
import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import duckdb


DEFAULT_STRATEGY_STATE_DB = Path(__file__).resolve().parents[1] / "var" / "strategy_state.duckdb"
VALID_STATE_MODES = {"live", "paper"}
STATE_KIND_ORDER = (
    "operations",
    "runs",
    "control_state",
    "capital_events",
    "signals",
    "submit_attempts",
    "orders",
    "fills",
    "positions",
    "snapshots",
    "watermarks",
    "reconciliations",
)
FACT_KINDS = {"signals", "submit_attempts", "orders", "fills", "positions", "snapshots"}
KIND_TABLES = {
    "operations": "strategy_operations",
    "runs": "strategy_runs",
    "control_state": "strategy_control_state",
    "capital_events": "strategy_capital_events",
    "signals": "strategy_signals",
    "submit_attempts": "strategy_submit_attempts",
    "orders": "strategy_orders",
    "fills": "strategy_fills",
    "positions": "strategy_positions",
    "snapshots": "strategy_nav_snapshots",
    "watermarks": "strategy_watermarks",
    "reconciliations": "strategy_reconciliations",
}
TABLE_KEYS = {
    "strategy_operations": "operation_id",
    "strategy_runs": "run_row_id",
    "strategy_control_state": "control_state_id",
    "strategy_capital_events": "capital_event_id",
    "strategy_signals": "signal_id",
    "strategy_submit_attempts": "attempt_id",
    "strategy_orders": "order_row_id",
    "strategy_fills": "fill_row_id",
    "strategy_positions": "position_id",
    "strategy_nav_snapshots": "snapshot_id",
    "strategy_watermarks": "watermark_id",
    "strategy_reconciliations": "reconciliation_id",
}


class StrategyStateStore:
    def __init__(self, db_path: Optional[Any] = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_STRATEGY_STATE_DB
        self._lock = threading.RLock()

    def ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            for ddl in _schema_statements():
                con.execute(ddl)
        finally:
            con.close()

    def record_operation(
        self,
        *,
        mode: str,
        strategy_name: str,
        operation_type: str,
        requested_by: str = "system",
        requested_at: Optional[Any] = None,
        effective_date: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        status: str = "applied",
        applied_at: Optional[Any] = None,
        failure_reason: str = "",
        idempotency_key: str = "",
        run_id: str = "",
        raw: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = _timestamp_text(requested_at or datetime.now())
        applied = _timestamp_text(applied_at or ts) if status == "applied" else ""
        row = {
            "operation_id": _stable_id("operation", _mode(mode), strategy_name, operation_type, ts, requested_by, idempotency_key),
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "run_id": run_id,
            "operation_type": operation_type,
            "requested_by": requested_by,
            "requested_at": ts,
            "effective_date": _date_text(effective_date) or ts[:10],
            "params_json": params or {},
            "status": status,
            "applied_at": applied,
            "failure_reason": failure_reason,
            "idempotency_key": idempotency_key,
            "timestamp": ts,
            "record_date": ts[:10],
        }
        if raw:
            for key, value in raw.items():
                if key not in row or row.get(key) is None or row.get(key) == "":
                    row[key] = value
        return self._replace("strategy_operations", row)

    def ensure_run(
        self,
        *,
        mode: str,
        strategy_name: str,
        initial_cash: float,
        started_at: Optional[Any] = None,
        operation_id: str = "",
        status: str = "active",
        base_currency: str = "CNY",
    ) -> Optional[Dict[str, Any]]:
        if _float(initial_cash) <= 0:
            return None
        active = self.active_run(mode=mode, strategy_name=strategy_name)
        if active:
            return active
        ts = _timestamp_text(started_at or datetime.now())
        run_id = _stable_id("run", _mode(mode), strategy_name or "default", ts)
        row = {
            "run_row_id": _stable_id("run-row", run_id, status, ts),
            "run_id": run_id,
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "started_at": ts,
            "started_by_operation_id": operation_id,
            "initial_cash": _float(initial_cash),
            "base_currency": base_currency,
            "status": status,
            "ended_at": "",
            "ended_by_operation_id": "",
            "updated_at": ts,
            "timestamp": ts,
            "record_date": ts[:10],
        }
        return self._replace("strategy_runs", row)

    def record_run_state(
        self,
        *,
        mode: str,
        strategy_name: str,
        run_id: str,
        status: str,
        timestamp: Optional[Any] = None,
        operation_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not run_id:
            return None
        latest = self.latest_run(mode=mode, strategy_name=strategy_name, run_id=run_id) or {}
        ts = _timestamp_text(timestamp or datetime.now())
        row = dict(latest)
        row.update({
            "run_row_id": _stable_id("run-row", run_id, status, ts, operation_id),
            "run_id": run_id,
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "status": status,
            "updated_at": ts,
            "timestamp": ts,
            "record_date": ts[:10],
        })
        if status in {"stopped", "closed", "error"}:
            row["ended_at"] = ts
            row["ended_by_operation_id"] = operation_id
        return self._replace("strategy_runs", row)

    def record_control_state(
        self,
        *,
        mode: str,
        strategy_name: str,
        lifecycle_state: str,
        signal_enabled: bool,
        submit_enabled: bool,
        reconcile_enabled: bool = True,
        valuation_enabled: bool = True,
        current_run_id: str = "",
        last_operation_id: str = "",
        timestamp: Optional[Any] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = _timestamp_text(timestamp or datetime.now())
        row = {
            "control_state_id": _stable_id(
                "control",
                _mode(mode),
                strategy_name or "default",
                lifecycle_state,
                current_run_id,
                last_operation_id,
                ts,
            ),
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "current_run_id": current_run_id,
            "lifecycle_state": lifecycle_state,
            "signal_enabled": bool(signal_enabled),
            "submit_enabled": bool(submit_enabled),
            "reconcile_enabled": bool(reconcile_enabled),
            "valuation_enabled": bool(valuation_enabled),
            "last_operation_id": last_operation_id,
            "updated_at": ts,
            "timestamp": ts,
            "record_date": ts[:10],
        }
        if raw:
            row["raw_json"] = raw
        return self._replace("strategy_control_state", row)

    def record_capital_event(
        self,
        *,
        mode: str,
        strategy_name: str,
        run_id: str,
        event_type: str,
        amount: float,
        effective_date: Any,
        operation_id: str = "",
        timestamp: Optional[Any] = None,
    ) -> Dict[str, Any]:
        ts = _timestamp_text(timestamp or datetime.now())
        event_date = _date_text(effective_date) or ts[:10]
        row = {
            "capital_event_id": _stable_id("capital", _mode(mode), strategy_name or "default", run_id, event_type, event_date, amount),
            "run_id": run_id,
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "event_type": event_type,
            "amount": _float(amount),
            "effective_date": event_date,
            "operation_id": operation_id,
            "created_at": ts,
            "timestamp": ts,
            "record_date": event_date,
        }
        return self._replace("strategy_capital_events", row)

    def record_watermark(
        self,
        *,
        mode: str,
        strategy_name: str,
        run_id: str = "",
        latest_market_data_date: Optional[str] = None,
        latest_signal_date: Optional[str] = None,
        latest_submit_date: Optional[str] = None,
        latest_order_date: Optional[str] = None,
        latest_fill_date: Optional[str] = None,
        latest_nav_date: Optional[str] = None,
        latest_record_date: Optional[str] = None,
        status: str = "ok",
        timestamp: Optional[Any] = None,
    ) -> Dict[str, Any]:
        ts = _timestamp_text(timestamp or datetime.now())
        record_date = (
            latest_record_date
            or latest_nav_date
            or latest_fill_date
            or latest_order_date
            or latest_signal_date
            or latest_market_data_date
            or ts[:10]
        )
        row = {
            "watermark_id": _stable_id(
                "watermark",
                _mode(mode),
                strategy_name or "default",
                run_id,
                latest_market_data_date,
                latest_signal_date,
                latest_submit_date,
                latest_order_date,
                latest_fill_date,
                latest_nav_date,
                latest_record_date,
                status,
            ),
            "run_id": run_id,
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "latest_market_data_date": latest_market_data_date,
            "latest_signal_date": latest_signal_date,
            "latest_submit_date": latest_submit_date,
            "latest_order_date": latest_order_date,
            "latest_fill_date": latest_fill_date,
            "latest_nav_date": latest_nav_date,
            "latest_record_date": latest_record_date,
            "status": status,
            "updated_at": ts,
            "timestamp": ts,
            "record_date": str(record_date or "")[:10],
        }
        return self._replace("strategy_watermarks", row)

    def record_reconciliation(
        self,
        *,
        mode: str,
        strategy_name: str,
        run_id: str = "",
        reconciliation_type: str,
        status: str,
        started_at: Optional[Any] = None,
        completed_at: Optional[Any] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = _timestamp_text(completed_at or started_at or datetime.now())
        row = {
            "reconciliation_id": _stable_id("reconciliation", _mode(mode), strategy_name or "default", run_id, reconciliation_type, ts, status),
            "run_id": run_id,
            "strategy_name": strategy_name or "default",
            "mode": _mode(mode),
            "reconciliation_type": reconciliation_type,
            "status": status,
            "started_at": _timestamp_text(started_at or ts),
            "completed_at": ts,
            "payload_json": payload or {},
            "timestamp": ts,
            "record_date": ts[:10],
        }
        return self._replace("strategy_reconciliations", row)

    def upsert_record(
        self,
        kind: str,
        *,
        mode: str,
        strategy_name: str,
        record: Dict[str, Any],
        run_id: str = "",
    ) -> Dict[str, Any]:
        value = _kind(kind)
        if value == "operations":
            return self.record_operation(
                mode=mode,
                strategy_name=strategy_name,
                operation_type=str(record.get("operation_type") or record.get("action") or ""),
                requested_by=str(record.get("requested_by") or record.get("source") or "system"),
                requested_at=record.get("requested_at") or record.get("timestamp"),
                effective_date=record.get("effective_date") or record.get("record_date"),
                params=record.get("params_json") if isinstance(record.get("params_json"), dict) else record.get("payload"),
                status=str(record.get("status") or "applied"),
                applied_at=record.get("applied_at") or record.get("timestamp"),
                failure_reason=str(record.get("failure_reason") or ""),
                idempotency_key=str(record.get("idempotency_key") or ""),
                run_id=str(record.get("run_id") or run_id or ""),
                raw=record,
            )
        if value == "runs":
            return self._replace("strategy_runs", _run_row(mode=mode, strategy_name=strategy_name, record=record))
        if value == "control_state":
            return self.record_control_state(
                mode=mode,
                strategy_name=strategy_name,
                lifecycle_state=str(record.get("lifecycle_state") or record.get("live_state") or "stopped"),
                signal_enabled=bool(record.get("signal_enabled", record.get("live_enabled", False))),
                submit_enabled=bool(record.get("submit_enabled", record.get("live_enabled", False))),
                reconcile_enabled=bool(record.get("reconcile_enabled", True)),
                valuation_enabled=bool(record.get("valuation_enabled", True)),
                current_run_id=str(record.get("current_run_id") or record.get("run_id") or run_id or ""),
                last_operation_id=str(record.get("last_operation_id") or ""),
                timestamp=record.get("updated_at") or record.get("timestamp"),
                raw=record,
            )
        if value == "capital_events":
            return self.record_capital_event(
                mode=mode,
                strategy_name=strategy_name,
                run_id=str(record.get("run_id") or run_id or ""),
                event_type=str(record.get("event_type") or ""),
                amount=_float(record.get("amount")),
                effective_date=record.get("effective_date") or record.get("record_date") or record.get("timestamp"),
                operation_id=str(record.get("operation_id") or ""),
                timestamp=record.get("timestamp") or record.get("created_at"),
            )
        if value == "watermarks":
            return self.record_watermark(
                mode=mode,
                strategy_name=strategy_name,
                run_id=str(record.get("run_id") or run_id or ""),
                latest_market_data_date=record.get("latest_market_data_date"),
                latest_signal_date=record.get("latest_signal_date"),
                latest_submit_date=record.get("latest_submit_date"),
                latest_order_date=record.get("latest_order_date"),
                latest_fill_date=record.get("latest_fill_date"),
                latest_nav_date=record.get("latest_nav_date"),
                latest_record_date=record.get("latest_record_date"),
                status=str(record.get("status") or "ok"),
                timestamp=record.get("timestamp") or record.get("updated_at"),
            )
        if value == "reconciliations":
            return self.record_reconciliation(
                mode=mode,
                strategy_name=strategy_name,
                run_id=str(record.get("run_id") or run_id or ""),
                reconciliation_type=str(record.get("reconciliation_type") or ""),
                status=str(record.get("status") or "ok"),
                started_at=record.get("started_at") or record.get("timestamp"),
                completed_at=record.get("completed_at") or record.get("timestamp"),
                payload=record.get("payload_json") or record.get("payload"),
            )
        table = KIND_TABLES[value]
        return self._replace(table, _fact_row(value, mode=mode, strategy_name=strategy_name, record=record, run_id=run_id or self.active_run_id(mode=mode, strategy_name=strategy_name)))

    def migrate_records(
        self,
        *,
        mode: str,
        strategy_name: str,
        records: Dict[str, Iterable[Dict[str, Any]]],
        run_id: str = "",
    ) -> None:
        self.ensure_schema()
        for kind in STATE_KIND_ORDER:
            for record in records.get(kind, []):
                self.upsert_record(kind, mode=mode, strategy_name=strategy_name, record=dict(record), run_id=run_id)

    def read_records(self, *, mode: str, strategy_name: str) -> Dict[str, List[Dict[str, Any]]]:
        return {
            kind: self.read(kind, mode=mode, strategy_name=strategy_name)
            for kind in STATE_KIND_ORDER
        }

    def read(self, kind: str, *, mode: str, strategy_name: str) -> List[Dict[str, Any]]:
        if not self.db_path.exists():
            return []
        table = KIND_TABLES[_kind(kind)]
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                f"""
                select raw_json
                from {table}
                where mode = ? and strategy_name = ?
                order by coalesce(timestamp, record_date, '')
                """,
                [_mode(mode), strategy_name or "default"],
            ).fetchall()
        finally:
            con.close()
        return [_loads(row[0]) for row in rows if row and row[0]]

    def strategy_names(self, mode: Optional[str] = None) -> List[str]:
        if not self.db_path.exists():
            return []
        modes = [_mode(mode)] if mode else sorted(VALID_STATE_MODES)
        names = set()
        con = self._connect(read_only=True)
        try:
            for table in KIND_TABLES.values():
                placeholders = ",".join("?" for _ in modes)
                rows = con.execute(
                    f"select distinct strategy_name from {table} where mode in ({placeholders})",
                    modes,
                ).fetchall()
                names.update(str(row[0]) for row in rows if row and row[0])
        finally:
            con.close()
        return sorted(names)

    def latest_record_date(self, mode: str) -> Optional[str]:
        if not self.db_path.exists():
            return None
        latest: Optional[str] = None
        con = self._connect(read_only=True)
        try:
            for kind in FACT_KINDS:
                table = KIND_TABLES[kind]
                row = con.execute(
                    f"select max(record_date) from {table} where mode = ?",
                    [_mode(mode)],
                ).fetchone()
                value = str((row or [None])[0] or "")[:10]
                if value and (latest is None or value > latest):
                    latest = value
        finally:
            con.close()
        return latest

    def active_run_id(self, *, mode: str, strategy_name: str) -> str:
        run = self.active_run(mode=mode, strategy_name=strategy_name)
        return str((run or {}).get("run_id") or "")

    def active_run(self, *, mode: str, strategy_name: str) -> Optional[Dict[str, Any]]:
        latest = self.latest_run(mode=mode, strategy_name=strategy_name)
        if not latest:
            return None
        status = str(latest.get("status") or "").lower()
        if status in {"active", "running", "paused"} and not latest.get("ended_at"):
            return latest
        return None

    def latest_run(
        self,
        *,
        mode: str,
        strategy_name: str,
        run_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self.db_path.exists():
            return None
        params: List[Any] = [_mode(mode), strategy_name or "default"]
        run_clause = ""
        if run_id:
            run_clause = " and run_id = ?"
            params.append(run_id)
        con = self._connect(read_only=True)
        try:
            rows = con.execute(
                f"""
                select raw_json
                from strategy_runs
                where mode = ? and strategy_name = ?{run_clause}
                order by coalesce(updated_at, timestamp, started_at, '')
                """,
                params,
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return None
        latest_by_id: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            item = _loads(row[0])
            item_id = str(item.get("run_id") or "")
            if item_id:
                latest_by_id[item_id] = item
        values = list(latest_by_id.values()) or [_loads(row[0]) for row in rows]
        return sorted(values, key=lambda item: str(item.get("updated_at") or item.get("timestamp") or item.get("started_at") or ""))[-1]

    def latest(self, kind: str, *, mode: str, strategy_name: str) -> Optional[Dict[str, Any]]:
        rows = self.read(kind, mode=mode, strategy_name=strategy_name)
        return rows[-1] if rows else None

    def _replace(self, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_schema()
        payload = _jsonable(dict(row))
        source_json = payload.get("raw_json")
        payload.pop("raw_json", None)
        key = TABLE_KEYS[table]
        if not payload.get(key):
            payload[key] = _stable_id(key, table, payload)
        payload.setdefault("timestamp", payload.get("updated_at") or payload.get("created_at") or datetime.now().isoformat())
        payload.setdefault("record_date", _record_date(payload) or str(payload.get("timestamp") or "")[:10])
        raw_payload = dict(payload)
        if source_json:
            raw_payload["source_json"] = source_json
        payload["raw_json"] = json.dumps(_jsonable(raw_payload), ensure_ascii=False, sort_keys=True)
        columns = _table_columns(table)
        values = [_column_value(payload, column) for column in columns]
        placeholders = ",".join("?" for _ in columns)
        with self._lock:
            con = self._connect()
            try:
                con.execute(f"delete from {table} where {key} = ?", [payload[key]])
                con.execute(
                    f"insert into {table} ({','.join(columns)}) values ({placeholders})",
                    values,
                )
            finally:
                con.close()
        return _loads(payload["raw_json"])

    def _connect(self, *, read_only: bool = False):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if read_only and not self.db_path.exists():
            read_only = False
        return duckdb.connect(str(self.db_path), read_only=read_only)


def _schema_statements() -> List[str]:
    return [
        """
        create table if not exists strategy_operations (
            operation_id varchar primary key,
            strategy_name varchar,
            mode varchar,
            run_id varchar,
            operation_type varchar,
            requested_by varchar,
            requested_at varchar,
            effective_date varchar,
            params_json varchar,
            status varchar,
            applied_at varchar,
            failure_reason varchar,
            idempotency_key varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_runs (
            run_row_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            started_at varchar,
            started_by_operation_id varchar,
            initial_cash double,
            base_currency varchar,
            status varchar,
            ended_at varchar,
            ended_by_operation_id varchar,
            updated_at varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_control_state (
            control_state_id varchar primary key,
            strategy_name varchar,
            mode varchar,
            current_run_id varchar,
            lifecycle_state varchar,
            signal_enabled boolean,
            submit_enabled boolean,
            reconcile_enabled boolean,
            valuation_enabled boolean,
            last_operation_id varchar,
            updated_at varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_capital_events (
            capital_event_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            event_type varchar,
            amount double,
            effective_date varchar,
            operation_id varchar,
            created_at varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_signals (
            signal_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            signal_date varchar,
            submit_date varchar,
            timestamp varchar,
            order_id varchar,
            symbol varchar,
            side varchar,
            quantity double,
            order_type varchar,
            reference_price double,
            execution_cost_bps double,
            status varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_submit_attempts (
            attempt_id varchar primary key,
            signal_id varchar,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            execution_date varchar,
            timestamp varchar,
            order_id varchar,
            broker_order_id varchar,
            symbol varchar,
            side varchar,
            quantity double,
            limit_price double,
            status varchar,
            failure_reason varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_orders (
            order_row_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            attempt_id varchar,
            signal_id varchar,
            order_id varchar,
            broker_order_id varchar,
            symbol varchar,
            side varchar,
            quantity double,
            order_type varchar,
            price double,
            broker_status varchar,
            normalized_status varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_fills (
            fill_row_id varchar primary key,
            fill_id varchar,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            order_id varchar,
            broker_order_id varchar,
            symbol varchar,
            side varchar,
            quantity double,
            price double,
            commission double,
            value double,
            fill_time varchar,
            trade_date varchar,
            source varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_positions (
            position_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            symbol varchar,
            as_of_date varchar,
            quantity double,
            avg_cost double,
            realized_pnl double,
            updated_from_fill_id varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_nav_snapshots (
            snapshot_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            nav_date varchar,
            market_data_date varchar,
            source varchar,
            nav double,
            cash double,
            market_value double,
            realized_pnl double,
            unrealized_pnl double,
            total_pnl double,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_watermarks (
            watermark_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            latest_market_data_date varchar,
            latest_signal_date varchar,
            latest_submit_date varchar,
            latest_order_date varchar,
            latest_fill_date varchar,
            latest_nav_date varchar,
            latest_record_date varchar,
            status varchar,
            updated_at varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
        """
        create table if not exists strategy_reconciliations (
            reconciliation_id varchar primary key,
            run_id varchar,
            strategy_name varchar,
            mode varchar,
            reconciliation_type varchar,
            status varchar,
            started_at varchar,
            completed_at varchar,
            payload_json varchar,
            timestamp varchar,
            record_date varchar,
            raw_json varchar
        )
        """,
    ]


def _table_columns(table: str) -> List[str]:
    first = _schema_statements()[list(KIND_TABLES.values()).index(table)] if table in KIND_TABLES.values() else ""
    if not first:
        raise ValueError(f"Unsupported table: {table}")
    body = first.split("(", 1)[1].rsplit(")", 1)[0]
    columns = []
    for line in body.splitlines():
        text = line.strip().rstrip(",")
        if not text or text.lower().startswith(("primary", "unique", "foreign")):
            continue
        columns.append(text.split()[0])
    return columns


def _run_row(*, mode: str, strategy_name: str, record: Dict[str, Any]) -> Dict[str, Any]:
    ts = _timestamp_text(record.get("updated_at") or record.get("timestamp") or record.get("started_at") or datetime.now())
    run_id = str(record.get("run_id") or _stable_id("run", _mode(mode), strategy_name or "default", record.get("started_at") or ts))
    row = {
        "run_row_id": str(record.get("run_row_id") or _stable_id("run-row", run_id, record.get("status"), ts)),
        "run_id": run_id,
        "strategy_name": strategy_name or "default",
        "mode": _mode(mode),
        "started_at": _timestamp_text(record.get("started_at") or ts),
        "started_by_operation_id": str(record.get("started_by_operation_id") or ""),
        "initial_cash": _float(record.get("initial_cash")),
        "base_currency": str(record.get("base_currency") or "CNY"),
        "status": str(record.get("status") or "active"),
        "ended_at": _timestamp_text(record.get("ended_at")) if record.get("ended_at") else "",
        "ended_by_operation_id": str(record.get("ended_by_operation_id") or ""),
        "updated_at": ts,
        "timestamp": ts,
        "record_date": str(record.get("record_date") or ts[:10])[:10],
        "raw_json": record,
    }
    return row


def _fact_row(kind: str, *, mode: str, strategy_name: str, record: Dict[str, Any], run_id: str = "") -> Dict[str, Any]:
    item = _jsonable(dict(record))
    item["strategy_name"] = strategy_name or item.get("strategy_name") or "default"
    item["mode"] = _mode(mode)
    item["run_id"] = item.get("run_id") or run_id or ""
    timestamp = _timestamp_text(item.get("timestamp") or item.get("date") or datetime.now())
    item["timestamp"] = timestamp
    item["record_date"] = str(item.get("record_date") or _record_date(item) or timestamp[:10])[:10]
    if kind == "signals":
        item.setdefault("signal_id", _stable_id("signal", item.get("run_id"), item.get("order_id"), item.get("timestamp"), item.get("symbol"), item.get("side"), item.get("quantity")))
        item.setdefault("signal_date", item.get("record_date"))
        return _with_raw(item)
    if kind == "submit_attempts":
        item.setdefault("attempt_id", _stable_id("attempt", item.get("run_id"), item.get("signal_id"), item.get("order_id"), item.get("timestamp"), item.get("status")))
        item.setdefault("execution_date", item.get("submit_date") or item.get("record_date"))
        item.setdefault("limit_price", item.get("price"))
        return _with_raw(item)
    if kind == "orders":
        item.setdefault("order_row_id", _stable_id("order-row", item.get("run_id"), item.get("order_id"), item.get("broker_order_id"), item.get("timestamp"), item.get("status")))
        item.setdefault("broker_status", item.get("broker_status") or item.get("status"))
        item.setdefault("normalized_status", item.get("normalized_status") or item.get("display_status") or item.get("status"))
        return _with_raw(item)
    if kind == "fills":
        item.setdefault("fill_row_id", _stable_id("fill-row", item.get("run_id"), item.get("fill_id"), item.get("order_id"), item.get("timestamp"), item.get("symbol"), item.get("quantity"), item.get("price")))
        item.setdefault("fill_time", item.get("timestamp"))
        item.setdefault("trade_date", item.get("record_date"))
        item.setdefault("value", _float(item.get("quantity")) * _float(item.get("price")))
        return _with_raw(item)
    if kind == "positions":
        item.setdefault("position_id", _stable_id("position", item.get("run_id"), item.get("symbol"), item.get("as_of_date") or item.get("record_date")))
        item.setdefault("as_of_date", item.get("record_date"))
        item.setdefault("quantity", item.get("qty"))
        return _with_raw(item)
    if kind == "snapshots":
        item.setdefault("snapshot_id", _stable_id("snapshot", item.get("run_id"), item.get("date") or item.get("record_date"), item.get("source"), item.get("nav")))
        item.setdefault("nav_date", item.get("date") or item.get("record_date"))
        item.setdefault("market_data_date", item.get("market_data_date") or item.get("date") or item.get("record_date"))
        return _with_raw(item)
    raise ValueError(f"Unsupported fact kind: {kind}")


def _with_raw(item: Dict[str, Any]) -> Dict[str, Any]:
    item["raw_json"] = dict(item)
    return item


def _column_value(row: Dict[str, Any], column: str) -> Any:
    value = row.get(column)
    if isinstance(value, (dict, list)):
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return bool(value)
    if column in {
        "initial_cash",
        "amount",
        "quantity",
        "reference_price",
        "execution_cost_bps",
        "limit_price",
        "price",
        "commission",
        "value",
        "avg_cost",
        "realized_pnl",
        "nav",
        "cash",
        "market_value",
        "unrealized_pnl",
        "total_pnl",
    }:
        return _float(value)
    if value is None:
        return None
    return value


def _kind(kind: str) -> str:
    value = str(kind or "").lower()
    if value not in KIND_TABLES:
        raise ValueError(f"Unsupported strategy state kind: {kind}")
    return value


def _mode(mode: Optional[str]) -> str:
    value = str(mode or "live").lower()
    if value not in VALID_STATE_MODES:
        raise ValueError("mode must be live or paper")
    return value


def _record_date(record: Dict[str, Any]) -> Optional[str]:
    for key in ("record_date", "date", "timestamp"):
        value = record.get(key)
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value or "")
        if len(text) >= 10:
            return text[:10]
    return None


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    text = str(value or "").strip()
    return text if text else datetime.now().isoformat()


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps([_jsonable(part) for part in parts], ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}:{hashlib.sha1(payload.encode('utf-8')).hexdigest()}"


def _loads(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return json.loads(str(value))


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value
