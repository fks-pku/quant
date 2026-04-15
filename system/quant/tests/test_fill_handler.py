"""Unit tests for FillHandler."""

import pytest
from datetime import datetime

from quant.core.portfolio import Portfolio
from quant.core.events import EventBus
from quant.execution.fill_handler import FillHandler


def _make_fill_handler():
    portfolio = Portfolio(initial_cash=100000)
    bus = EventBus()
    handler = FillHandler(portfolio, bus, {})
    return handler, portfolio, bus


class TestFillHandlerProcessFill:
    def test_process_buy_fill(self):
        handler, portfolio, _ = _make_fill_handler()
        fill = handler.process_fill("ord1", "AAPL", "BUY", 10, 150.0, commission=0.5)
        assert fill.symbol == "AAPL"
        assert fill.quantity == 10
        assert fill.price == 150.0
        pos = portfolio.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 10

    def test_process_sell_fill(self):
        handler, portfolio, _ = _make_fill_handler()
        handler.process_fill("ord1", "AAPL", "BUY", 10, 150.0, commission=0.5)
        fill = handler.process_fill("ord2", "AAPL", "SELL", 10, 160.0, commission=0.5)
        assert fill.price == 160.0

    def test_fill_callback_called(self):
        handler, _, _ = _make_fill_handler()
        received = []
        handler.register_fill_callback(lambda fill: received.append(fill))
        handler.process_fill("ord1", "AAPL", "BUY", 10, 150.0)
        assert len(received) == 1
        assert received[0].symbol == "AAPL"


class TestFillHandlerStats:
    def test_get_total_commission(self):
        handler, _, _ = _make_fill_handler()
        handler.process_fill("ord1", "AAPL", "BUY", 10, 150.0, commission=1.0)
        handler.process_fill("ord2", "MSFT", "BUY", 5, 300.0, commission=1.5)
        assert handler.get_total_commission() == 2.5

    def test_get_fill_stats(self):
        handler, _, _ = _make_fill_handler()
        handler.process_fill("ord1", "AAPL", "BUY", 10, 150.0, commission=1.0)
        handler.process_fill("ord2", "AAPL", "SELL", 10, 160.0, commission=1.0)
        stats = handler.get_fill_stats()
        assert stats["total_fills"] == 2
        assert stats["buy_fills"] == 1
        assert stats["sell_fills"] == 1

    def test_get_fills_filtered_by_symbol(self):
        handler, _, _ = _make_fill_handler()
        handler.process_fill("ord1", "AAPL", "BUY", 10, 150.0)
        handler.process_fill("ord2", "MSFT", "BUY", 5, 300.0)
        aapl_fills = handler.get_fills(symbol="AAPL")
        assert len(aapl_fills) == 1
        assert aapl_fills[0].symbol == "AAPL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
