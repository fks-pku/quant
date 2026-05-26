"""基础设施测试 — EventBus, Portfolio, RiskEngine。"""
from datetime import datetime, date, timedelta

import pandas as pd
import pytest

from quant.infrastructure.events import EventBus
from quant.infrastructure.data.storage_duckdb import DuckDBStorage
from quant.infrastructure.data.providers.tushare import TushareProvider
from quant.domain.events.base import EventType, Event
from quant.features.trading.portfolio import Portfolio
from quant.domain.models.position import Position
from quant.domain.models.bar import Bar
from quant.domain.models.fill import Fill
from quant.domain.models.order import Order


class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.BAR, lambda e: received.append(e))
        event = Event(event_type=EventType.BAR, data={"symbol": "AAPL"})
        bus.publish(event)
        assert len(received) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        handler = lambda e: received.append(e)
        bus.subscribe(EventType.BAR, handler)
        bus.unsubscribe(EventType.BAR, handler)
        bus.publish(Event(event_type=EventType.BAR, data={}))
        assert len(received) == 0


class TestDuckDBStorage:
    def test_get_bars_for_symbols_reads_multiple_symbols(self, tmp_path):
        db_path = tmp_path / "bulk.duckdb"
        storage = DuckDBStorage(str(db_path))
        start = datetime(2025, 1, 2)
        try:
            for symbol, base in (("AAPL", 100.0), ("MSFT", 200.0)):
                rows = []
                for i in range(3):
                    price = base + i
                    rows.append({
                        "timestamp": start + timedelta(days=i),
                        "symbol": symbol,
                        "open": price,
                        "high": price + 1,
                        "low": price - 1,
                        "close": price,
                        "volume": 1000 + i,
                    })
                storage.save_bars(pd.DataFrame(rows), "1d")

            bars = storage.get_bars_for_symbols(
                ["MSFT", "AAPL"],
                start,
                start + timedelta(days=1),
                "1d",
            )
        finally:
            storage.close()

        assert len(bars) == 4
        assert sorted(bars["symbol"].unique().tolist()) == ["AAPL", "MSFT"]
        assert bars.groupby("symbol").size().to_dict() == {"AAPL": 2, "MSFT": 2}

    def test_get_bars_preserves_optional_cn_market_cap_columns(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        db_path = tmp_path / "market_cap.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE daily_cn_ochl (
                timestamp TIMESTAMP,
                symbol VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                turnover DOUBLE,
                adj_open DOUBLE,
                adj_high DOUBLE,
                adj_low DOUBLE,
                adj_close DOUBLE,
                adj_factor DOUBLE,
                total_mv DOUBLE,
                circ_mv DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO daily_cn_ochl VALUES
            ('2024-01-02', '600001', 10, 11, 9, 10.5, 1000, 10500, 10, 11, 9, 10.5, 1, 12345, 6789)
            """
        )
        conn.close()

        storage = DuckDBStorage(str(db_path), read_only=True, use_security_status=False)
        try:
            bars = storage.get_bars("600001", datetime(2024, 1, 2), datetime(2024, 1, 2), "1d")
            bulk = storage.get_bars_for_symbols(["600001"], datetime(2024, 1, 2), datetime(2024, 1, 2), "1d")
        finally:
            storage.close()

        assert bars["turnover"].iloc[0] == pytest.approx(10500)
        assert bars["total_mv"].iloc[0] == pytest.approx(12345)
        assert bars["circ_mv"].iloc[0] == pytest.approx(6789)
        assert bulk["total_mv"].iloc[0] == pytest.approx(12345)

    def test_cn_etf_and_index_bars_route_to_sidecars(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        stock_db = tmp_path / "cn_ohlcv.duckdb"
        etf_db = tmp_path / "cn_etf_ohlcv.duckdb"
        index_db = tmp_path / "cn_index_ohlcv.duckdb"
        start = datetime(2024, 1, 2)

        storage = DuckDBStorage(
            str(stock_db),
            etf_db_path=str(etf_db),
            index_db_path=str(index_db),
        )
        try:
            for symbol, close in (("600519", 100.0), ("510300", 3.5), ("000300", 3300.0)):
                storage.save_bars(
                    pd.DataFrame(
                        [
                            {
                                "timestamp": start,
                                "symbol": symbol,
                                "open": close,
                                "high": close,
                                "low": close,
                                "close": close,
                                "volume": 1000,
                            }
                        ]
                    ),
                    "1d",
                )
        finally:
            storage.close()

        conn = duckdb.connect(str(stock_db), read_only=True)
        try:
            assert conn.execute("SELECT symbol FROM daily_cn_ochl").fetchall() == [("600519",)]
        finally:
            conn.close()
        conn = duckdb.connect(str(etf_db), read_only=True)
        try:
            assert conn.execute("SELECT symbol FROM daily_cn_ochl").fetchall() == [("510300",)]
        finally:
            conn.close()
        conn = duckdb.connect(str(index_db), read_only=True)
        try:
            assert conn.execute("SELECT symbol FROM daily_cn_ochl").fetchall() == [("000300",)]
        finally:
            conn.close()

        storage = DuckDBStorage(
            str(stock_db),
            read_only=True,
            etf_db_path=str(etf_db),
            index_db_path=str(index_db),
        )
        try:
            bars = storage.get_bars_for_symbols(["600519", "510300", "000300"], start, start, "1d")
        finally:
            storage.close()

        assert set(bars["symbol"]) == {"600519", "510300", "000300"}

    def test_research_daily_provider_reads_index_sidecar_when_status_exists(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        from quant.api.research_bp import _DuckDBDailyDateProvider

        stock_db = tmp_path / "cn_ohlcv.duckdb"
        etf_db = tmp_path / "cn_etf_ohlcv.duckdb"
        index_db = tmp_path / "cn_index_ohlcv.duckdb"
        status_db = tmp_path / "cn_status.duckdb"
        start = datetime(2024, 1, 2)

        storage = DuckDBStorage(str(stock_db), etf_db_path=str(etf_db), index_db_path=str(index_db))
        try:
            storage.save_bars(pd.DataFrame([{
                "timestamp": start,
                "symbol": "600519",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1000,
            }]), "1d")
            storage.save_bars(pd.DataFrame([{
                "timestamp": start,
                "symbol": "000300",
                "open": 3300.0,
                "high": 3310.0,
                "low": 3290.0,
                "close": 3305.0,
                "volume": 123456,
            }]), "1d")
        finally:
            storage.close()

        conn = duckdb.connect(str(status_db))
        try:
            conn.execute(
                """
                CREATE TABLE cn_security_status_daily (
                    trade_date DATE,
                    symbol VARCHAR,
                    is_st BOOLEAN,
                    st_type VARCHAR,
                    is_suspended BOOLEAN,
                    has_daily_bar BOOLEAN,
                    tradable BOOLEAN,
                    up_limit DOUBLE,
                    down_limit DOUBLE,
                    pre_close DOUBLE,
                    is_listed BOOLEAN,
                    list_status VARCHAR,
                    suspend_type VARCHAR,
                    suspend_timing VARCHAR
                )
                """
            )
            conn.execute(
                """
                INSERT INTO cn_security_status_daily VALUES
                ('2024-01-02', '000300', false, '', false, false, true, NULL, NULL, 3200.0, true, 'L', '', '')
                """
            )
        finally:
            conn.close()

        provider = _DuckDBDailyDateProvider(
            ["000300"],
            start,
            start,
            db_path=str(stock_db),
            status_db_path=str(status_db),
            etf_db_path=str(etf_db),
            index_db_path=str(index_db),
            include_daily_basic=False,
            cache_enabled=False,
        )
        try:
            rows = provider.get_bars_for_date(start)
        finally:
            provider.close()

        assert len(rows) == 1
        assert rows[0]["symbol"] == "000300"
        assert rows[0]["high"] == pytest.approx(3310.0)
        assert rows[0]["low"] == pytest.approx(3290.0)
        assert rows[0]["close"] == pytest.approx(3305.0)
        assert rows[0]["volume"] == 123456

    def test_cn_lof_bars_route_to_fund_sidecar(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        stock_db = tmp_path / "cn_ohlcv.duckdb"
        fund_db = tmp_path / "cn_etf_ohlcv.duckdb"
        start = datetime(2024, 1, 2)

        storage = DuckDBStorage(str(stock_db), etf_db_path=str(fund_db))
        try:
            for symbol, close in (("160216", 1.5), ("501018", 0.8)):
                storage.save_bars(
                    pd.DataFrame(
                        [
                            {
                                "timestamp": start,
                                "symbol": symbol,
                                "open": close,
                                "high": close,
                                "low": close,
                                "close": close,
                                "volume": 1000,
                            }
                        ]
                    ),
                    "1d",
                )
        finally:
            storage.close()

        conn = duckdb.connect(str(stock_db), read_only=True)
        try:
            assert conn.execute("SELECT COUNT(*) FROM daily_cn_ochl").fetchone()[0] == 0
        finally:
            conn.close()
        conn = duckdb.connect(str(fund_db), read_only=True)
        try:
            assert set(row[0] for row in conn.execute("SELECT symbol FROM daily_cn_ochl").fetchall()) == {"160216", "501018"}
        finally:
            conn.close()

    def test_cn_fund_bars_can_join_nav_and_metadata_sidecars(self, tmp_path):
        stock_db = tmp_path / "cn_ohlcv.duckdb"
        fund_db = tmp_path / "cn_etf_ohlcv.duckdb"
        fund_meta_db = tmp_path / "cn_fund_meta.duckdb"
        fund_nav_db = tmp_path / "cn_fund_nav.duckdb"
        start = datetime(2024, 1, 2)

        storage = DuckDBStorage(
            str(stock_db),
            etf_db_path=str(fund_db),
            fund_meta_db_path=str(fund_meta_db),
            fund_nav_db_path=str(fund_nav_db),
        )
        try:
            storage.save_bars(
                pd.DataFrame(
                    [
                        {
                            "timestamp": start,
                            "symbol": "160216",
                            "open": 1.05,
                            "high": 1.06,
                            "low": 1.04,
                            "close": 1.05,
                            "volume": 1000,
                            "turnover": 1050,
                        }
                    ]
                ),
                "1d",
            )
            storage.save_cn_fund_instruments(
                pd.DataFrame(
                    [
                        {
                            "symbol": "160216",
                            "ts_code": "160216.SZ",
                            "name": "GT Commodity LOF",
                            "fund_type": "QDII",
                            "instrument_type": "LOF",
                            "status": "L",
                            "market": "E",
                            "list_date": "20150407",
                            "delist_date": "",
                            "index_code": "",
                            "index_name": "",
                        }
                    ]
                )
            )
            storage.save_cn_fund_nav(
                pd.DataFrame(
                    [
                        {
                            "symbol": "160216",
                            "nav_date": start,
                            "unit_nav": 1.00,
                            "accum_nav": 1.20,
                            "adj_nav": 1.25,
                            "net_asset": 10_000_000.0,
                            "total_netasset": 10_000_000.0,
                        }
                    ]
                )
            )
            bars = storage.get_bars("160216", start, start, "1d")
        finally:
            storage.close()

        assert bars["fund_name"].iloc[0] == "GT Commodity LOF"
        assert bars["instrument_type"].iloc[0] == "LOF"
        assert bars["unit_nav"].iloc[0] == pytest.approx(1.00)
        assert bars["premium_rate"].iloc[0] == pytest.approx(0.05)

    def test_tushare_provider_routes_bse_symbols_to_bj(self):
        assert TushareProvider._to_ts_code("830799") == "830799.BJ"
        assert TushareProvider._to_ts_code("920000") == "920000.BJ"
        assert TushareProvider._to_ts_code("600519") == "600519.SH"
        assert TushareProvider._to_ts_code("000001") == "000001.SZ"

    def test_tushare_provider_fetches_cn_etfs_from_fund_daily(self):
        class FakeApi:
            def __init__(self):
                self.calls = []

            def fund_daily(self, **kwargs):
                self.calls.append(("fund_daily", kwargs))
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "513100.SH",
                            "trade_date": "20240102",
                            "open": 1.0,
                            "high": 1.1,
                            "low": 0.9,
                            "close": 1.05,
                            "vol": 1000,
                            "amount": 1050,
                        }
                    ]
                )

            def daily(self, **kwargs):
                self.calls.append(("daily", kwargs))
                return pd.DataFrame()

            def index_daily(self, **kwargs):
                self.calls.append(("index_daily", kwargs))
                return pd.DataFrame()

        provider = TushareProvider(min_interval=0.0)
        provider._api = FakeApi()
        provider._connected = True

        frame = provider.fetch_daily_with_hfq("513100", datetime(2024, 1, 2), datetime(2024, 1, 3))

        assert provider._api.calls[0][0] == "fund_daily"
        assert frame["symbol"].iloc[0] == "513100"
        assert frame["adj_factor"].iloc[0] == pytest.approx(1.0)
        assert frame["adj_close"].iloc[0] == pytest.approx(1.05)

    def test_tushare_provider_fetches_cn_lofs_from_fund_daily_with_fund_adj(self):
        class FakeApi:
            def __init__(self):
                self.calls = []

            def fund_daily(self, **kwargs):
                self.calls.append(("fund_daily", kwargs))
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "160216.SZ",
                            "trade_date": "20240102",
                            "open": 1.0,
                            "high": 1.1,
                            "low": 0.9,
                            "close": 1.05,
                            "vol": 1000,
                            "amount": 1050,
                        }
                    ]
                )

            def fund_adj(self, **kwargs):
                self.calls.append(("fund_adj", kwargs))
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "160216.SZ",
                            "trade_date": "20240102",
                            "adj_factor": 2.0,
                        }
                    ]
                )

            def daily(self, **kwargs):
                self.calls.append(("daily", kwargs))
                return pd.DataFrame()

            def adj_factor(self, **kwargs):
                self.calls.append(("adj_factor", kwargs))
                return pd.DataFrame()

        provider = TushareProvider(min_interval=0.0)
        provider._api = FakeApi()
        provider._connected = True

        frame = provider.fetch_daily_with_hfq("160216", datetime(2024, 1, 2), datetime(2024, 1, 3))

        assert [call[0] for call in provider._api.calls[:2]] == ["fund_daily", "fund_adj"]
        assert frame["symbol"].iloc[0] == "160216"
        assert frame["adj_factor"].iloc[0] == pytest.approx(2.0)
        assert frame["adj_close"].iloc[0] == pytest.approx(2.10)

    def test_tushare_provider_preserves_full_fund_basic_lifecycle_fields(self):
        class FakeApi:
            def fund_basic(self, **kwargs):
                assert "delist_date" in kwargs["fields"]
                return pd.DataFrame(
                    [
                        {
                            "ts_code": "510999.SH",
                            "name": "Sample ETF",
                            "management": "Sample Fund",
                            "custodian": "Sample Bank",
                            "fund_type": "股票型",
                            "found_date": "20160101",
                            "due_date": "",
                            "list_date": "20160201",
                            "issue_date": "20160115",
                            "delist_date": "20200102",
                            "issue_amount": "10.5",
                            "m_fee": "0.5",
                            "c_fee": "0.1",
                            "duration_year": "",
                            "p_value": "1.0",
                            "min_amount": "",
                            "exp_return": "",
                            "benchmark": "中证红利指数",
                            "status": "D",
                            "invest_type": "被动指数型",
                            "type": "契约型开放式",
                            "trustee": "",
                            "purc_startdate": "20160301",
                            "redm_startdate": "20160301",
                            "market": "E",
                        }
                    ]
                )

        provider = TushareProvider(min_interval=0.0)
        provider._api = FakeApi()

        frame = provider.fetch_fund_basic(status="D")

        assert frame["symbol"].iloc[0] == "510999"
        assert frame["delist_date"].iloc[0] == "20200102"
        assert frame["management"].iloc[0] == "Sample Fund"
        assert frame["benchmark"].iloc[0] == "中证红利指数"

    def test_storage_saves_extended_fund_metadata_and_fund_nav_size(self, tmp_path):
        stock_db = tmp_path / "cn_ohlcv.duckdb"
        fund_db = tmp_path / "cn_etf_ohlcv.duckdb"
        fund_meta_db = tmp_path / "cn_fund_meta.duckdb"
        fund_nav_db = tmp_path / "cn_fund_nav.duckdb"
        start = datetime(2024, 1, 2)

        storage = DuckDBStorage(
            str(stock_db),
            etf_db_path=str(fund_db),
            fund_meta_db_path=str(fund_meta_db),
            fund_nav_db_path=str(fund_nav_db),
        )
        try:
            storage.save_bars(
                pd.DataFrame(
                    [
                        {
                            "timestamp": start,
                            "symbol": "510300",
                            "open": 4.0,
                            "high": 4.2,
                            "low": 3.9,
                            "close": 4.1,
                            "volume": 1000,
                            "turnover": 4100,
                        }
                    ]
                ),
                "1d",
            )
            storage.save_cn_fund_instruments(
                pd.DataFrame(
                    [
                        {
                            "symbol": "510300",
                            "ts_code": "510300.SH",
                            "name": "沪深300ETF",
                            "fund_type": "股票型",
                            "instrument_type": "ETF",
                            "status": "L",
                            "market": "E",
                            "list_date": "20120528",
                            "delist_date": "",
                            "index_code": "000300.SH",
                            "index_name": "沪深300指数",
                            "exchange": "SH",
                            "management": "华泰柏瑞基金",
                            "benchmark": "沪深300指数",
                            "m_fee": 0.5,
                        }
                    ]
                )
            )
            storage.save_cn_fund_nav(
                pd.DataFrame(
                    [
                        {
                            "symbol": "510300",
                            "nav_date": start,
                            "unit_nav": 4.0,
                            "accum_nav": 4.0,
                            "adj_nav": 4.0,
                            "total_netasset": 250.25,
                        }
                    ]
                )
            )
            bars = storage.get_bars("510300", start, start, "1d")
        finally:
            storage.close()

        assert bars["fund_name"].iloc[0] == "沪深300ETF"
        assert bars["management"].iloc[0] == "华泰柏瑞基金"
        assert bars["benchmark"].iloc[0] == "沪深300指数"
        assert bars["fund_category"].iloc[0] == "equity_cn_broad_csi300"
        assert bars["category_group"].iloc[0] == "csi300"
        assert bars["classification_version"].iloc[0] == "cn_fund_taxonomy_v1"
        assert bars["total_netasset"].iloc[0] == pytest.approx(250.25)
        assert bars["fund_size"].iloc[0] == pytest.approx(250.25)
        assert bars["premium_rate"].iloc[0] == pytest.approx(0.025)

    def test_get_bars_for_symbols_can_use_cn_security_status(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        db_path = tmp_path / "market.duckdb"
        status_path = tmp_path / "security_status.duckdb"
        start = datetime(2024, 1, 2)

        storage = DuckDBStorage(str(db_path))
        try:
            storage.save_bars(pd.DataFrame([
                {
                    "timestamp": start,
                    "symbol": "600519",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.0,
                    "volume": 1000,
                },
                {
                    "timestamp": datetime(2024, 1, 4),
                    "symbol": "600519",
                    "open": 10.2,
                    "high": 10.8,
                    "low": 10.1,
                    "close": 10.5,
                    "volume": 1200,
                },
            ]), "1d")
        finally:
            storage.close()

        conn = duckdb.connect(str(status_path))
        conn.execute(
            """
            CREATE TABLE cn_security_status_daily (
                symbol VARCHAR,
                trade_date DATE,
                is_st BOOLEAN,
                st_type VARCHAR,
                is_suspended BOOLEAN,
                has_daily_bar BOOLEAN,
                tradable BOOLEAN,
                up_limit DOUBLE,
                down_limit DOUBLE,
                pre_close DOUBLE,
                is_listed BOOLEAN,
                list_status VARCHAR,
                suspend_type VARCHAR,
                suspend_timing VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO cn_security_status_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600519", "2024-01-02", False, "", False, True, True, 11.0, 9.0, None, True, "L", "", ""),
                ("600519", "2024-01-03", True, "ST", True, False, False, 11.0, 9.0, None, True, "L", "S", ""),
                ("600519", "2024-01-04", False, "", False, True, True, 11.55, 9.45, 10.0, True, "L", "", ""),
            ],
        )
        conn.close()

        plain = DuckDBStorage(str(db_path), read_only=True)
        try:
            plain_bars = plain.get_bars_for_symbols(["600519"], start, datetime(2024, 1, 4), "1d")
        finally:
            plain.close()

        enriched = DuckDBStorage(
            str(db_path),
            read_only=True,
            use_security_status=True,
            status_db_path=str(status_path),
        )
        try:
            bars = enriched.get_bars_for_symbols(["600519"], start, datetime(2024, 1, 4), "1d")
        finally:
            enriched.close()

        assert len(plain_bars) == 2
        assert len(bars) == 3
        suspended = bars[pd.to_datetime(bars["timestamp"]).dt.date == date(2024, 1, 3)].iloc[0]
        assert bool(suspended["_suspended"]) is True
        assert bool(suspended["tradable"]) is False
        assert bool(suspended["is_st"]) is True
        assert suspended["st_type"] == "ST"
        assert suspended["suspend_type"] == "S"
        assert suspended["volume"] == 0
        assert suspended["close"] == pytest.approx(10.0)
        assert suspended["up_limit"] == pytest.approx(11.0)

    def test_multiple_subscribers(self):
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe(EventType.BAR, lambda e: r1.append(e))
        bus.subscribe(EventType.BAR, lambda e: r2.append(e))
        bus.publish(Event(event_type=EventType.BAR, data={}))
        assert len(r1) == 1
        assert len(r2) == 1

    def test_publish_nowait(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.BAR, lambda e: received.append(e))
        bus.publish_nowait(EventType.BAR, data={"symbol": "AAPL"})
        assert len(received) == 1

    def test_publish_multiple_event_types(self):
        bus = EventBus()
        r1, r2, r3 = [], [], []
        bus.subscribe(EventType.BAR, lambda e: r1.append(e))
        bus.subscribe(EventType.MARKET_OPEN, lambda e: r2.append(e))
        bus.subscribe(EventType.ORDER_SUBMITTED, lambda e: r3.append(e))
        bus.publish(Event(EventType.BAR, data={}))
        bus.publish(Event(EventType.MARKET_OPEN, data={}))
        bus.publish(Event(EventType.BAR, data={}))
        assert len(r1) == 2
        assert len(r2) == 1
        assert len(r3) == 0

    def test_unrelated_events_not_dispatched(self):
        bus = EventBus()
        received = []
        bus.subscribe(EventType.BAR, lambda e: received.append(e))
        bus.publish(Event(EventType.MARKET_CLOSE, data={}))
        bus.publish(Event(EventType.SYSTEM_START, data={}))
        assert len(received) == 0


