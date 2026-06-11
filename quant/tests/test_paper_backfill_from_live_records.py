from pathlib import Path

import duckdb
import pytest

from quant.infrastructure.execution.live_recorder import LiveTradingRecorder
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
from quant.scripts.backfill_paper_from_live_records import backfill_paper_from_live_records


def test_backfill_paper_from_live_signals_uses_execution_open_and_skips_manual_orders(tmp_path):
    root = tmp_path
    live_dir = root / "quant" / "infrastructure" / "var" / "live_trading"
    etf_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"
    live_recorder = LiveTradingRecorder(live_dir)
    trading_date = "2026-06-03"
    _write_etf_bars(etf_db, [
        ("2026-06-03", "159949", 1.995, 2.017),
        ("2026-06-03", "518880", 9.28, 9.281),
    ])

    live_recorder.record_signal(
        timestamp=_ts("2026-06-03T09:31:22"),
        strategy_name="DemoStrategy",
        symbol="159949",
        side="BUY",
        quantity=4900,
        order_type="LIMIT",
        price=2.000985,
        status="accepted",
        order_id="SIG-1",
    )
    live_recorder.record_signal(
        timestamp=_ts("2026-06-03T09:31:23"),
        strategy_name="DemoStrategy",
        symbol="518880",
        side="BUY",
        quantity=1000,
        order_type="LIMIT",
        price=9.30784,
        status="accepted",
        order_id="SIG-2",
    )
    live_recorder.record_order(
        _order("MANUAL-RETRY", "518880", 1000, 9.307),
        broker_order_id="MANUAL-RETRY",
        status="submitted",
        timestamp=_ts("2026-06-03T09:47:00"),
    )

    result = backfill_paper_from_live_records(trading_date, root=root)

    paper_recorder = LiveTradingRecorder(root / "quant" / "infrastructure" / "var" / "paper_trading")
    paper_orders = paper_recorder.read_day("orders", trading_date)
    paper_fills = paper_recorder.read_day("fills", trading_date)
    assert result["signals"] == 2
    assert result["filled"] == 2
    assert [order["order_id"] for order in paper_orders] == ["PAPER-SIG-1", "PAPER-SIG-2"]
    assert [fill["price"] for fill in paper_fills] == pytest.approx([1.995, 9.28])
    assert "MANUAL-RETRY" not in {order["order_id"] for order in paper_orders}

    paper_day = root / "quant" / "infrastructure" / "var" / "paper_trading" / trading_date
    assert not list(paper_day.glob("*.json*")) if paper_day.exists() else True

    second = backfill_paper_from_live_records(trading_date, root=root)
    assert second["skipped"] == 2
    assert len(paper_recorder.read_day("orders", trading_date)) == 2

    forced = backfill_paper_from_live_records(trading_date, root=root, force=True)
    assert forced["written"] == 2
    assert len(paper_recorder.read_day("orders", trading_date)) == 2
    assert not (root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json").exists()
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    positions = {row["symbol"]: row for row in store.get_positions(strategy_name="DemoStrategy", mode="paper")}
    assert positions["159949"]["quantity"] == 4900
    assert positions["518880"]["quantity"] == 1000


def test_scheduled_scripts_replay_paper_after_post_close_data_update():
    root = Path(__file__).resolve().parents[1]
    update_script = (root / "scripts" / "update_cn_data_and_oss.ps1").read_text(encoding="utf-8")
    live_script = (root / "scripts" / "run_qmt_live_daily.ps1").read_text(encoding="utf-8")
    paper_script = (root / "scripts" / "run_paper_daily.ps1").read_text(encoding="utf-8")
    recovery_script = (root / "scripts" / "run_qmt_live_recovery.ps1").read_text(encoding="utf-8")

    assert "run_paper_daily.ps1" in update_script
    assert "paper replay command" in update_script
    assert "run_qmt_live_recovery.ps1" in update_script
    assert "live recovery command" in update_script
    assert "skip live pending because live recovery failed" in update_script
    assert "run_qmt_live_daily.ps1" in update_script
    assert "-PendingOnly" in update_script
    assert "paper signal command" in update_script
    assert "paper signal exit_code=" in update_script
    assert "-SignalOnly" in update_script
    assert "resolve_cn_trading_date.py" in update_script
    assert "resolve_cn_trading_date.py" in live_script
    assert "resolve_cn_trading_date.py" in paper_script
    assert "dryrun_update_cn_data_oss" in update_script
    assert "dryrun_qmt_live_daily" in live_script
    assert "dryrun_paper_daily" in paper_script
    assert "[switch]$SignalOnly" in paper_script
    assert "latest-data" in paper_script
    assert '"--pending-only"' in paper_script
    assert "CODEX_PAPER_COMPLETION_MODE" in paper_script
    assert 'mode == "signal"' in paper_script
    assert "_daily_replay_complete.json" not in paper_script
    assert "strategy_dashboard.duckdb" in paper_script
    assert "paper replay deferred to post-close data update" in live_script
    assert "--pending-only" in live_script
    assert "-not $PendingOnly -and -not $ConfirmRealOrders" in live_script
    assert "run_paper_daily.ps1" not in live_script
    assert "--recover-trades-only" in recovery_script
    assert "--simulate-daily" not in recovery_script
    assert "--pending-only" not in recovery_script
    assert "qmt live recovery exit_code=" in recovery_script
    assert "dryrun_qmt_live_recovery" in recovery_script


def _write_etf_bars(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            create table daily_cn_ochl (
                timestamp timestamp,
                symbol varchar,
                open double,
                close double
            )
            """
        )
        con.executemany("insert into daily_cn_ochl values (?, ?, ?, ?)", rows)
    finally:
        con.close()


def _ts(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def _order(order_id: str, symbol: str, quantity: float, price: float):
    from quant.domain.models.order import Order, OrderSide, OrderType

    return Order(
        symbol=symbol,
        quantity=quantity,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        order_id=order_id,
        price=price,
        strategy_name="DemoStrategy",
    )
