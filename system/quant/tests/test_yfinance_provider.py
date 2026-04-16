import os
import time
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from quant.data.providers.yfinance_provider import YfinanceProvider

SKIP_NETWORK = os.getenv("SKIP_NETWORK") == "1"


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path / "cache")


@pytest.fixture
def provider(cache_dir):
    p = YfinanceProvider(cache_dir=cache_dir, cache_ttl_hours=24)
    p.connect()
    return p


def _make_ohlcv_df(rows=10):
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="1D")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(rows)],
            "High": [105.0 + i for i in range(rows)],
            "Low": [95.0 + i for i in range(rows)],
            "Close": [100.0 + i for i in range(rows)],
            "Volume": [1000000 + i for i in range(rows)],
        },
        index=dates,
    )


@pytest.mark.skipif(SKIP_NETWORK, reason="Network test")
def test_yfinance_get_bars_returns_ohlcv(provider):
    end = datetime.now()
    start = end - timedelta(days=30)
    df = provider.get_bars("AAPL", start, end, "1d")
    assert not df.empty
    for col in ["open", "high", "low", "close", "volume"]:
        assert col in df.columns


@pytest.mark.skipif(SKIP_NETWORK, reason="Network test")
def test_yfinance_caches_to_parquet(provider, cache_dir):
    end = datetime.now()
    start = end - timedelta(days=10)
    df1 = provider.get_bars("MSFT", start, end, "1d")

    parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    assert len(parquet_files) >= 1

    df2 = provider.get_bars("MSFT", start, end, "1d")
    assert not df2.empty
    pd.testing.assert_frame_equal(df1.reset_index(drop=True), df2.reset_index(drop=True))


@pytest.mark.skipif(SKIP_NETWORK, reason="Network test")
def test_yfinance_invalid_symbol_returns_empty(provider):
    end = datetime.now()
    start = end - timedelta(days=10)
    df = provider.get_bars("NOTAREALSYMBOL12345", start, end, "1d")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_yfinance_rate_limiting(provider):
    mock_df = _make_ohlcv_df()

    with patch("quant.data.providers.yfinance_provider.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_df
        mock_yf.Ticker.return_value = mock_ticker

        end = datetime.now()
        start = end - timedelta(days=5)

        provider._last_download_time = 0
        t0 = time.time()
        provider.get_bars("AAPL", start, end, "1d")
        provider.get_bars("MSFT", start, end, "1d")
        elapsed = time.time() - t0

        assert elapsed >= provider._min_download_interval * 0.9


def test_yfinance_cache_validity(cache_dir):
    p = YfinanceProvider(cache_dir=cache_dir, cache_ttl_hours=0)
    p.connect()

    mock_df = _make_ohlcv_df()
    with patch("quant.data.providers.yfinance_provider.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_df
        mock_yf.Ticker.return_value = mock_ticker

        end = datetime.now()
        start = end - timedelta(days=5)

        df1 = p.get_bars("AAPL", start, end, "1d")
        assert mock_yf.Ticker.call_count == 1

        import time as t
        t.sleep(0.1)

        df2 = p.get_bars("AAPL", start, end, "1d")
        assert mock_yf.Ticker.call_count == 2


def test_yfinance_sanitizes_symbol(cache_dir):
    p = YfinanceProvider(cache_dir=cache_dir)
    assert p._sanitize_symbol("^VIX") == "_VIX"
    assert p._sanitize_symbol("BRK/B") == "BRK_B"
    assert p._sanitize_symbol("A B") == "A_B"
