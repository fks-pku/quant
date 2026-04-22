from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from quant.infrastructure.data.providers.tushare import TushareProvider, _CN_INDEX_CODES


def _make_tushare_stock_df(rows=10):
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="1D")
    return pd.DataFrame(
        {
            "ts_code": ["600519.SH"] * rows,
            "trade_date": dates.strftime("%Y%m%d"),
            "open": [100.0 + i for i in range(rows)],
            "high": [105.0 + i for i in range(rows)],
            "low": [95.0 + i for i in range(rows)],
            "close": [100.5 + i for i in range(rows)],
            "vol": [1000000 + i for i in range(rows)],
            "amount": [50000 + i for i in range(rows)],
        }
    )


def _make_tushare_index_df(rows=10):
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="1D")
    return pd.DataFrame(
        {
            "ts_code": ["000300.SH"] * rows,
            "trade_date": dates.strftime("%Y%m%d"),
            "open": [3000.0 + i for i in range(rows)],
            "high": [3050.0 + i for i in range(rows)],
            "low": [2950.0 + i for i in range(rows)],
            "close": [3010.0 + i for i in range(rows)],
            "vol": [5000000 + i for i in range(rows)],
            "amount": [200000 + i for i in range(rows)],
        }
    )


@pytest.fixture
def provider():
    p = TushareProvider()
    with patch.object(p, "_load_config", return_value={"token": "test_token", "api_url": ""}):
        with patch("quant.infrastructure.data.providers.tushare.TUSHARE_AVAILABLE", True):
            with patch("quant.infrastructure.data.providers.tushare.ts") as mock_ts:
                mock_api = MagicMock()
                mock_ts.pro_api.return_value = mock_api
                with patch("quant.infrastructure.data.providers.tushare.DuckDBStorage"):
                    p.connect()
    return p


def test_ts_code_conversion_stock_sh():
    assert TushareProvider._to_ts_code("600519") == "600519.SH"


def test_ts_code_conversion_stock_sz():
    assert TushareProvider._to_ts_code("000858") == "000858.SZ"


def test_ts_code_conversion_index_sh():
    assert TushareProvider._to_ts_code("000300") == "000300.SH"


def test_ts_code_conversion_index_sz():
    assert TushareProvider._to_ts_code("399001") == "399001.SZ"


def test_from_ts_code():
    assert TushareProvider._from_ts_code("600519.SH") == "600519"
    assert TushareProvider._from_ts_code("000001.SZ") == "000001"


def test_is_index():
    assert TushareProvider._is_index("000300") is True
    assert TushareProvider._is_index("399006") is True
    assert TushareProvider._is_index("600519") is False
    assert TushareProvider._is_index("000001") is True


def test_get_bars_stock_normalizes_columns(provider):
    provider._api = MagicMock()
    provider._api.daily.return_value = _make_tushare_stock_df()
    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = pd.DataFrame()

    end = datetime.now()
    start = end - pd.Timedelta(days=30)
    df = provider.get_bars("600519", start, end, "1d")

    assert not df.empty
    for col in ["timestamp", "symbol", "open", "high", "low", "close", "volume"]:
        assert col in df.columns
    assert df["symbol"].iloc[0] == "600519"
    provider._api.daily.assert_called_once()


def test_get_bars_index_uses_index_daily(provider):
    provider._api = MagicMock()
    provider._api.index_daily.return_value = _make_tushare_index_df()
    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = pd.DataFrame()

    end = datetime.now()
    start = end - pd.Timedelta(days=30)
    df = provider.get_bars("000300", start, end, "1d")

    assert not df.empty
    assert df["symbol"].iloc[0] == "000300"
    provider._api.index_daily.assert_called_once()
    provider._api.daily.assert_not_called()


def test_get_bars_minute_timeframe_returns_empty(provider):
    end = datetime.now()
    start = end - pd.Timedelta(days=5)
    df = provider.get_bars("600519", start, end, "1m")
    assert df.empty


def test_get_bars_empty_response(provider):
    provider._api = MagicMock()
    provider._api.daily.return_value = pd.DataFrame()
    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = pd.DataFrame()

    end = datetime.now()
    start = end - pd.Timedelta(days=10)
    df = provider.get_bars("600519", start, end, "1d")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_get_bars_caches_to_duckdb(provider):
    provider._api = MagicMock()
    provider._api.daily.return_value = _make_tushare_stock_df()
    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = pd.DataFrame()

    end = datetime.now()
    start = end - pd.Timedelta(days=10)
    provider.get_bars("600519", start, end, "1d")

    provider._storage.save_bars.assert_called_once()


def test_get_bars_returns_cached_when_available(provider):
    end = datetime.now()
    start = end - pd.Timedelta(days=5)
    cached_df = pd.DataFrame(
        {
            "timestamp": pd.date_range(start=start, end=end, freq="1D"),
            "symbol": ["600519"] * 6,
            "open": [100.0] * 6,
            "high": [105.0] * 6,
            "low": [95.0] * 6,
            "close": [100.5] * 6,
            "volume": [1000000] * 6,
        }
    )

    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = cached_df

    df = provider.get_bars("600519", start, end, "1d")

    assert not df.empty
    provider._api.daily.assert_not_called()


def test_get_quote_returns_price(provider):
    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = pd.DataFrame()
    provider._api = MagicMock()
    provider._api.daily.return_value = _make_tushare_stock_df(5)

    quote = provider.get_quote("600519")

    assert quote["symbol"] == "600519"
    assert quote["bid"] > 0
    assert quote["ask"] > 0


def test_get_quote_missing_returns_zeros(provider):
    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = pd.DataFrame()
    provider._api = MagicMock()
    provider._api.daily.return_value = pd.DataFrame()

    quote = provider.get_quote("999999")

    assert quote["symbol"] == "999999"
    assert quote["bid"] == 0.0
    assert quote["ask"] == 0.0
