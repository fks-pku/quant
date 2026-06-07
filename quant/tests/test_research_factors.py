import numpy as np
import pandas as pd
import pytest

from quant.features.research.validation.ff_decomposition import decompose_alpha
from quant.infrastructure.research.factors.ff_factor_store import FFFactorStore


def test_decompose_alpha_populates_regression_values():
    dates = pd.date_range("2022-01-03", periods=160, freq="B")
    mkt = pd.Series(np.linspace(-0.01, 0.01, len(dates)), index=dates)
    returns = 0.002 + 0.5 * mkt
    factor_data = pd.DataFrame(
        {
            "MKT": mkt,
            "SMB": 0.0,
            "HML": 0.0,
            "RMW": 0.0,
            "CMA": 0.0,
            "RF": 0.0,
        },
        index=dates,
    )

    result = decompose_alpha(returns, factor_data)

    assert result["alpha_monthly"] == pytest.approx(0.002 * 21, rel=1e-6)
    assert result["tstat"] > 1.0
    assert 0.99 <= result["r2"] <= 1.0


def test_decompose_alpha_returns_barra_like_factor_attribution():
    dates = pd.date_range("2022-01-03", periods=180, freq="B")
    mkt = pd.Series(np.linspace(-0.012, 0.012, len(dates)), index=dates)
    size = pd.Series(np.sin(np.linspace(0, 12, len(dates))) * 0.01, index=dates)
    value = pd.Series(np.cos(np.linspace(0, 10, len(dates))) * 0.008, index=dates)
    mom = pd.Series(np.sin(np.linspace(0, 21, len(dates))) * 0.007, index=dates)
    rev = pd.Series(np.cos(np.linspace(0, 13, len(dates))) * 0.006, index=dates)
    vol = pd.Series(np.sin(np.linspace(0, 9, len(dates))) * 0.004, index=dates)
    liq = pd.Series(np.cos(np.linspace(0, 7, len(dates))) * 0.005, index=dates)
    returns = 0.001 + 0.70 * mkt + 0.45 * size - 0.25 * value + 0.15 * mom - 0.10 * rev + 0.08 * vol + 0.05 * liq
    factor_data = pd.DataFrame(
        {
            "MKT": mkt,
            "SIZE": size,
            "VALUE": value,
            "MOM": mom,
            "REV": rev,
            "VOL": vol,
            "LIQ": liq,
            "RF": 0.0,
        },
        index=dates,
    )

    result = decompose_alpha(returns, factor_data)
    attribution = result["factor_decomposition"]

    assert attribution["model"] == "barra_like_cn_style"
    assert attribution["status"] == "computed"
    assert attribution["observations"] == len(dates)
    assert attribution["alpha_annualized"] == pytest.approx(0.001 * 252, rel=1e-5)
    assert attribution["residual_vol_annualized"] < 1e-8
    assert attribution["factors"]["MKT"]["beta"] == pytest.approx(0.70, rel=1e-5)
    assert attribution["factors"]["SIZE"]["beta"] == pytest.approx(0.45, rel=1e-5)
    assert attribution["factors"]["VALUE"]["beta"] == pytest.approx(-0.25, rel=1e-5)
    assert attribution["factors"]["MOM"]["beta"] == pytest.approx(0.15, rel=1e-5)
    assert attribution["factors"]["REV"]["beta"] == pytest.approx(-0.10, rel=1e-5)
    assert attribution["factors"]["VOL"]["beta"] == pytest.approx(0.08, rel=1e-5)
    assert attribution["factors"]["LIQ"]["beta"] == pytest.approx(0.05, rel=1e-5)


def test_decompose_alpha_returns_zeros_for_short_history():
    dates = pd.date_range("2022-01-03", periods=60, freq="B")
    result = decompose_alpha(pd.Series([0.001] * 60, index=dates), pd.DataFrame({"MKT": 0.0}, index=dates))

    assert result == {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}


def test_decompose_alpha_returns_zeros_without_factor_columns():
    dates = pd.date_range("2022-01-03", periods=160, freq="B")
    result = decompose_alpha(
        pd.Series([0.001] * 160, index=dates),
        pd.DataFrame({"RF": 0.0}, index=dates),
    )

    assert result == {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}