class TestPortfolio:
    def test_initial_nav(self):
        p = Portfolio(initial_cash=100000)
        assert p.nav == 100000

    def test_update_position_buy(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        pos = p.get_position("AAPL")
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == pytest.approx(150.0, rel=1e-4)

    def test_update_position_sell(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, trade_date=date(2025, 1, 2))
        p.update_position("AAPL", quantity=-50, price=155.0, cost=0, trade_date=date(2025, 1, 3))
        pos = p.get_position("AAPL")
        assert pos.quantity == 50

    def test_close_position(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        pnl = p.close_position("AAPL", 160.0)
        assert pnl == pytest.approx(1000.0, rel=1e-4)
        pos = p.get_position("AAPL")
        assert pos.quantity == 0

    def test_nav_with_position(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        p.cash -= 15000.0
        assert p.nav == pytest.approx(100000.0, rel=1e-4)

    def test_get_all_positions(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        p.update_position("MSFT", quantity=50, price=400.0, cost=20000.0)
        all_pos = p.get_all_positions()
        assert len(all_pos) == 2

    def test_is_cn_symbol(self):
        assert Portfolio.is_cn_symbol("600519") is True
        assert Portfolio.is_cn_symbol("AAPL") is False
        assert Portfolio.is_cn_symbol("00700") is False

    def test_sector_exposure(self):
        p = Portfolio(initial_cash=100000)
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0, sector="Tech")
        p.update_position("JPM", quantity=50, price=200.0, cost=10000.0, sector="Finance")
        p.cash -= 25000.0
        exposure = p.get_sector_exposure()
        assert "Tech" in exposure
        assert "Finance" in exposure

    def test_check_daily_loss(self):
        p = Portfolio(initial_cash=100000)
        p._starting_nav = 100000
        p.cash = 94000
        assert p.check_daily_loss(0.05) is True

    def test_check_daily_loss_ok(self):
        p = Portfolio(initial_cash=100000)
        p._starting_nav = 100000
        assert p.check_daily_loss(0.05) is False

    def test_reset_daily(self):
        p = Portfolio(initial_cash=100000)
        p._starting_nav = 100000
        p.update_position("AAPL", quantity=100, price=150.0, cost=15000.0)
        p.cash -= 15000.0
        p.reset_daily()
        assert p.starting_nav == p.nav

    def test_to_dict(self):
        p = Portfolio(initial_cash=100000)
        d = p.to_dict()
        assert d["nav"] == 100000
        assert d["cash"] == 100000
        assert d["currency"] == "USD"


class TestPositionModel:
    def test_is_long(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.is_long is True

    def test_is_short(self):
        pos = Position(symbol="AAPL", quantity=-100, avg_cost=150.0)
        assert pos.is_short is True

    def test_cost_basis(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        assert pos.cost_basis == 15000.0

    def test_update_market_price(self):
        pos = Position(symbol="AAPL", quantity=100, avg_cost=150.0)
        pos.update_market_price(160.0)
        assert pos.market_value == 16000.0
        assert pos.unrealized_pnl == 1000.0


class TestBarModel:
    def test_valid_bar(self):
        bar = Bar(
            symbol="AAPL", timestamp=datetime(2025, 1, 2),
            open=150.0, high=155.0, low=148.0, close=152.0,
            volume=1000000,
        )
        assert bar.is_bullish is True
        assert bar.price_range == 7.0

    def test_invalid_bar_raises(self):
        with pytest.raises(ValueError):
            Bar(
                symbol="AAPL", timestamp=datetime(2025, 1, 2),
                open=150.0, high=140.0, low=148.0, close=152.0,
            )

    def test_typical_price(self):
        bar = Bar(
            symbol="AAPL", timestamp=datetime(2025, 1, 2),
            open=150.0, high=155.0, low=145.0, close=152.0,
        )
        assert bar.typical_price == pytest.approx((155 + 145 + 152) / 3, rel=1e-4)


class TestFillModel:
    def test_fill_creation(self):
        fill = Fill(
            order_id="o1", symbol="AAPL", side="BUY",
            quantity=100, price=150.0, commission=1.0,
            timestamp=datetime(2025, 1, 2),
        )
        assert fill.symbol == "AAPL"
        assert fill.quantity == 100


class TestOrderModel:
    def test_order_creation(self):
        order = Order(
            symbol="AAPL", quantity=100, side="BUY",
            order_type="MARKET",
        )
        assert order.symbol == "AAPL"
        assert order.side == "BUY"
