import os
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from quant.infrastructure.data.providers.akshare import AkshareProvider

SKIP_NETWORK = os.getenv("SKIP_NETWORK") == "1"


@pytest.fixture
def provider():
    p = AkshareProvider()
    p.connect()
    return p


def _make_akshare_df(rows=10):
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="1D")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": [100.0 + i for i in range(rows)],
            "收盘": [100.5 + i for i in range(rows)],
            "最高": [105.0 + i for i in range(rows)],
            "最低": [95.0 + i for i in range(rows)],
            "成交量": [1000000 + i for i in range(rows)],
        }
    )


def _patch_akshare(mock_ak, return_df):
    mock_ak.stock_zh_a_hist.return_value = return_df
    mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()


def test_akshare_get_bars_normalizes_columns(provider):
    mock_df = _make_akshare_df()

    with patch("quant.infrastructure.data.providers.akshare.ak") as mock_ak, \
         patch("quant.infrastructure.data.providers.akshare.AKSHARE_AVAILABLE", True):
        _patch_akshare(mock_ak, mock_df)

        end = datetime.now()
        start = end - pd.Timedelta(days=30)
        df = provider.get_bars("600519", start, end, "1d")

    assert not df.empty
    for col in ["timestamp", "symbol", "open", "high", "low", "close", "volume"]:
        assert col in df.columns
    assert df["symbol"].iloc[0] == "600519"


def test_akshare_invalid_symbol_returns_empty(provider):
    with patch("quant.infrastructure.data.providers.akshare.ak") as mock_ak, \
         patch("quant.infrastructure.data.providers.akshare.AKSHARE_AVAILABLE", True):
        _patch_akshare(mock_ak, pd.DataFrame())

        end = datetime.now()
        start = end - pd.Timedelta(days=10)
        df = provider.get_bars("NOTREAL", start, end, "1d")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_akshare_minute_timeframe_warns_and_returns_empty(provider):
    with patch("quant.infrastructure.data.providers.akshare.ak") as mock_ak, \
         patch("quant.infrastructure.data.providers.akshare.AKSHARE_AVAILABLE", True):
        _patch_akshare(mock_ak, _make_akshare_df())

        end = datetime.now()
        start = end - pd.Timedelta(days=5)
        df = provider.get_bars("600519", start, end, "1m")

    assert df.empty


def test_akshare_rate_limiting(provider):
    import time

    mock_df = _make_akshare_df()

    with patch("quant.infrastructure.data.providers.akshare.ak") as mock_ak, \
         patch("quant.infrastructure.data.providers.akshare.AKSHARE_AVAILABLE", True):
        _patch_akshare(mock_ak, mock_df)

        end = datetime.now()
        start = end - pd.Timedelta(days=5)

        provider._last_request_time = 0
        t0 = time.time()
        provider.get_bars("600519", start, end, "1d")
        provider.get_bars("000001", start, end, "1d")
        elapsed = time.time() - t0

        assert elapsed >= provider._min_interval * 0.9


def test_akshare_get_quote_returns_price(provider):
    mock_spot = pd.DataFrame({
        "代码": ["600519"],
        "最新价": [1500.0],
    })

    with patch("quant.infrastructure.data.providers.akshare.ak") as mock_ak, \
         patch("quant.infrastructure.data.providers.akshare.AKSHARE_AVAILABLE", True):
        mock_ak.stock_zh_a_spot_em.return_value = mock_spot

        quote = provider.get_quote("600519")

    assert quote["symbol"] == "600519"
    assert quote["bid"] == 1500.0
    assert quote["ask"] == 1500.0


def test_akshare_get_quote_missing_symbol_returns_zeros(provider):
    mock_spot = pd.DataFrame({
        "代码": ["000001"],
        "最新价": [10.0],
    })

    with patch("quant.infrastructure.data.providers.akshare.ak") as mock_ak, \
         patch("quant.infrastructure.data.providers.akshare.AKSHARE_AVAILABLE", True):
        mock_ak.stock_zh_a_spot_em.return_value = mock_spot

        quote = provider.get_quote("600519")

    assert quote["bid"] == 0.0
    assert quote["ask"] == 0.0
