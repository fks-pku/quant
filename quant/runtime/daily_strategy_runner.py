"""Shared daily snapshot strategy runner."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from quant.runtime.strategy_cycle import after_trading, before_trading, feed_strategy_bars


@dataclass(frozen=True)
class DailySnapshot:
    """A same-date batch of bars prepared for a daily strategy decision."""

    trading_date: date
    bars: Mapping[str, Any]
    required_symbols: tuple[str, ...] = ()
    missing_symbols: tuple[str, ...] = ()
    stale_symbols: tuple[str, ...] = ()
    duplicate_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyRunResult:
    """Result of attempting to run one strategy on a daily snapshot."""

    trading_date: date
    ran: bool
    bar_count: int
    missing_symbols: tuple[str, ...] = ()
    stale_symbols: tuple[str, ...] = ()
    duplicate_symbols: tuple[str, ...] = ()


def extract_bar_symbol(bar: Any) -> Optional[str]:
    """Return a bar symbol from dict-like or attribute-style data."""

    symbol = _get_value(bar, ("symbol", "ts_code", "code", "ticker"))
    if symbol is None:
        return None
    return str(symbol)


def extract_bar_date(bar: Any) -> Optional[date]:
    """Return the trading date encoded in a bar when available."""

    value = _get_value(bar, ("trading_date", "trade_date", "date", "timestamp", "datetime", "time"))
    return _coerce_date(value)


def build_daily_snapshot(
    bars: Iterable[Any],
    trading_date: Union[date, datetime, str],
    required_symbols: Optional[Sequence[str]] = None,
) -> DailySnapshot:
    """Validate and group a daily bar batch by symbol."""

    normalized_date = _coerce_date(trading_date)
    if normalized_date is None:
        raise ValueError(f"Cannot coerce trading_date to date: {trading_date!r}")

    required = tuple(str(symbol) for symbol in (required_symbols or ()) if symbol)
    bars_by_symbol: dict[str, Any] = {}
    bar_dates: dict[str, date] = {}
    duplicates: set[str] = set()
    unnamed_count = 0

    for bar in bars:
        symbol = extract_bar_symbol(bar)
        if symbol is None:
            unnamed_count += 1
            symbol = f"__bar_{unnamed_count}"
        if symbol in bars_by_symbol:
            duplicates.add(symbol)
        bars_by_symbol[symbol] = bar
        bar_date = extract_bar_date(bar)
        if bar_date is not None:
            bar_dates[symbol] = bar_date

    missing = tuple(symbol for symbol in required if symbol not in bars_by_symbol)
    stale = tuple(
        symbol
        for symbol in required
        if symbol in bar_dates and bar_dates[symbol] != normalized_date
    )
    return DailySnapshot(
        trading_date=normalized_date,
        bars=bars_by_symbol,
        required_symbols=required,
        missing_symbols=missing,
        stale_symbols=stale,
        duplicate_symbols=tuple(sorted(duplicates)),
    )


def run_daily_snapshot(
    strategy: Any,
    trading_date: Union[date, datetime, str],
    bars: Iterable[Any],
    *,
    strict: bool = True,
    call_before: bool = False,
) -> DailyRunResult:
    """Run a strategy once on a same-date daily snapshot."""

    symbols = getattr(strategy, "symbols", None) or ()
    if isinstance(symbols, str):
        symbols = (symbols,)
    required_symbols = tuple(str(symbol) for symbol in symbols if symbol)
    snapshot = build_daily_snapshot(bars, trading_date, required_symbols)
    if strict and (snapshot.missing_symbols or snapshot.stale_symbols):
        return DailyRunResult(
            trading_date=snapshot.trading_date,
            ran=False,
            bar_count=len(snapshot.bars),
            missing_symbols=snapshot.missing_symbols,
            stale_symbols=snapshot.stale_symbols,
            duplicate_symbols=snapshot.duplicate_symbols,
        )

    if call_before:
        before_trading(strategy, snapshot.trading_date)
    feed_strategy_bars(strategy, _ordered_bars(snapshot))
    after_trading(strategy, snapshot.trading_date)
    return DailyRunResult(
        trading_date=snapshot.trading_date,
        ran=True,
        bar_count=len(snapshot.bars),
        missing_symbols=snapshot.missing_symbols,
        stale_symbols=snapshot.stale_symbols,
        duplicate_symbols=snapshot.duplicate_symbols,
    )


def _ordered_bars(snapshot: DailySnapshot) -> list[Any]:
    ordered: list[Any] = []
    seen: set[str] = set()
    for symbol in snapshot.required_symbols:
        if symbol in snapshot.bars:
            ordered.append(snapshot.bars[symbol])
            seen.add(symbol)
    for symbol, bar in snapshot.bars.items():
        if symbol not in seen:
            ordered.append(bar)
    return ordered


def _get_value(item: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        if isinstance(item, Mapping) and name in item:
            return item[name]
        value = getattr(item, name, None)
        if value is not None:
            return value
        getter = getattr(item, "get", None)
        if callable(getter):
            value = getter(name, None)
            if value is not None:
                return value
    return None


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) == 8 and text.isdigit():
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        return datetime.fromisoformat(text[:10]).date()
    return None
