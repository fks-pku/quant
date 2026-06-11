"""DuckDB-backed live strategy control state — uses strategy_states table."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


DEFAULT_CONTROL_FILE = str(Path(__file__).resolve().parents[1] / "var" / "strategy_dashboard.duckdb")
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


def _resolve_db_path(path: Optional[Any]) -> Path:
    if path is None:
        return Path(DEFAULT_CONTROL_FILE)
    p = Path(str(path))
    if p.suffix in (".json",):
        p = p.parent / (p.stem + ".duckdb")
    if p.is_dir():
        p = p / "strategy_dashboard.duckdb"
    if p.suffix != ".duckdb":
        p = p.parent / (p.stem + ".duckdb")
    return p


def _store(path: Optional[Any]) -> StrategyStateStore:
    return StrategyStateStore(_resolve_db_path(path))


def get_strategy_control(
    strategy_name: str,
    path: Optional[Any] = None,
    *,
    default_live_enabled: bool = False,
    mode: str = "live",
) -> StrategyControl:
    control_mode = _control_mode(mode)
    store = _store(path)
    row = store.get_current_state(strategy_name=strategy_name, mode=control_mode)
    if row is None:
        live_enabled = default_live_enabled
        state = "running" if default_live_enabled else "stopped"
        updated_at = ""
        liquidation = False
    else:
        live_enabled = row.get("signal_enabled", False)
        state = str(row.get("to_state", "stopped"))
        if state not in VALID_LIVE_STATES:
            state = "stopped"
        updated_at = str(row.get("recorded_at", ""))
        liquidation = bool(row.get("liquidation_requested", False))
    return StrategyControl(
        strategy_name=strategy_name,
        mode=control_mode,
        live_enabled=live_enabled,
        live_state=state,
        liquidation_requested=liquidation,
        updated_at=updated_at,
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
    store = _store(path)
    current = store.get_current_state(strategy_name=strategy_name, mode=control_mode)
    current_state = str((current or {}).get("to_state", "stopped"))
    current_cash = float((current or {}).get("initial_cash", 0.0))
    if initial_cash is not None and initial_cash > 0:
        current_cash = initial_cash
    timestamp = (now or datetime.now()).isoformat()

    if current_state == "liquidating" and action not in {"liquidate_stop", "stop"}:
        raise ValueError(
            f"Cannot {action} while liquidating. "
            f"Wait for liquidation to complete or use 'stop' to force-stop."
        )

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
        current_enabled = (current or {}).get("signal_enabled", default_live_enabled)
        updated = StrategyControl(
            strategy_name=strategy_name,
            mode=control_mode,
            live_enabled=current_enabled,
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

    store.record_state(
        strategy_name=strategy_name,
        mode=control_mode,
        from_state=current_state,
        to_state=updated.live_state,
        signal_enabled=updated.live_enabled,
        submit_enabled=updated.accepts_live_signals,
        liquidation_requested=updated.liquidation_requested,
        initial_cash=current_cash,
        note=note,
        recorded_at=timestamp,
    )
    return updated


def _control_mode(mode: str) -> str:
    value = str(mode or "live").lower()
    if value not in VALID_CONTROL_MODES:
        raise ValueError("mode must be live or paper")
    return value
