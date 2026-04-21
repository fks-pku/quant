"""Datetime utilities for timezone and market hours."""

from datetime import datetime, time, timedelta
from typing import Optional, Tuple
import pytz


def get_current_time(timezone: str = "UTC") -> datetime:
    """Get current time in the specified timezone."""
    tz = pytz.timezone(timezone)
    return datetime.now(tz)


def is_market_open(
    current_time: datetime,
    open_hour: int,
    open_minute: int,
    close_hour: int,
    close_minute: int,
) -> bool:
    """Check if market is currently open based on time components."""
    market_open = time(open_hour, open_minute)
    market_close = time(close_hour, close_minute)
    current_time_only = current_time.time()
    return market_open <= current_time_only <= market_close


def is_trading_day(current_time: datetime, market: str = "US") -> bool:
    """Check if current day is a trading day (Mon-Fri, excluding holidays)."""
    if current_time.weekday() >= 5:
        return False
    return True


def get_next_market_open(
    reference_time: datetime,
    timezone: str,
    open_hour: int,
    open_minute: int,
) -> datetime:
    """Get next market open datetime."""
    tz = pytz.timezone(timezone)
    ref = reference_time.astimezone(tz)

    market_open = tz.localize(
        datetime.combine(ref.date(), time(open_hour, open_minute))
    )

    if ref.time() > time(open_hour, open_minute):
        market_open += timedelta(days=1)

    while market_open.weekday() >= 5:
        market_open += timedelta(days=1)

    return market_open


def get_next_market_close(
    reference_time: datetime,
    timezone: str,
    close_hour: int,
    close_minute: int,
) -> datetime:
    """Get next market close datetime."""
    tz = pytz.timezone(timezone)
    ref = reference_time.astimezone(tz)

    market_close = tz.localize(
        datetime.combine(ref.date(), time(close_hour, close_minute))
    )

    if ref.time() > time(close_hour, close_minute):
        market_close += timedelta(days=1)

    while market_close.weekday() >= 5:
        market_close += timedelta(days=1)

    return market_close


def parse_timeframe(timeframe: str) -> Tuple[str, int]:
    """Parse timeframe string like '5m', '1h', '1d' into (unit, value)."""
    if timeframe.endswith("s"):
        return ("second", int(timeframe[:-1]))
    elif timeframe.endswith("m"):
        return ("minute", int(timeframe[:-1]))
    elif timeframe.endswith("h"):
        return ("hour", int(timeframe[:-1]))
    elif timeframe.endswith("d"):
        return ("day", int(timeframe[:-1]))
    else:
        raise ValueError(f"Invalid timeframe format: {timeframe}")


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    """Convert timeframe string to timedelta."""
    unit, value = parse_timeframe(timeframe)
    if unit == "second":
        return timedelta(seconds=value)
    elif unit == "minute":
        return timedelta(minutes=value)
    elif unit == "hour":
        return timedelta(hours=value)
    elif unit == "day":
        return timedelta(days=value)
    raise ValueError(f"Invalid timeframe: {timeframe}")