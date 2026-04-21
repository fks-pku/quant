"""Tests for normalizer MarketTrade rename and normalize_trade correctness."""

from datetime import datetime

from quant.infrastructure.data.normalizer import MarketTrade, Normalizer


def test_import_market_trade():
    from quant.infrastructure.data.normalizer import MarketTrade as MT
    assert MT is MarketTrade


def test_normalize_trade_returns_market_trade():
    data = {
        "timestamp": "2024-01-01 10:00:00",
        "symbol": "AAPL",
        "price": 150.0,
        "size": 100,
        "exchange": "NASDAQ",
    }
    result = Normalizer.normalize_trade(data)
    assert isinstance(result, MarketTrade)
    assert result.symbol == "AAPL"
    assert result.price == 150.0
    assert result.size == 100
    assert result.exchange == "NASDAQ"


def test_normalize_trade_without_timestamp_falls_to_now():
    data = {
        "date": "2024-01-01",
        "symbol": "MSFT",
        "price": 300.0,
        "size": 50,
        "exchange": "",
    }
    result = Normalizer.normalize_trade(data)
    assert isinstance(result, MarketTrade)
    assert result.symbol == "MSFT"
    from datetime import datetime as dt
    assert abs((result.timestamp - dt.now()).total_seconds()) < 5


def test_normalize_trade_with_timestamp_key():
    data = {
        "timestamp": "2024-01-01 10:00:00",
        "symbol": "MSFT",
        "price": 300.0,
        "size": 50,
        "exchange": "",
    }
    result = Normalizer.normalize_trade(data)
    assert isinstance(result, MarketTrade)
    assert result.timestamp.year == 2024
    assert result.timestamp.month == 1
    assert result.timestamp.day == 1


def test_normalize_trade_uses_volume_as_size_fallback():
    data = {
        "timestamp": datetime(2024, 6, 1),
        "symbol": "GOOG",
        "price": 200.0,
        "volume": 500,
        "exchange": "NYSE",
    }
    result = Normalizer.normalize_trade(data)
    assert result.size == 500


def test_normalize_trade_symbol_from_parameter():
    data = {
        "timestamp": datetime(2024, 1, 1),
        "price": 100.0,
        "size": 10,
        "exchange": "",
    }
    result = Normalizer.normalize_trade(data, symbol="TSLA")
    assert result.symbol == "TSLA"


def test_market_trade_fields():
    mt = MarketTrade(
        timestamp=datetime(2024, 1, 1),
        symbol="AAPL",
        price=150.0,
        size=100,
        exchange="NASDAQ",
    )
    assert mt.timestamp == datetime(2024, 1, 1)
    assert mt.symbol == "AAPL"
    assert mt.price == 150.0
    assert mt.size == 100
    assert mt.exchange == "NASDAQ"
