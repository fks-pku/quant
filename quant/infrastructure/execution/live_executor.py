"""Cost-bounded live target-order execution."""

import threading
from dataclasses import dataclass, replace
from datetime import datetime, time
from typing import Any, Callable, Dict, Optional, Union


@dataclass(frozen=True)
class TargetOrder:
    symbol: str
    quantity: float
    side: str
    reference_price: float
    strategy_name: Optional[str] = None
    max_cost_bps: Optional[float] = None
    deadline: Optional[datetime] = None


@dataclass
class TargetState:
    target: TargetOrder
    order_id: Optional[str]
    limit_price: float
    status: str
    submitted_at: datetime


class LiveExecutionManager:
    def __init__(
        self,
        order_manager: Any,
        default_max_cost_bps: float = 30.0,
        default_deadline: Optional[Union[str, time]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.order_manager = order_manager
        self.default_max_cost_bps = float(default_max_cost_bps)
        self.default_deadline = self._parse_deadline(default_deadline)
        self._clock = clock or datetime.now
        self._targets: Dict[str, TargetState] = {}
        self._lock = threading.RLock()

    def submit_target_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        reference_price: float,
        strategy_name: Optional[str] = None,
    ) -> Optional[str]:
        return self.submit_target(TargetOrder(
            symbol=symbol,
            quantity=quantity,
            side=side,
            reference_price=reference_price,
            strategy_name=strategy_name,
        ))

    def submit_target(self, target: TargetOrder) -> Optional[str]:
        target = self._with_default_deadline(target)
        self._validate_target(target)
        limit_price = self._cost_limit_price(target)
        order_id = self.order_manager.submit_order(
            target.symbol,
            target.quantity,
            target.side.upper(),
            "LIMIT",
            limit_price,
            target.strategy_name,
        )
        if order_id is not None:
            with self._lock:
                self._targets[order_id] = TargetState(
                    target=target,
                    order_id=order_id,
                    limit_price=limit_price,
                    status="submitted",
                    submitted_at=self._clock(),
                )
        return order_id

    def drop_expired_targets(self, now: Optional[datetime] = None) -> list:
        current_time = now or self._clock()
        dropped = []
        with self._lock:
            states = list(self._targets.values())
        for state in states:
            order_status = self._order_status(state.order_id)
            if order_status in ("filled", "cancelled", "rejected"):
                with self._lock:
                    state.status = order_status
                continue
            if state.status not in ("submitted", "partial"):
                continue
            if state.target.deadline is None or current_time < state.target.deadline:
                continue
            if state.order_id and self.order_manager.cancel_order(state.order_id):
                with self._lock:
                    state.status = "dropped"
                dropped.append(state.order_id)
        return dropped

    def get_target_state(self, order_id: str) -> Optional[TargetState]:
        with self._lock:
            return self._targets.get(order_id)

    def _cost_limit_price(self, target: TargetOrder) -> float:
        bps = self.default_max_cost_bps if target.max_cost_bps is None else float(target.max_cost_bps)
        side = target.side.upper()
        if side == "BUY":
            return target.reference_price * (1 + bps / 10000.0)
        if side == "SELL":
            return target.reference_price * (1 - bps / 10000.0)
        raise ValueError(f"Unsupported order side: {target.side}")

    def _validate_target(self, target: TargetOrder) -> None:
        if target.quantity <= 0:
            raise ValueError("Target quantity must be positive")
        if target.reference_price <= 0:
            raise ValueError("Target reference_price must be positive")
        if target.side.upper() not in ("BUY", "SELL"):
            raise ValueError(f"Unsupported order side: {target.side}")

    def _with_default_deadline(self, target: TargetOrder) -> TargetOrder:
        if target.deadline is not None or self.default_deadline is None:
            return target
        current_time = self._clock()
        deadline = datetime.combine(current_time.date(), self.default_deadline)
        return replace(target, deadline=deadline)

    def _parse_deadline(self, deadline: Optional[Union[str, time]]) -> Optional[time]:
        if deadline is None or isinstance(deadline, time):
            return deadline
        hour, minute = str(deadline).split(":", 1)
        return time(hour=int(hour), minute=int(minute))

    def _order_status(self, order_id: Optional[str]) -> Optional[str]:
        if order_id is None or not hasattr(self.order_manager, "get_order_status"):
            return None
        status = self.order_manager.get_order_status(order_id)
        if status is None:
            return None
        if hasattr(status, "value"):
            return str(status.value).lower()
        return str(status).rsplit(".", 1)[-1].lower()
