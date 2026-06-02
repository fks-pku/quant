"""Shared strategy lifecycle dispatch helpers."""

from datetime import date
from typing import Any, Iterable


def start_strategy(strategy: Any) -> None:
    context = getattr(strategy, "context", None)
    hook = getattr(strategy, "on_start", None)
    if callable(hook):
        hook(context)


def stop_strategy(strategy: Any) -> None:
    context = getattr(strategy, "context", None)
    hook = getattr(strategy, "on_stop", None)
    if callable(hook):
        hook(context)


def before_trading(strategy: Any, trading_date: date) -> None:
    context = getattr(strategy, "context", None)
    hook = getattr(strategy, "on_before_trading", None)
    if callable(hook):
        hook(context, trading_date)


def after_trading(strategy: Any, trading_date: date) -> None:
    context = getattr(strategy, "context", None)
    hook = getattr(strategy, "on_after_trading", None)
    if callable(hook):
        hook(context, trading_date)


def feed_strategy_bars(strategy: Any, bars: Iterable[Any]) -> None:
    context = getattr(strategy, "context", None)
    bar_list = list(bars)
    if not bar_list:
        return
    batch_hook = getattr(strategy, "on_data_batch", None)
    if callable(batch_hook):
        batch_hook(context, bar_list)
        return
    data_hook = getattr(strategy, "on_data", None)
    if callable(data_hook):
        for bar in bar_list:
            data_hook(context, bar)
