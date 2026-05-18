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

    def test_tushare_provider_routes_bse_symbols_to_bj(self):
        assert TushareProvider._to_ts_code("830799") == "830799.BJ"
        assert TushareProvider._to_ts_code("920000") == "920000.BJ"
        assert TushareProvider._to_ts_code("600519") == "600519.SH"
        assert TushareProvider._to_ts_code("000001") == "000001.SZ"

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
