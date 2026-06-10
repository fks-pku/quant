"""File-backed live strategy control state."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from quant.infrastructure.execution.strategy_mode_records import append_control_operation
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
from quant.infrastructure.execution.strategy_ledger import append_strategy_audit


DEFAULT_CONTROL_FILE = Path(__file__).resolve().parents[1] / "var" / "strategy_controls.json"
VALID_LIVE_STATES = {"running", "paused", "stopped", "liquidating"}
VALID_ACTIONS = {"start", "pause", "resume", "liquidate_stop", "stop"}
VALID_CONTROL_MODES = {"live", "paper"}


@dataclass(frozen=True)
class StrategyControl:
    strategy_name: str
    mode: str = "live"
    live_enabled: bool = False
    live_state: str = "stopped"
    liquidation_requested: bool = False
    updated_at: str = ""
    note: str = ""

    @property
    def accepts_live_signals(self) -> bool:
        return (
            self.live_enabled
            and self.live_state == "running"
            and not self.liquidation_requested
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_strategy_controls(path: Optional[Any] = None) -> Dict[str, Any]:
    control_path = _control_path(path)
    if not control_path.exists():
        return {"strategies": {}}
    with control_path.open("r", encoding="utf-8") as f:
        data = json.load(f) or {}
    strategies = data.get("strategies", {})
    if not isinstance(strategies, dict):
        strategies = {}
    data["strategies"] = strategies
    return data


def get_strategy_control(
    strategy_name: str,
    path: Optional[Any] = None,
    *,
    default_live_enabled: bool = False,
    mode: str = "live",
) -> StrategyControl:
    data = load_strategy_controls(path)
    control_mode = _control_mode(mode)
    raw = data.get(_strategy_bucket_key(control_mode), {}).get(strategy_name, {})
    if not isinstance(raw, dict):
        raw = {}
    live_enabled = bool(raw.get("live_enabled", default_live_enabled))
    state = str(raw.get("live_state") or ("running" if live_enabled else "stopped"))
    if state not in VALID_LIVE_STATES:
        state = "stopped"
    return StrategyControl(
        strategy_name=strategy_name,
        mode=control_mode,
        live_enabled=live_enabled,
        live_state=state,
        liquidation_requested=bool(raw.get("liquidation_requested", False)),
        updated_at=str(raw.get("updated_at", "")),
        note=str(raw.get("note", "")),
    )


def apply_strategy_control_action(
    strategy_name: str,
    action: str,
    path: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
    note: str = "",
    default_live_enabled: bool = False,
    mode: str = "live",
    initial_cash: Optional[float] = None,
) -> StrategyControl:
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unsupported strategy control action: {action}")
    control_mode = _control_mode(mode)

    current = get_strategy_control(
        strategy_name,
        path,
        default_live_enabled=default_live_enabled,
        mode=control_mode,
    )
    timestamp = (now or datetime.now()).isoformat()

    if action in {"start", "resume"}:
        updated = StrategyControl(
            strategy_name=strategy_name,
            mode=control_mode,
            live_enabled=True,
            live_state="running",
            liquidation_requested=False,
            updated_at=timestamp,
            note=note,
        )
    elif action == "pause":
        updated = StrategyControl(
            strategy_name=strategy_name,
            mode=control_mode,
            live_enabled=current.live_enabled or default_live_enabled,
            live_state="paused",
            liquidation_requested=False,
            updated_at=timestamp,
            note=note,
        )
    elif action == "liquidate_stop":
        updated = StrategyControl(
            strategy_name=strategy_name,
            mode=control_mode,
            live_enabled=False,
            live_state="liquidating",
            liquidation_requested=True,
            updated_at=timestamp,
            note=note,
        )
    else:
        updated = StrategyControl(
            strategy_name=strategy_name,
            mode=control_mode,
            live_enabled=False,
            live_state="stopped",
            liquidation_requested=False,
            updated_at=timestamp,
            note=note,
        )

    data = load_strategy_controls(path)
    strategies = data.setdefault(_strategy_bucket_key(control_mode), {})
    strategies[strategy_name] = updated.to_dict()
    _write_strategy_controls(data, path)
    append_strategy_audit(
        _control_path(path).parent / "strategy_audit.jsonl",
        strategy_name=strategy_name,
        mode=control_mode,
        action=action,
        source="dashboard",
        note=note,
        payload={
            "previous": current.to_dict(),
            "updated": updated.to_dict(),
        },
        timestamp=now,
    )
    operation = append_control_operation(
        _control_path(path),
        strategy_name=strategy_name,
        mode=control_mode,
        action=action,
        control=updated.to_dict(),
        timestamp=timestamp,
        note=note,
    )
    state_store = StrategyStateStore(_control_path(path).parent / "strategy_state.duckdb")
    state_operation = state_store.record_operation(
        mode=control_mode,
        strategy_name=strategy_name,
        operation_type=action,
        requested_by="dashboard",
        requested_at=timestamp,
        effective_date=timestamp[:10],
        params={"control": updated.to_dict(), "note": note},
        status="applied",
        applied_at=timestamp,
        idempotency_key=str(operation.get("_record_key") or ""),
    )
    run = state_store.active_run(mode=control_mode, strategy_name=strategy_name)
    if action == "start":
        run = state_store.ensure_run(
            mode=control_mode,
            strategy_name=strategy_name,
            initial_cash=float(initial_cash or 0.0),
            started_at=timestamp,
            operation_id=state_operation["operation_id"],
        )
    elif action == "resume" and run:
        run = state_store.record_run_state(
            mode=control_mode,
            strategy_name=strategy_name,
            run_id=str(run.get("run_id") or ""),
            status="active",
            timestamp=timestamp,
            operation_id=state_operation["operation_id"],
        )
    elif action == "pause" and run:
        run = state_store.record_run_state(
            mode=control_mode,
            strategy_name=strategy_name,
            run_id=str(run.get("run_id") or ""),
            status="paused",
            timestamp=timestamp,
            operation_id=state_operation["operation_id"],
        )
    elif action in {"stop", "liquidate_stop"} and run:
        run = state_store.record_run_state(
            mode=control_mode,
            strategy_name=strategy_name,
            run_id=str(run.get("run_id") or ""),
            status="stopped" if action == "stop" else "active",
            timestamp=timestamp,
            operation_id=state_operation["operation_id"],
        )
    run_id = str((run or {}).get("run_id") or "")
    state_store.record_control_state(
        mode=control_mode,
        strategy_name=strategy_name,
        lifecycle_state=updated.live_state,
        signal_enabled=updated.accepts_live_signals,
        submit_enabled=updated.accepts_live_signals,
        reconcile_enabled=True,
        valuation_enabled=True,
        current_run_id=run_id,
        last_operation_id=state_operation["operation_id"],
        timestamp=timestamp,
        raw=updated.to_dict(),
    )
    return updated


def _control_mode(mode: str) -> str:
    value = str(mode or "live").lower()
    if value not in VALID_CONTROL_MODES:
        raise ValueError("mode must be live or paper")
    return value


def _strategy_bucket_key(mode: str) -> str:
    return "strategies" if mode == "live" else "paper_strategies"


def _write_strategy_controls(data: Dict[str, Any], path: Optional[Any] = None) -> None:
    control_path = _control_path(path)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = control_path.with_name(f"{control_path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(control_path)


def _control_path(path: Optional[Any]) -> Path:
    if path is None:
        return DEFAULT_CONTROL_FILE
    return Path(path)
