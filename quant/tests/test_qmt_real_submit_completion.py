import subprocess
import sys
from pathlib import Path

from quant.infrastructure.execution.strategy_state_store import StrategyStateStore


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_qmt_real_submit_completion_ignores_prior_execution_rows_with_same_signal_date(tmp_path):
    db_path = tmp_path / "strategy_dashboard.duckdb"
    store = StrategyStateStore(db_path)

    store.upsert_signal(signal={
        "signal_id": "old-submitted",
        "strategy_name": "ashare_broad_asset_etf_rotation",
        "mode": "live",
        "timestamp": "2026-06-12T11:19:29",
        "signal_date": "2026-06-12",
        "record_date": "2026-06-12",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100,
        "order_type": "LIMIT",
        "reference_price": 3.25,
        "status": "submitted",
        "order_id": "1099356543",
        "broker_order_id": "1099356543",
    })
    store.upsert_signal(signal={
        "signal_id": "due-pending",
        "strategy_name": "ashare_broad_asset_etf_rotation",
        "mode": "live",
        "timestamp": "2026-06-12T15:00:00",
        "signal_date": "2026-06-12",
        "submit_date": "2026-06-15",
        "record_date": "2026-06-12",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100,
        "order_type": "LIMIT",
        "reference_price": 3.303,
        "status": "accepted",
        "order_id": "D4393B16-7C8",
    })

    checker = REPO_ROOT / "quant" / "scripts" / "check_qmt_real_submit_completion.py"
    assert checker.exists()

    result = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--db",
            str(db_path),
            "--signal-date",
            "2026-06-12",
            "--execution-date",
            "2026-06-15",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "0"


def test_qmt_real_submit_completion_accepts_execution_date_submit_facts(tmp_path):
    db_path = tmp_path / "strategy_dashboard.duckdb"
    store = StrategyStateStore(db_path)
    store.upsert_signal(signal={
        "signal_id": "today-submitted",
        "strategy_name": "ashare_broad_asset_etf_rotation",
        "mode": "live",
        "timestamp": "2026-06-15T09:30:01",
        "signal_date": "2026-06-15",
        "record_date": "2026-06-15",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100,
        "order_type": "LIMIT",
        "reference_price": 3.29,
        "status": "submitted",
        "order_id": "1099999999",
        "broker_order_id": "1099999999",
    })

    checker = REPO_ROOT / "quant" / "scripts" / "check_qmt_real_submit_completion.py"
    assert checker.exists()

    result = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--db",
            str(db_path),
            "--signal-date",
            "2026-06-12",
            "--execution-date",
            "2026-06-15",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "1"


def test_qmt_real_submit_completion_accepts_split_order_ledger(tmp_path):
    db_path = tmp_path / "strategy_dashboard.duckdb"
    store = StrategyStateStore(db_path)
    store.upsert_signal(signal={
        "signal_id": "due-pending",
        "strategy_name": "ashare_broad_asset_etf_rotation",
        "mode": "live",
        "timestamp": "2026-06-12T15:00:00",
        "signal_date": "2026-06-12",
        "submit_date": "2026-06-15",
        "record_date": "2026-06-12",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100,
        "order_type": "MARKET",
        "reference_price": 3.303,
        "status": "accepted",
    })
    store.upsert_order(order={
        "signal_id": "due-pending",
        "strategy_name": "ashare_broad_asset_etf_rotation",
        "mode": "live",
        "timestamp": "2026-06-15T09:30:01",
        "signal_date": "2026-06-12",
        "submit_date": "2026-06-15",
        "record_date": "2026-06-15",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100,
        "order_type": "LIMIT",
        "price": 3.29,
        "status": "submitted",
        "order_id": "1099999999",
        "broker_order_id": "1099999999",
    })

    checker = REPO_ROOT / "quant" / "scripts" / "check_qmt_real_submit_completion.py"
    assert checker.exists()

    result = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--db",
            str(db_path),
            "--signal-date",
            "2026-06-12",
            "--execution-date",
            "2026-06-15",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "1"
