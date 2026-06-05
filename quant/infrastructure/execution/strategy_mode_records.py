"""Mode-scoped append-only records for one strategy."""

import hashlib
import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


DEFAULT_STRATEGY_MODE_RECORD_DIR = Path(__file__).resolve().parents[1] / "var" / "strategy_modes"
VALID_MODE_RECORD_KINDS = {"operations", "signals", "orders", "fills", "snapshots"}
VALID_MODE_RECORD_MODES = {"live", "paper"}


class StrategyModeRecordStore:
    def __init__(self, base_dir: Optional[Any] = None):
        self.base_dir = Path(base_dir) if base_dir is not None else DEFAULT_STRATEGY_MODE_RECORD_DIR
        self._lock = threading.RLock()

    def append(
        self,
        kind: str,
        *,
        mode: str,
        strategy_name: str,
        record: Dict[str, Any],
        unique: bool = False,
    ) -> Dict[str, Any]:
        normalized = self._normalize_record(kind, mode=mode, strategy_name=strategy_name, record=record)
        path = self.path(kind, mode=mode, strategy_name=strategy_name)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if unique and self._record_key(normalized) in self._existing_keys(path):
                return normalized
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")
        return normalized

    def append_operation(
        self,
        *,
        mode: str,
        strategy_name: str,
        action: str,
        timestamp: Optional[Any] = None,
        source: str = "system",
        note: str = "",
        payload: Optional[Dict[str, Any]] = None,
        unique: bool = True,
    ) -> Dict[str, Any]:
        ts = _timestamp_text(timestamp or datetime.now())
        return self.append(
            "operations",
            mode=mode,
            strategy_name=strategy_name,
            unique=unique,
            record={
                "timestamp": ts,
                "record_date": ts[:10],
                "action": action,
                "source": source,
                "note": note,
                "payload": payload or {},
            },
        )

    def read(self, kind: str, *, mode: str, strategy_name: str) -> List[Dict[str, Any]]:
        path = self.path(kind, mode=mode, strategy_name=strategy_name)
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
        return sorted(rows, key=lambda item: str(item.get("timestamp") or item.get("date") or ""))

    def read_records(self, *, mode: str, strategy_name: str) -> Dict[str, List[Dict[str, Any]]]:
        return {
            kind: self.read(kind, mode=mode, strategy_name=strategy_name)
            for kind in ("operations", "signals", "orders", "fills", "snapshots")
        }

    def strategy_names(self, mode: Optional[str] = None) -> List[str]:
        modes = [_mode(mode)] if mode else sorted(VALID_MODE_RECORD_MODES)
        names = set()
        for item_mode in modes:
            mode_dir = self.base_dir / item_mode
            if not mode_dir.exists():
                continue
            for item in mode_dir.iterdir():
                if item.is_dir():
                    names.add(item.name)
        return sorted(names)

    def latest_record_date(self, mode: str) -> Optional[str]:
        mode_dir = self.base_dir / _mode(mode)
        if not mode_dir.exists():
            return None
        latest: Optional[str] = None
        for strategy_dir in mode_dir.iterdir():
            if not strategy_dir.is_dir():
                continue
            for path in strategy_dir.glob("*.jsonl"):
                if path.stem == "operations":
                    continue
                for row in _read_jsonl(path):
                    row_date = _record_date(row)
                    if row_date and (latest is None or row_date > latest):
                        latest = row_date
        return latest

    def path(self, kind: str, *, mode: str, strategy_name: str) -> Path:
        return self.base_dir / _mode(mode) / _safe_strategy_name(strategy_name) / f"{_kind(kind)}.jsonl"

    def _normalize_record(
        self,
        kind: str,
        *,
        mode: str,
        strategy_name: str,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized = _jsonable(dict(record))
        normalized["mode"] = _mode(mode)
        normalized["strategy_name"] = strategy_name or "default"
        normalized["record_kind"] = _kind(kind)
        if not normalized.get("timestamp"):
            normalized["timestamp"] = normalized.get("date") or datetime.now().isoformat()
        normalized["record_date"] = str(normalized.get("record_date") or _record_date(normalized) or "")[:10]
        normalized["_record_key"] = self._make_record_key(_kind(kind), normalized)
        return normalized

    def _existing_keys(self, path: Path) -> Set[str]:
        return {
            str(row.get("_record_key") or self._make_record_key(str(row.get("record_kind") or path.stem), row))
            for row in _read_jsonl(path)
        }

    def _record_key(self, record: Dict[str, Any]) -> str:
        return str(record.get("_record_key") or self._make_record_key(str(record.get("record_kind") or ""), record))

    def _make_record_key(self, kind: str, record: Dict[str, Any]) -> str:
        parts = {
            key: record.get(key)
            for key in (
                "mode",
                "strategy_name",
                "record_kind",
                "timestamp",
                "record_date",
                "action",
                "source",
                "order_id",
                "broker_order_id",
                "fill_id",
                "symbol",
                "side",
                "quantity",
                "price",
                "status",
                "date",
            )
        }
        payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return f"{kind}:{hashlib.sha1(payload.encode('utf-8')).hexdigest()}"


def append_control_operation(
    control_file: Any,
    *,
    strategy_name: str,
    mode: str,
    action: str,
    control: Dict[str, Any],
    timestamp: Optional[Any] = None,
    note: str = "",
) -> Dict[str, Any]:
    base = Path(control_file).parent / "strategy_modes"
    ts = timestamp or control.get("updated_at") or datetime.now()
    return StrategyModeRecordStore(base).append_operation(
        mode=mode,
        strategy_name=strategy_name,
        action=action,
        timestamp=ts,
        source="dashboard",
        note=note,
        payload={"control": control},
        unique=True,
    )


def materialize_daily_records(
    store: StrategyModeRecordStore,
    *,
    mode: str,
    strategy_name: str,
    records: Dict[str, Iterable[Dict[str, Any]]],
) -> None:
    for kind in ("signals", "orders", "fills", "snapshots"):
        for record in records.get(kind, []):
            store.append(kind, mode=mode, strategy_name=strategy_name, record=record, unique=True)
            if kind == "snapshots":
                store.append_operation(
                    mode=mode,
                    strategy_name=strategy_name,
                    action="daily_snapshot",
                    timestamp=record.get("timestamp") or record.get("date"),
                    source="recorder",
                    payload={"snapshot": record},
                    unique=True,
                )


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _mode(mode: Optional[str]) -> str:
    value = str(mode or "live").lower()
    if value not in VALID_MODE_RECORD_MODES:
        raise ValueError("mode must be live or paper")
    return value


def _kind(kind: str) -> str:
    value = str(kind or "").lower()
    if value not in VALID_MODE_RECORD_KINDS:
        raise ValueError(f"Unsupported strategy mode record kind: {kind}")
    return value


def _safe_strategy_name(strategy_name: str) -> str:
    value = str(strategy_name or "default")
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)


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
    text = str(value or "")
    return text if text else datetime.now().isoformat()


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
