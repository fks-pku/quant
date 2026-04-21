"""Tests for Strategy base class on_fill accumulation."""

from quant.features.strategies.base import Strategy
from quant.shared.models.fill import Fill
from datetime import datetime


class _FakeContext:
    pass


class _ConcreteStrategy(Strategy):
    def __init__(self):
        super().__init__("TestStrategy")

    @property
    def symbols(self):
        return []


def _make_fill(symbol, quantity, side="BUY"):
    return Fill(
        order_id="ord1",
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=100.0,
        commission=0.0,
        timestamp=datetime.now(),
    )


def test_buy_fills_accumulate():
    s = _ConcreteStrategy()
    ctx = _FakeContext()
    s.on_fill(ctx, _make_fill("AAPL", 100, "BUY"))
    s.on_fill(ctx, _make_fill("AAPL", 50, "BUY"))
    assert s.get_position("AAPL") == 150


def test_sell_fills_add_quantity_regardless_of_side():
    s = _ConcreteStrategy()
    ctx = _FakeContext()
    s.on_fill(ctx, _make_fill("AAPL", 100, "BUY"))
    s.on_fill(ctx, _make_fill("AAPL", 30, "SELL"))
    assert s.get_position("AAPL") == 130


def test_fills_on_new_symbols_start_from_zero():
    s = _ConcreteStrategy()
    ctx = _FakeContext()
    s.on_fill(ctx, _make_fill("MSFT", 25, "BUY"))
    assert s.get_position("MSFT") == 25
    assert s.get_position("GOOG") == 0


def test_on_fill_ignores_objects_without_symbol_or_quantity():
    s = _ConcreteStrategy()
    ctx = _FakeContext()
    s.on_fill(ctx, "not a fill")
    assert s.get_all_positions() == {}


def test_multiple_symbols_tracked_independently():
    s = _ConcreteStrategy()
    ctx = _FakeContext()
    s.on_fill(ctx, _make_fill("AAPL", 100, "BUY"))
    s.on_fill(ctx, _make_fill("MSFT", 200, "BUY"))
    s.on_fill(ctx, _make_fill("AAPL", 30, "SELL"))
    assert s.get_position("AAPL") == 130
    assert s.get_position("MSFT") == 200
