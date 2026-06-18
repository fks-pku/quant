import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


SUBMITTED_STATUSES = {"submitted", "filled", "partial", "cancelled", "canceled"}


def is_real_submit_complete(
    db_path: Path,
    *,
    signal_date: str,
    execution_date: str,
    mode: str = "live",
    days: int = 30,
) -> bool:
    store = StrategyStateStore(db_path)
    target_execution_date = execution_date[:10]
    for row in store.get_recent_orders(mode=mode, days=days):
        if not _has_submit_fact(row):
            continue
        if _row_dates_include_execution(row, target_execution_date):
            return True
    for row in store.get_recent_fills(mode=mode, days=days):
        if float(row.get("quantity") or row.get("fill_quantity") or 0.0) <= 0:
            continue
        if _row_dates_include_execution(row, target_execution_date):
            return True
    for row in store.get_recent_signals(mode=mode, days=days):
        if not _has_submit_fact(row):
            continue
        if _row_dates_include_execution(row, target_execution_date):
            return True
    return False


def _has_submit_fact(row: Dict[str, Any]) -> bool:
    status = str(row.get("status") or "").lower()
    return (
        bool(row.get("broker_order_id"))
        or status in SUBMITTED_STATUSES
        or float(row.get("fill_quantity") or 0.0) > 0
    )


def _row_dates_include_execution(row: Dict[str, Any], execution_date: str) -> bool:
    return execution_date in set(_submit_fact_dates(row))


def _submit_fact_dates(row: Dict[str, Any]) -> Iterable[str]:
    for key in ("submit_date", "record_date", "signal_date", "timestamp", "fill_time"):
        value = str(row.get(key) or "")[:10]
        if value:
            yield value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--execution-date", required=True)
    parser.add_argument("--mode", default="live")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    complete = is_real_submit_complete(
        Path(args.db),
        signal_date=args.signal_date,
        execution_date=args.execution_date,
        mode=args.mode,
        days=args.days,
    )
    print("1" if complete else "0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
