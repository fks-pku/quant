# Tushare Data Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Tushare data provider for CN stock/index daily bars, cached in DuckDB, following hexagonal architecture.

**Architecture:** TushareProvider extends DataProvider base class in infrastructure layer. Data fetched from Tushare Pro API is cached in DuckDB via existing DuckDBStorage. Token configured in config.yaml.

**Tech Stack:** Python 3.10+, tushare SDK, pandas, DuckDB, pytest

---

### Task 1: Add tushare dependency + config

**Files:**
- Modify: `quant/requirements.txt`
- Modify: `quant/shared/config/config.yaml`

- [ ] **Step 1: Add tushare to requirements.txt**

Add at end of file:

```
tushare>=1.4.0
duckdb>=0.8.0
```

- [ ] **Step 2: Add tushare config section to config.yaml**

After the existing `data:` → `providers:` list, add `tushare:` config. The full `data:` section should become:

```yaml
data:
  tushare:
    token: ""
  providers:
    - yahoo
    - alpha_vantage
    - futu
    - tushare
  default_timeframe: 5m
  cache_enabled: true
  cache_ttl_minutes: 5
  symbol_registry:
    default_format: internal
```

- [ ] **Step 3: Commit**

```bash
git add quant/requirements.txt quant/shared/config/config.yaml
git commit -m "feat: add tushare dependency and config (vibe-kanban FKS-1694)"
```

---

### Task 2: Create TushareProvider

**Files:**
- Create: `quant/infrastructure/data/providers/tushare.py`

- [ ] **Step 1: Write TushareProvider implementation**

Create `quant/infrastructure/data/providers/tushare.py`:

```python
"""Tushare Pro data provider for China A-share stock and index daily bars.

Implements the DataProvider ABC interface, fetching data from Tushare Pro API
and caching all results in DuckDB via DuckDBStorage.
"""

import time
from datetime import datetime
from typing import Optional

import pandas as pd

from quant.infrastructure.data.providers.base import DataProvider
from quant.infrastructure.data.storage_duckdb import DuckDBStorage, _DEFAULT_DB
from quant.shared.utils.config_loader import ConfigLoader
from quant.shared.utils.logger import setup_logger

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    ts = None
    TUSHARE_AVAILABLE = False

_CN_INDEX_CODES = {
    "000001", "000016", "000300", "000905",
    "399001", "399006", "399673",
}


class TushareProvider(DataProvider):
    def __init__(self, db_path: str = _DEFAULT_DB, min_interval: float = 0.3):
        super().__init__("tushare")
        self._db_path = db_path
        self._min_interval = min_interval
        self._last_request_time = 0.0
        self._api = None
        self._storage: Optional[DuckDBStorage] = None
        self.logger = setup_logger("TushareProvider")

    def connect(self) -> None:
        if not TUSHARE_AVAILABLE:
            self.logger.warning("tushare not installed")
            self._connected = True
            return

        token = self._load_token()
        if not token:
            self.logger.warning("tushare token not configured")
            self._connected = True
            return

        ts.set_token(token)
        self._api = ts.pro_api()
        self._storage = DuckDBStorage(self._db_path)
        self._connected = True
        self.logger.info("TushareProvider connected")

    def disconnect(self) -> None:
        if self._storage:
            self._storage.close()
            self._storage = None
        self._api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _load_token(self) -> str:
        try:
            loader = ConfigLoader()
            return loader.get("config.yaml", "data", "tushare", "token", default="")
        except Exception:
            return ""

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    @staticmethod
    def _is_index(symbol: str) -> bool:
        return symbol in _CN_INDEX_CODES

    @staticmethod
    def _to_ts_code(symbol: str) -> str:
        if TushareProvider._is_index(symbol):
            if symbol.startswith("399"):
                return f"{symbol}.SZ"
            return f"{symbol}.SH"
        if symbol[0] in ("6", "9"):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    @staticmethod
    def _from_ts_code(ts_code: str) -> str:
        return ts_code.split(".")[0]

    def _fetch_daily(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        if self._api is None:
            return pd.DataFrame()

        ts_code = self._to_ts_code(symbol)
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        self._rate_limit()

        try:
            if self._is_index(symbol):
                df = self._api.index_daily(
                    ts_code=ts_code, start_date=start_str, end_date=end_str,
                )
            else:
                df = self._api.daily(
                    ts_code=ts_code, start_date=start_str, end_date=end_str,
                )
        except Exception as e:
            self.logger.warning(f"Error fetching bars for {symbol}: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        return self._normalize_bars(df, symbol)

    def _normalize_bars(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()

        col_mapping = {
            "trade_date": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "vol": "volume",
            "amount": "turnover",
        }

        df = df.rename(columns={k: v for k, v in col_mapping.items() if k in df.columns})

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y%m%d")

        df["symbol"] = symbol

        desired = ["timestamp", "symbol", "open", "high", "low", "close", "volume", "turnover"]
        available = [c for c in desired if c in df.columns]
        df = df[available]

        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=[c for c in ["open", "high", "low", "close"] if c in df.columns])
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        if not self._connected:
            self.connect()

        if timeframe not in ("1d", "day", "daily"):
            self.logger.warning(f"tushare only supports daily bars, got {timeframe}")
            return pd.DataFrame()

        if self._storage is not None:
            cached = self._storage.get_bars(symbol, start, end, timeframe)
            if not cached.empty:
                cached_start = cached["timestamp"].min().to_pydatetime()
                cached_end = cached["timestamp"].max().to_pydatetime()
                if cached_start <= start and cached_end >= end:
                    return cached

                missing_start = start
                missing_end = end
                if cached_start <= start:
                    missing_start = cached_end
                if cached_end >= end:
                    missing_end = cached_start

                if missing_start < missing_end:
                    fresh = self._fetch_daily(symbol, missing_start, missing_end)
                    if not fresh.empty and self._storage is not None:
                        self._storage.save_bars(fresh, timeframe)
                    if not cached.empty and not fresh.empty:
                        return pd.concat([cached, fresh]).drop_duplicates(
                            subset=["timestamp", "symbol"],
                        ).sort_values("timestamp").reset_index(drop=True)
                return cached

        fresh = self._fetch_daily(symbol, start, end)
        if not fresh.empty and self._storage is not None:
            self._storage.save_bars(fresh, timeframe)
        return fresh

    def get_quote(self, symbol: str) -> dict:
        if not self._connected:
            self.connect()

        if not TUSHARE_AVAILABLE or self._api is None:
            self.logger.error("tushare not available")
            return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}

        today = datetime.now()
        start = today.replace(year=today.year - 1)

        self._rate_limit()
        try:
            df = self.get_bars(symbol, start, today, "1d")
            if df.empty:
                return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}

            last = df.iloc[-1]
            price = float(last["close"])
            ts = last["timestamp"] if "timestamp" in df.columns else None
            return {
                "timestamp": ts,
                "symbol": symbol,
                "bid": price,
                "ask": price,
                "bid_size": 0,
                "ask_size": 0,
            }
        except Exception as e:
            self.logger.warning(f"Error fetching quote for {symbol}: {e}")
            return {"timestamp": None, "symbol": symbol, "bid": 0.0, "ask": 0.0, "bid_size": 0, "ask_size": 0}
```

- [ ] **Step 2: Commit**

```bash
git add quant/infrastructure/data/providers/tushare.py
git commit -m "feat: add TushareProvider with DuckDB cache (vibe-kanban FKS-1694)"
```

---

### Task 3: Wire up provider exports + symbol registry

**Files:**
- Modify: `quant/infrastructure/data/providers/__init__.py`
- Modify: `quant/infrastructure/data/symbol_registry.py`

- [ ] **Step 1: Update providers/__init__.py**

Full file content:

```python
"""Data providers package."""

from quant.infrastructure.data.providers.base import DataProvider
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.data.providers.akshare import AkshareProvider
from quant.infrastructure.data.providers.tushare import TushareProvider

__all__ = ["DataProvider", "DuckDBProvider", "AkshareProvider", "TushareProvider"]
```

- [ ] **Step 2: Update symbol_registry.py format_for_provider and normalize**

In `format_for_provider` method, change line with `elif provider.lower() in ("yahoo", "alpha_vantage", "akshare"):` to include `"tushare"`:

```python
        elif provider.lower() in ("yahoo", "alpha_vantage", "akshare", "tushare"):
```

In `normalize` method, change line with `elif source.lower() in ("yahoo", "alpha_vantage", "akshare"):` to include `"tushare"`:

```python
        elif source.lower() in ("yahoo", "alpha_vantage", "akshare", "tushare"):
```

- [ ] **Step 3: Commit**

```bash
git add quant/infrastructure/data/providers/__init__.py quant/infrastructure/data/symbol_registry.py
git commit -m "feat: wire TushareProvider into exports and registry (vibe-kanban FKS-1694)"
```

---

### Task 4: Create ingest_tushare CLI script

**Files:**
- Create: `quant/scripts/ingest_tushare.py`

- [ ] **Step 1: Write ingest script**

Create `quant/scripts/ingest_tushare.py`:

```python
"""CLI script to ingest China A-share daily data from Tushare into DuckDB.

Usage:
    python quant/scripts/ingest_tushare.py --symbol 600519 --start 2023-01-01 --end 2024-01-01
    python quant/scripts/ingest_tushare.py --symbol 000300 --start 2023-01-01 --end 2024-01-01
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

_pkg_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_pkg_dir.parent))

from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.shared.utils.logger import setup_logger

logger = setup_logger("ingest_tushare")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest A-share daily bars from Tushare into DuckDB")
    parser.add_argument("--symbol", required=True, help="A-share symbol, e.g. 600519, 000300")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--db-path", default=None, help="Path to DuckDB database")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    provider = TushareProvider(db_path=args.db_path) if args.db_path else TushareProvider()
    provider.connect()

    logger.info(f"Fetching {args.symbol} from {args.start} to {args.end}")
    df = provider.get_bars(args.symbol, start_dt, end_dt, timeframe="1d")

    if df.empty:
        logger.warning("No data returned. Exiting.")
        sys.exit(1)

    logger.info(f"Fetched {len(df)} rows")
    logger.info("Data cached in DuckDB")
    provider.disconnect()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add quant/scripts/ingest_tushare.py
git commit -m "feat: add Tushare ingest CLI script (vibe-kanban FKS-1694)"
```

---

### Task 5: Write tests

**Files:**
- Create: `quant/tests/test_tushare_provider.py`

- [ ] **Step 1: Write tests**

Create `quant/tests/test_tushare_provider.py`:

```python
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from quant.infrastructure.data.providers.tushare import TushareProvider, _CN_INDEX_CODES


def _make_tushare_stock_df(rows=10):
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="1D")
    return pd.DataFrame(
        {
            "ts_code": [f"600519.SH"] * rows,
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
            "ts_code": [f"000300.SH"] * rows,
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
    with patch.object(p, "_load_token", return_value="test_token"):
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
    assert TushareProvider._to_ts_code("000001") == "000001.SZ"


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
    cached_df = _make_tushare_stock_df(5)
    cached_df["timestamp"] = pd.to_datetime(cached_df["trade_date"], format="%Y%m%d")
    cached_df["symbol"] = "600519"

    provider._storage = MagicMock()
    provider._storage.get_bars.return_value = cached_df

    end = datetime.now()
    start = end - pd.Timedelta(days=5)
    df = provider.get_bars("600519", start, end, "1d")

    assert not df.empty
    provider._api.daily.assert_not_called()


def test_get_quote_returns_price(provider):
    bars_df = _make_tushare_stock_df(5)
    bars_df = bars_df.rename(columns={"trade_date": "timestamp"})
    bars_df["timestamp"] = pd.to_datetime(bars_df["timestamp"], format="%Y%m%d")
    bars_df["symbol"] = "600519"

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
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest quant/tests/test_tushare_provider.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add quant/tests/test_tushare_provider.py
git commit -m "test: add TushareProvider unit tests (vibe-kanban FKS-1694)"
```

---

### Task 6: Run full test suite + verify

- [ ] **Step 1: Run all tests**

```bash
python -m pytest quant/tests/ -q
```

Expected: All tests pass, no regressions.

- [ ] **Step 2: Verify import works**

```bash
python -c "from quant.infrastructure.data.providers.tushare import TushareProvider; print('OK')"
```

Expected: prints `OK`