def test_ff_factor_store_lists_known_factors():
    factors = FFFactorStore().list_factors()
    names = {factor["name"] for factor in factors}

    assert {"MKT", "SIZE", "VALUE", "MOM", "REV", "VOL", "LIQ", "RF"}.issubset(names)


def test_ff_factor_store_reads_cached_factors(tmp_path):
    dates = pd.date_range("2023-01-02", periods=5, freq="B")
    ff5 = pd.DataFrame(
        {
            "date": dates,
            "MKT": [0.1, 0.2, 0.3, 0.4, 0.5],
            "SMB": [0.0] * 5,
            "HML": [0.1] * 5,
            "RMW": [0.2] * 5,
            "CMA": [0.3] * 5,
            "RF": [0.01] * 5,
        }
    )
    momentum = pd.DataFrame({"date": dates, "Mom": [0.5, 0.4, 0.3, 0.2, 0.1]})
    ff5.to_parquet(tmp_path / "ff5_daily.parquet")
    momentum.to_parquet(tmp_path / "ff_momentum_daily.parquet")

    factors = FFFactorStore(cache_dir=tmp_path).get_factors(
        ["MKT", "HML", "Mom"],
        start="2023-01-03",
        end="2023-01-05",
    )

    assert list(factors.columns) == ["MKT", "HML", "Mom"]
    assert list(factors.index) == list(dates[1:4])


def test_ff_factor_store_reads_legacy_momentum_fallback(tmp_path):
    dates = pd.date_range("2023-01-02", periods=2, freq="B")
    pd.DataFrame({"date": dates, "Mom": [0.1, 0.2]}).to_parquet(tmp_path / "momentum_daily.parquet")

    factors = FFFactorStore(cache_dir=tmp_path).get_factors(["Mom"], "2023-01-01", "2023-01-31")

    assert list(factors.columns) == ["Mom"]


def test_ff_downloader_returns_cached_parquet_on_download_error(tmp_path, monkeypatch):
    from quant.infrastructure.research.factors import ff_downloader

    cached = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=2, freq="B"),
            "MKT": [0.01, 0.02],
            "SMB": [0.0, 0.0],
            "HML": [0.0, 0.0],
            "RMW": [0.0, 0.0],
            "CMA": [0.0, 0.0],
            "RF": [0.0, 0.0],
        }
    )
    cached.to_parquet(tmp_path / "ff5_daily.parquet")

    def fail_get(*args, **kwargs):
        raise RuntimeError("network disabled")

    monkeypatch.setattr(ff_downloader.requests, "get", fail_get)

    result = ff_downloader.download_ff5_daily(cache_dir=tmp_path)

    assert result is not None
    assert list(result["MKT"]) == [0.01, 0.02]


def test_ff_downloader_uses_fresh_momentum_cache_before_network(tmp_path, monkeypatch):
    from quant.infrastructure.research.factors import ff_downloader

    cached = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=2, freq="B"),
            "Mom": [0.03, 0.04],
        }
    )
    cached.to_parquet(tmp_path / "ff_momentum_daily.parquet")

    def fail_get(*args, **kwargs):
        raise AssertionError("network should not be called for fresh cache")

    monkeypatch.setattr(ff_downloader.requests, "get", fail_get)

    result = ff_downloader.download_momentum_daily(cache_dir=tmp_path)

    assert result is not None
    assert list(result["Mom"]) == [0.03, 0.04]


