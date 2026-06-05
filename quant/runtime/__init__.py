"""Shared runtime helpers for strategy lifecycle dispatch."""

from quant.runtime.daily_strategy_runner import (
    DailyRunResult,
    DailySnapshot,
    build_daily_snapshot,
    extract_bar_date,
    extract_bar_symbol,
    run_daily_snapshot,
    run_daily_snapshots,
)
from quant.runtime.execution_reference import (
    ExecutionReferencePrice,
    ExecutionReferencePriceResolver,
)
from quant.runtime.execution_cost import (
    CostProtectionEstimate,
    estimate_cost_protection_limit,
)
from quant.runtime.strategy_cycle import (
    after_trading,
    before_trading,
    feed_strategy_bars,
    start_strategy,
    stop_strategy,
)

__all__ = [
    "DailyRunResult",
    "DailySnapshot",
    "ExecutionReferencePrice",
    "ExecutionReferencePriceResolver",
    "CostProtectionEstimate",
    "after_trading",
    "before_trading",
    "build_daily_snapshot",
    "estimate_cost_protection_limit",
    "extract_bar_date",
    "extract_bar_symbol",
    "feed_strategy_bars",
    "run_daily_snapshot",
    "run_daily_snapshots",
    "start_strategy",
    "stop_strategy",
]
