"""Unit tests for data layer."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd

from quant.data.normalizer import Normalizer, Bar, Quote, MarketTrade


class TestNormalizer:
    """Tests for data normalizer."""

    def test_normalize_bar_from_dict(self):
        """Test normalizing bar from dictionary."""
        data = {
            "timestamp": "2025-01-01 09:30:00",
            "symbol": "AAPL",
            "open": 150.0,
            "high": 151.0,
            "low": 149.0,
            "close": 150.5,
            "volume": 1000000,
        }

        bar = Normalizer.normalize_bar(data)

        assert isinstance(bar, Bar)
        assert bar.symbol == "AAPL"
        assert bar.open == 150.0
        assert bar.high == 151.0
        assert bar.low == 149.0
        assert bar.close == 150.5
        assert bar.volume == 1000000

    def test_normalize_bar_from_pandas_series(self):
        """Test normalizing bar from pandas Series."""
        data = pd.Series({
            "timestamp": datetime.now(),
            "symbol": "AAPL",
            "open": 150.0,
            "high": 151.0,
            "low": 149.0,
            "close": 150.5,
            "volume": 1000000,
        })

        bar = Normalizer.normalize_bar(data)

        assert isinstance(bar, Bar)
        assert bar.symbol == "AAPL"

    def test_normalize_quote(self):
        """Test normalizing quote data."""
        data = {
            "timestamp": datetime.now(),
            "symbol": "AAPL",
            "bid": 150.0,
            "ask": 150.5,
            "bid_size": 100,
            "ask_size": 100,
        }

        quote = Normalizer.normalize_quote(data)

        assert isinstance(quote, Quote)
        assert quote.symbol == "AAPL"
        assert quote.bid == 150.0
        assert quote.ask == 150.5

    def test_normalize_trade(self):
        """Test normalizing trade data."""
        data = {
            "timestamp": datetime.now(),
            "symbol": "AAPL",
            "price": 150.25,
            "size": 100,
            "exchange": "NASDAQ",
        }

        trade = Normalizer.normalize_trade(data)

        assert isinstance(trade, MarketTrade)
        assert trade.symbol == "AAPL"
        assert trade.price == 150.25

    def test_to_standard_dict(self):
        """Test converting Bar to standard dictionary."""
        bar = Bar(
            timestamp=datetime.now(),
            symbol="AAPL",
            open=150.0,
            high=151.0,
            low=149.0,
            close=150.5,
            volume=1000000,
        )

        d = Normalizer.to_standard_dict(bar)

        assert d["symbol"] == "AAPL"
        assert d["open"] == 150.0
        assert d["close"] == 150.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