def test_cn_factor_builder_builds_cached_cn7_from_daily_cn_ochl_and_daily_basic(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    from quant.infrastructure.research.factors.cn_factor_builder import build_cn3_factors

    db_path = tmp_path / "market.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE daily_cn_ochl (symbol VARCHAR, date DATE, close DOUBLE, adj_close DOUBLE, volume DOUBLE, turnover DOUBLE)")
    rows = []
    dates = pd.date_range("2022-11-01", periods=90, freq="B")
    symbols = [f"00000{i}" for i in range(1, 9)]
    for i, symbol in enumerate(symbols):
        for day, date in enumerate(dates):
            close = 10.0 + i + day * (0.03 + i * 0.004)
            rows.append((symbol, date.date().isoformat(), close, close * 1.1, 1000.0 + i * 50.0, 10000.0 + i * 1000.0))
    conn.executemany("INSERT INTO daily_cn_ochl VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.close()

    basic_path = tmp_path / "daily_basic.duckdb"
    conn = duckdb.connect(str(basic_path))
    conn.execute(
        """
        CREATE TABLE cn_daily_basic (
            trade_date DATE,
            symbol VARCHAR,
            pb DOUBLE,
            total_mv DOUBLE,
            circ_mv DOUBLE,
            turnover_rate DOUBLE,
            turnover_rate_f DOUBLE
        )
        """
    )
    basic_rows = []
    for i, symbol in enumerate(symbols):
        for date in dates:
            basic_rows.append(
                (
                    date.date().isoformat(),
                    symbol,
                    0.8 + i * 0.25,
                    100000.0 + i * 50000.0,
                    80000.0 + i * 45000.0,
                    0.2 + i * 0.1,
                    0.3 + i * 0.1,
                )
            )
    conn.executemany("INSERT INTO cn_daily_basic VALUES (?, ?, ?, ?, ?, ?, ?)", basic_rows)
    conn.close()

    factors = build_cn3_factors(
        str(db_path),
        start="2023-02-01",
        end="2023-03-31",
        cache_dir=tmp_path,
        daily_basic_db_path=str(basic_path),
    )

    assert factors is not None
    assert {"date", "MKT", "SIZE", "VALUE", "MOM", "REV", "VOL", "LIQ", "RF"}.issubset(factors.columns)
    assert factors[["MKT", "SIZE", "VALUE", "MOM", "REV", "VOL", "LIQ"]].notna().all().all()
    assert pd.to_datetime(factors["date"]).min() >= pd.Timestamp("2023-02-01")
    assert (tmp_path / "cn7_daily.parquet").exists()


def test_cn_factor_builder_uses_fresh_cache_before_db(tmp_path):
    from quant.infrastructure.research.factors.cn_factor_builder import build_cn3_factors

    cached = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=2, freq="B"),
            "MKT": [0.01, 0.02],
            "SIZE": [0.0, 0.0],
            "VALUE": [0.0, 0.0],
            "MOM": [0.0, 0.0],
            "REV": [0.0, 0.0],
            "VOL": [0.0, 0.0],
            "LIQ": [0.0, 0.0],
            "RF": [0.0, 0.0],
        }
    )
    cached.to_parquet(tmp_path / "cn7_daily.parquet")

    factors = build_cn3_factors(
        str(tmp_path / "missing.duckdb"),
        start="2023-01-02",
        end="2023-01-03",
        cache_dir=tmp_path,
    )

    assert factors is not None
    assert list(factors["MKT"]) == [0.01, 0.02]


def test_cn_factor_builder_rebuilds_when_fresh_cache_does_not_cover_request(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")
    from quant.infrastructure.research.factors.cn_factor_builder import build_cn3_factors

    db_path = tmp_path / "market_wide.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE daily_cn_ochl (symbol VARCHAR, date DATE, close DOUBLE, turnover DOUBLE)")
    rows = []
    for i, symbol in enumerate(["000001", "000002", "000003", "000004"]):
        for day in range(6):
            close = 10.0 + i + day * (0.1 + i * 0.01)
            rows.append((symbol, f"2023-01-0{day + 2}", close, 1000.0 + i * 100.0))
    conn.executemany("INSERT INTO daily_cn_ochl VALUES (?, ?, ?, ?)", rows)
    conn.close()

    narrow = build_cn3_factors(
        str(db_path),
        start="2023-01-02",
        end="2023-01-04",
        cache_dir=tmp_path,
    )
    wide = build_cn3_factors(
        str(db_path),
        start="2023-01-02",
        end="2023-01-07",
        cache_dir=tmp_path,
    )

    assert narrow is not None
    assert wide is not None
    assert pd.to_datetime(wide["date"]).max() > pd.to_datetime(narrow["date"]).max()
    assert pd.to_datetime(wide["date"]).max() == pd.Timestamp("2023-01-07")
