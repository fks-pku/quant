"""Execution reference price resolution shared by live and paper execution."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class ExecutionReferencePrice:
    symbol: str
    price: float
    source: str
    field: str
    timestamp: Optional[datetime] = None


class ExecutionReferencePriceResolver:
    """Resolve execution reference prices from broker or data-provider quotes."""

    def __init__(
        self,
        mode: str,
        broker: Any = None,
        data_provider: Any = None,
        allow_strategy_price_fallback: bool = False,
    ) -> None:
        self.mode = str(mode or "").lower()
        self.broker = broker
        self.data_provider = data_provider
        self.allow_strategy_price_fallback = bool(allow_strategy_price_fallback)

    def resolve(
        self,
        symbol: str,
        side: Optional[str] = None,
        strategy_price: Optional[float] = None,
    ) -> Optional[ExecutionReferencePrice]:
        for source_name, provider in (("broker", self.broker), ("data_provider", self.data_provider)):
            ref = self._resolve_from_provider(source_name, provider, symbol, side)
            if ref is not None:
                return ref
        fallback = _positive_float(strategy_price)
        if self.allow_strategy_price_fallback and fallback is not None:
            return ExecutionReferencePrice(
                symbol=str(symbol),
                price=fallback,
                source="strategy_fallback",
                field="strategy_price",
            )
        return None

    def _resolve_from_provider(
        self,
        source_name: str,
        provider: Any,
        symbol: str,
        side: Optional[str],
    ) -> Optional[ExecutionReferencePrice]:
        if provider is None:
            return None
        for method_name in ("get_execution_reference_price", "get_open_price", "get_quote", "get_latest_price"):
            method = getattr(provider, method_name, None)
            if not callable(method):
                continue
            response = _call_quote_method(method, symbol, side)
            ref = _coerce_reference_price(symbol, response, f"{source_name}.{method_name}", side)
            if ref is not None:
                return ref
        return None


def _call_quote_method(method: Any, symbol: str, side: Optional[str]) -> Any:
    attempts = (
        lambda: method(symbol=symbol, side=side),
        lambda: method(symbol=symbol),
        lambda: method(symbol, side),
        lambda: method(symbol),
    )
    for attempt in attempts:
        try:
            return attempt()
        except TypeError:
            continue
        except Exception:
            return None
    return None


def _coerce_reference_price(
    symbol: str,
    response: Any,
    source: str,
    side: Optional[str],
) -> Optional[ExecutionReferencePrice]:
    if response is None:
        return None
    if isinstance(response, ExecutionReferencePrice):
        return response
    direct = _positive_float(response)
    if direct is not None:
        return ExecutionReferencePrice(str(symbol), direct, source, "price")

    field_groups = [
        ("open", ("open", "open_price", "openPrice", "open_px")),
    ]
    side_text = str(side or "").upper()
    if side_text == "BUY":
        field_groups.append(("ask", ("ask", "ask_price", "askPrice", "ask1", "ask1Price")))
    elif side_text == "SELL":
        field_groups.append(("bid", ("bid", "bid_price", "bidPrice", "bid1", "bid1Price")))
    field_groups.append(("last", ("last_price", "lastPrice", "latest_price", "latestPrice", "price", "close")))

    for field_name, aliases in field_groups:
        value = _first_present_value(response, aliases)
        price = _positive_float(value)
        if price is not None:
            timestamp = _first_present_value(response, ("timestamp", "time", "datetime"))
            return ExecutionReferencePrice(
                symbol=str(symbol),
                price=price,
                source=source,
                field=field_name,
                timestamp=timestamp if isinstance(timestamp, datetime) else None,
            )
    return None


def _first_present_value(item: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        if hasattr(item, "get"):
            try:
                value = item.get(name, None)
                if value is not None:
                    return value
            except Exception:
                pass
        if hasattr(item, name):
            value = getattr(item, name)
            if value is not None:
                return value
    return None


def _positive_float(value: Any) -> Optional[float]:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 0 and number == number:
        return number
    return None
