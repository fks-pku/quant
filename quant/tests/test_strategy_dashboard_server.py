import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import duckdb
import pytest
import yaml

from quant.scripts.migrate_jsonl_to_duckdb import migrate_all
from quant.features.trading.dashboard_projection import project_pending_orders
import quant.scripts.strategy_dashboard_server as dashboard_server
from quant.scripts.strategy_dashboard_server import build_dashboard_payload, build_research_dashboard_payload, create_app
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
from quant.infrastructure.execution.strategy_controls import get_strategy_control


def _init_dashboard_state(root: Path, strategy_name: str, **modes):
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    for mode_key, cfg in modes.items():
        store.record_state(
            strategy_name=strategy_name, mode=mode_key,
            from_state=cfg.get("from_state", "stopped"),
            to_state=cfg.get("to_state", "running"),
            signal_enabled=cfg.get("signal_enabled", cfg.get("to_state") == "running"),
            submit_enabled=cfg.get("submit_enabled", cfg.get("to_state") == "running"),
            liquidation_requested=cfg.get("liquidation_requested", False),
            initial_cash=cfg.get("initial_cash", 0.0),
            note=cfg.get("note", ""),
        )


def test_strategy_dashboard_payload_reads_live_records_positions_and_controls(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-03"
    positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    paper_positions_path = root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json"
    report_path = root / "quant" / "features" / "strategies" / "DemoStrategy" / "full_research_report.html"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    shared_config = root / "quant" / "shared" / "config" / "config.yaml"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    positions_path.parent.mkdir(parents=True)
    report_path.parent.mkdir(parents=True)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    shared_config.parent.mkdir(parents=True, exist_ok=True)

    shared_config.write_text(
        yaml.safe_dump({"live_trading": {"strategy_initial_cash": 20000}}),
        encoding="utf-8",
    )
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 50000}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 30000}]}),
        encoding="utf-8",
    )
    report_path.write_text("<html>report</html>", encoding="utf-8")
    control_path.write_text(
        json.dumps({
            "strategies": {
                "DemoStrategy": {
                    "strategy_name": "DemoStrategy",
                    "live_enabled": True,
                    "live_state": "running",
                    "liquidation_requested": False,
                    "updated_at": "2026-06-03T09:31:00",
                }
            }
        }),
        encoding="utf-8",
    )
    paper_positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "600519": {
                        "symbol": "600519",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 9.99,
                        "market_value": 0.0,
                        "unrealized_pnl": 0.0,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"PAPER-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "600519": {
                        "symbol": "600519",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 10.0,
                        "market_value": 0.0,
                        "unrealized_pnl": 0.0,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"BRK-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "signals.jsonl",
        [
            {
                "timestamp": "2026-06-03T09:30:59",
                "strategy_name": "DemoStrategy",
                "order_id": "BRK-1",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "order_type": "LIMIT",
                "price": 10.02,
                "status": "accepted",
            },
            {
                "timestamp": "2026-06-03T15:01:00",
                "strategy_name": "DemoStrategy",
                "order_id": "CLIENT-2",
                "symbol": "000001",
                "side": "SELL",
                "quantity": 200,
                "order_type": "LIMIT",
                "price": None,
                "reference_price": 12.34,
                "execution_cost_bps": 25.0,
                "status": "accepted",
                "submit_date": date.today().isoformat(),
            },
        ],
    )
    _write_jsonl(
        live_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.02,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "price": 10.0,
            "commission": 1.23,
        }],
    )
    _write_jsonl(
        paper_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "broker_order_id": "PAPER-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.02,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        paper_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "price": 9.99,
            "commission": 0.5,
        }],
    )
    _write_jsonl(
        live_records / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "date": "2026-06-03",
            "strategy_name": "DemoStrategy",
            "nav": 120000.0,
            "cash": 30000.0,
            "market_value": 90000.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 100.0,
            "total_pnl": 100.0,
        }],
    )
    _write_daily_ohlc(stock_db, "600519", "2026-06-03", 10.0, 11.0)

    state_store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    state_store.record_state(
        strategy_name="DemoStrategy", mode="live",
        from_state="stopped", to_state="running",
        signal_enabled=True, submit_enabled=True,
        initial_cash=50000.0, note="test init",
        recorded_at="2026-06-03T09:31:00",
    )
    state_store.record_state(
        strategy_name="DemoStrategy", mode="paper",
        from_state="stopped", to_state="running",
        signal_enabled=True, submit_enabled=True,
        initial_cash=30000.0, note="test init",
        recorded_at="2026-06-03T09:31:00",
    )

    payload = build_dashboard_payload(root)

    strategy = payload["strategies"][0]
    assert strategy["name"] == "DemoStrategy"
    assert strategy["report_url"] == "/reports/DemoStrategy"
    assert strategy["live"]["configured"] is True
    assert strategy["live"]["accepts_signals"] == True
    assert strategy["live"]["control"]["live_state"] == "running"
    assert strategy["paper"]["control"]["mode"] == "paper"
    assert strategy["paper"]["control"]["live_state"] == "running"
    assert strategy["paper"]["accepts_signals"] is True
    assert strategy["live"]["records"]["orders"][0]["display_status"] == "filled"
    assert strategy["live"]["records"]["orders"][0]["limit_price"] == 10.02
    assert strategy["live"]["records"]["orders"][0]["fill_price"] == 10.0
    assert strategy["live"]["records"]["orders"][0]["raw_fill_price"] == 10.0
    assert strategy["live"]["records"]["orders"][0]["commission"] == 1.23
    assert strategy["live"]["records"]["orders"][0]["open_price"] == 10.0
    assert strategy["live"]["records"]["orders"][0]["slippage_bps"] == 0.0
    assert strategy["live"]["performance"]["total_commission"] == 1.23
    assert strategy["live"]["records"]["pending_orders"][0]["order_id"] == "CLIENT-2"
    assert strategy["live"]["records"]["pending_orders"][0]["signal_date"] == "2026-06-03"
    assert strategy["live"]["records"]["pending_orders"][0]["submit_date"] == date.today().isoformat()
    assert strategy["live"]["records"]["pending_orders"][0]["cost_bps_display"] == "+25.0 bps"
    assert strategy["live"]["records"]["pending_orders"][0]["display_status"] == "pending_submit"
    assert strategy["live"]["holdings"]["items"][0]["current_price"] == 11.0
    assert strategy["live"]["holdings"]["items"][0]["price_date"] == "2026-06-03"
    assert strategy["live"]["holdings"]["items"][0]["avg_cost"] == pytest.approx(10.0123)
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(98.77)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(48998.77)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(50098.77)
    assert strategy["live"]["performance"]["total_pnl"] == pytest.approx(98.77)
    assert strategy["live"]["performance"]["total_pnl_pct"] == pytest.approx(98.77 / 50000.0)
    assert strategy["live"]["performance"]["total_return"] == pytest.approx(98.77 / 50000.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(50098.77)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(48998.77)
    assert strategy["live"]["performance"]["slippage_sample_count"] == 1
    assert strategy["live"]["performance"]["median_slippage_bps"] == 0.0
    assert strategy["live"]["performance"]["weighted_avg_slippage_bps"] == 0.0
    assert strategy["paper"]["records"]["orders"][0]["display_status"] == "filled"
    assert strategy["paper"]["records"]["orders"][0]["limit_price"] == 10.02
    assert strategy["paper"]["records"]["orders"][0]["fill_price"] == 9.99
    assert strategy["paper"]["records"]["orders"][0]["raw_fill_price"] == 9.99
    assert strategy["paper"]["records"]["orders"][0]["commission"] == 0.5
    assert strategy["paper"]["records"]["orders"][0]["open_price"] == 10.0
    assert strategy["paper"]["records"]["orders"][0]["slippage_bps"] == pytest.approx(-10.0)
    assert strategy["paper"]["holdings"]["items"][0]["avg_cost"] == pytest.approx(9.995)
    assert strategy["paper"]["holdings"]["total_pnl"] == pytest.approx(100.5)
    assert strategy["paper"]["performance"]["total_pnl_pct"] == pytest.approx(100.5 / 30000.0)
    assert strategy["paper"]["performance"]["total_return"] == pytest.approx(100.5 / 30000.0)
    assert strategy["paper"]["performance"]["total_nav"] == pytest.approx(30100.5)
    assert strategy["paper"]["performance"]["cash"] == pytest.approx(29000.5)
    assert strategy["paper"]["performance"]["total_commission"] == 0.5
    assert strategy["paper"]["performance"]["median_slippage_bps"] == pytest.approx(-10.0)


def test_research_dashboard_payload_groups_ideas_by_source_and_links_strategy_files(tmp_path):
    root = tmp_path
    research_root = root / "quant" / "infrastructure" / "var" / "research"
    strategy_dir = root / "quant" / "features" / "strategies" / "demo_factor"
    research_root.mkdir(parents=True)
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("class DemoFactor: pass\n", encoding="utf-8")
    (strategy_dir / "full_research_report.html").write_text("<html>report</html>", encoding="utf-8")
    (research_root / "research_state.json").write_text(
        json.dumps(
            {
                "ideas": {
                    "idea-1": {
                        "idea_id": "idea-1",
                        "title": "Demo Factor",
                        "description": "Rank daily A-share stocks by a test factor.",
                        "source": "bigquant",
                        "source_url": "https://example.test/paper",
                        "authors": "Researcher",
                        "published_date": "2026-06",
                        "metadata": {
                            "discovery_quality": {
                                "score": 7.25,
                                "matched_terms": ["daily_ohlcv", "factor"],
                                "risk_flags": ["stale_source"],
                            }
                        },
                        "status": "candidate",
                        "reason": "passed fast gate",
                        "updated_at": "2026-06-18T01:00:00Z",
                    },
                    "idea-2": {
                        "idea_id": "idea-2",
                        "title": "Rejected Rotation",
                        "description": "A rejected public rotation idea.",
                        "source": "quantocracy",
                        "source_url": "https://example.test/blog",
                        "metadata": {"discovery_quality": {"score": 4.5}},
                        "status": "validation_failed",
                        "reason": "low after-cost Sharpe",
                        "updated_at": "2026-06-18T02:00:00Z",
                    },
                },
                "candidates": {
                    "demo_factor": {
                        "id": "demo_factor",
                        "name": "Demo Factor",
                        "status": "candidate",
                        "research_meta": {
                            "source": "bigquant",
                            "source_url": "https://example.test/paper",
                            "strategy_code_path": str(strategy_dir / "strategy.py"),
                            "discovery_quality": {"score": 7.5},
                        },
                    }
                },
                "hypotheses": {
                    "h1": {
                        "hypothesis_id": "h1",
                        "strategy_id": "demo_factor",
                        "title": "Demo Factor",
                        "status": "candidate",
                        "stage": "strict_backtest",
                        "source": "bigquant",
                        "source_url": "https://example.test/paper",
                        "decision_reason": "passed gate",
                        "metrics": {"rank_ic": 0.04},
                        "evidence": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = build_research_dashboard_payload(root)

    assert payload["total_ideas"] == 2
    assert "candidate" in payload["status_options"]
    assert "validation_failed" in payload["status_options"]
    assert [source["source"] for source in payload["sources"]] == ["arxiv", "bigquant", "jointquant", "quantocracy"]
    bigquant = next(source for source in payload["sources"] if source["source"] == "bigquant")
    assert bigquant["total"] == 1
    assert bigquant["counts"]["candidate"] == 1
    assert bigquant["passed"] == 1
    idea = bigquant["ideas"][0]
    assert idea["title"] == "Demo Factor"
    assert idea["status"] == "candidate"
    assert idea["passed"] is True
    assert idea["strategy_id"] == "demo_factor"
    assert idea["strategy_file_url"] == "/strategy-files/demo_factor"
    assert idea["report_url"] == "/reports/demo_factor"
    assert idea["discovery_score"] == pytest.approx(7.25)
    assert idea["matched_terms"] == ["daily_ohlcv", "factor"]
    assert idea["risk_flags"] == ["stale_source"]


def test_research_dashboard_routes_serve_payload_page_and_strategy_file(tmp_path):
    root = tmp_path
    research_root = root / "quant" / "infrastructure" / "var" / "research"
    strategy_dir = root / "quant" / "features" / "strategies" / "demo_factor"
    asset_dir = root / ".codex"
    research_root.mkdir(parents=True)
    strategy_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    (asset_dir / "research_dashboard.html").write_text("<html>research dashboard</html>", encoding="utf-8")
    (strategy_dir / "strategy.py").write_text("# strategy code\n", encoding="utf-8")
    (research_root / "research_state.json").write_text(
        json.dumps(
            {
                "ideas": {
                    "idea-1": {
                        "idea_id": "idea-1",
                        "title": "Demo Factor",
                        "source": "bigquant",
                        "source_url": "https://example.test/paper",
                        "status": "candidate",
                    }
                },
                "candidates": {
                    "demo_factor": {
                        "id": "demo_factor",
                        "name": "Demo Factor",
                        "status": "candidate",
                        "research_meta": {"source": "bigquant", "source_url": "https://example.test/paper"},
                    }
                },
                "hypotheses": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    client = create_app(root).test_client()

    page = client.get("/research")
    assert page.status_code == 200
    assert b"research dashboard" in page.data
    body = client.get("/api/research/dashboard").get_json()
    assert [source["source"] for source in body["sources"]] == ["arxiv", "bigquant", "jointquant", "quantocracy"]
    assert next(source for source in body["sources"] if source["source"] == "bigquant")["total"] == 1
    assert next(source for source in body["sources"] if source["source"] == "arxiv")["total"] == 0
    source_file = client.get("/strategy-files/demo_factor")
    assert source_file.status_code == 200
    assert b"# strategy code" in source_file.data


def test_strategy_dashboard_infers_live_slippage_before_daily_open_available(tmp_path):
    root = tmp_path
    strategy_name = "DemoStrategy"
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    strategy_dir = root / "quant" / "features" / "strategies" / strategy_name
    live_config.mkdir(parents=True)
    strategy_dir.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": strategy_name, "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    (strategy_dir / "full_research_report.html").write_text("<html>report</html>", encoding="utf-8")
    _init_dashboard_state(root, strategy_name, live={"initial_cash": 10000.0})
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_signal(signal={
        "signal_id": "sig:pending",
        "strategy_name": strategy_name,
        "mode": "live",
        "timestamp": "2026-06-15T15:00:00",
        "signal_date": "2026-06-15",
        "record_date": "2026-06-15",
        "submit_date": "2026-06-16",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 200,
        "order_type": "LIMIT",
        "reference_price": 10.0,
        "cost_bps": 20.0,
        "status": "accepted",
        "order_id": "CLIENT-1",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:filled",
        "strategy_name": strategy_name,
        "mode": "live",
        "timestamp": "2026-06-16T09:30:00",
        "signal_date": "2026-06-16",
        "record_date": "2026-06-16",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 200,
        "order_type": "LIMIT",
        "reference_price": 10.02,
        "status": "filled",
        "order_id": "BRK-1",
        "broker_order_id": "BRK-1",
        "fill_quantity": 200,
        "fill_price": 10.01,
        "commission": 5.0,
        "fill_time": "2026-06-16T09:30:06",
    })

    payload = build_dashboard_payload(root)

    strategy = next(item for item in payload["strategies"] if item["name"] == strategy_name)
    order = next(row for row in strategy["live"]["records"]["orders"] if row["order_id"] == "BRK-1")
    assert order["open_price"] == pytest.approx(10.0)
    assert order["slippage_bps"] == pytest.approx(10.0)
    assert strategy["live"]["performance"]["slippage_sample_count"] == 1
    assert strategy["live"]["performance"]["median_slippage_bps"] == pytest.approx(10.0)


def test_strategy_dashboard_reads_split_order_and_fill_ledgers(tmp_path):
    root = tmp_path
    strategy_name = "DemoStrategy"
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    strategy_dir = root / "quant" / "features" / "strategies" / strategy_name
    live_config.mkdir(parents=True)
    strategy_dir.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": strategy_name, "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    (strategy_dir / "full_research_report.html").write_text("<html>report</html>", encoding="utf-8")
    _init_dashboard_state(root, strategy_name, live={"initial_cash": 10000.0})
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    signal = store.upsert_signal(signal={
        "signal_id": "sig:split",
        "strategy_name": strategy_name,
        "mode": "live",
        "timestamp": "2026-06-15T15:00:00",
        "signal_date": "2026-06-15",
        "record_date": "2026-06-15",
        "submit_date": "2026-06-16",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 200,
        "order_type": "MARKET",
        "reference_price": 3.2,
        "status": "accepted",
    })
    order = store.upsert_order(order={
        "signal_id": signal["signal_id"],
        "strategy_name": strategy_name,
        "mode": "live",
        "timestamp": "2026-06-16T09:30:00",
        "signal_date": "2026-06-15",
        "submit_date": "2026-06-16",
        "record_date": "2026-06-16",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 200,
        "order_type": "LIMIT",
        "price": 3.21,
        "status": "submitted",
        "order_id": "CLIENT-1",
        "broker_order_id": "BRK-1",
        "execution_reference_price": 3.2,
    })
    store.upsert_fill(fill={
        "fill_id": "FILL-1",
        "order_row_id": order["order_row_id"],
        "signal_id": signal["signal_id"],
        "strategy_name": strategy_name,
        "mode": "live",
        "timestamp": "2026-06-16T09:31:00",
        "signal_date": "2026-06-15",
        "record_date": "2026-06-16",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 200,
        "price": 3.205,
        "commission": 5.0,
        "order_id": "CLIENT-1",
        "broker_order_id": "BRK-1",
    })

    payload = build_dashboard_payload(root)

    strategy = next(item for item in payload["strategies"] if item["name"] == strategy_name)
    live = strategy["live"]
    assert live["records"]["signals"][0]["signal_id"] == "sig:split"
    assert live["records"]["orders"][0]["order_id"] == "CLIENT-1"
    assert live["records"]["orders"][0]["filled_qty"] == pytest.approx(200.0)
    assert live["records"]["orders"][0]["fill_price"] == pytest.approx(3.205)
    assert live["records"]["orders"][0]["slippage_bps"] == pytest.approx(15.625)
    assert live["records"]["fills"][0]["fill_id"] == "FILL-1"


@pytest.mark.skip(reason="materialization removed in simplified state store")
def test_strategy_dashboard_materializes_and_reads_strategy_mode_records(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-03"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    control_path.write_text(
        json.dumps({
            "paper_strategies": {
                "DemoStrategy": {
                    "strategy_name": "DemoStrategy",
                    "mode": "paper",
                    "live_enabled": True,
                    "live_state": "paused",
                    "liquidation_requested": False,
                    "updated_at": "2026-06-03T09:30:00",
                }
            }
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "signals.jsonl",
        [
            {
                "timestamp": "2026-06-03T15:00:00",
                "strategy_name": "DemoStrategy",
                "order_id": "LIVE-1",
                "symbol": "600519",
                "side": "BUY",
                "quantity": 100,
                "order_type": "MARKET",
                "status": "accepted",
            },
            {
                "timestamp": "2026-06-03T15:00:00",
                "strategy_name": "OtherStrategy",
                "order_id": "LIVE-OTHER",
                "symbol": "000002",
                "side": "BUY",
                "quantity": 100,
                "order_type": "MARKET",
                "status": "accepted",
            },
        ],
    )
    _write_jsonl(
        paper_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "symbol": "000001",
            "side": "BUY",
            "quantity": 100,
            "order_type": "MARKET",
            "status": "accepted",
        }],
    )

    payload = build_dashboard_payload(root)
    strategy = next(item for item in payload["strategies"] if item["name"] == "DemoStrategy")

    assert (root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb").exists()
    assert [row["symbol"] for row in strategy["live"]["records"]["signals"]] == ["600519"]
    assert [row["symbol"] for row in strategy["paper"]["records"]["signals"]] == ["000001"]
    assert strategy["paper"]["control"]["live_state"] == "paused"
    assert (
        root
        / "quant"
        / "infrastructure"
        / "var"
        / "strategy_dashboard.duckdb"
    ).exists()


@pytest.mark.skip(reason="migration removed in simplified state store")
def test_strategy_dashboard_api_reads_strict_state_without_migration_writes(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "LIVE-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "MARKET",
            "status": "accepted",
        }],
    )
    build_dashboard_payload(root)
    state_path = root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb"
    before = state_path.stat().st_mtime_ns

    res = create_app(root).test_client().get("/api/dashboard")

    assert res.status_code == 200
    assert res.get_json()["record_dirs"]["strategy_state"].endswith("strategy_dashboard.duckdb")
    assert state_path.stat().st_mtime_ns == before


def test_strategy_dashboard_signal_table_hides_live_submit_attempts(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records_day = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    live_submit_day = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-09"
    live_config.mkdir(parents=True)
    live_records_day.mkdir(parents=True)
    live_submit_day.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records_day / "signals.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "SIGNAL-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": None,
            "execution_cost_bps": 25.0,
            "status": "accepted",
            "submit_date": "2026-06-09",
        }],
    )
    _write_jsonl(
        live_submit_day / "signals.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47.208000",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "execution_reference_price": 4.769,
            "status": "accepted",
        }],
    )
    _write_jsonl(
        live_submit_day / "orders.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47.208000",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_submit_day / "fills.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:48",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "price": 4.769,
            "commission": 5.0,
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert [row["timestamp"] for row in strategy["live"]["records"]["signals"]] == ["2026-06-08T15:00:00"]
    assert strategy["live"]["records"]["pending_orders"] == []
    assert strategy["live"]["records"]["orders"][0]["display_status"] == "filled"


def test_strategy_dashboard_cash_only_configured_strategy_uses_initial_cash(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    shared_config = root / "quant" / "shared" / "config" / "config.yaml"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    shared_config.parent.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    shared_config.write_text(
        yaml.safe_dump({"live_trading": {"strategy_initial_cash": 20000}}),
        encoding="utf-8",
    )
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "CashOnlyStrategy", "enabled": True, "allocation_cash": 15000}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "CashOnlyStrategy", "enabled": True, "allocation_cash": 25000}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "600519", "2026-06-04", 10.0, 11.0)

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["live"]["holdings"]["items"] == []
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(15000.0)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(15000.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(15000.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(15000.0)
    assert strategy["paper"]["holdings"]["cash"] == pytest.approx(25000.0)
    assert strategy["paper"]["performance"]["total_nav"] == pytest.approx(25000.0)


def test_strategy_dashboard_respects_explicit_zero_initial_cash(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    shared_config = root / "quant" / "shared" / "config" / "config.yaml"
    live_config.mkdir(parents=True)
    shared_config.parent.mkdir(parents=True)
    shared_config.write_text(
        yaml.safe_dump({"live_trading": {"strategy_initial_cash": 20000}}),
        encoding="utf-8",
    )
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "ZeroCashStrategy", "enabled": True, "initial_cash": 0.0}]}),
        encoding="utf-8",
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["live"]["initial_cash"] == pytest.approx(0.0)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(0.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(0.0)


def test_strategy_dashboard_cash_from_initial_less_current_holdings_value(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 10000.0}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "600519", "2026-06-11", 10.0, 11.0)
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_position(
        strategy_name="DemoStrategy",
        mode="live",
        symbol="600519",
        quantity=100.0,
        avg_cost=11.0,
        updated_at="2026-06-12T09:00:00",
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(1100.0)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(8900.0)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(8900.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(10000.0)


def test_strategy_dashboard_uses_db_position_qty_when_intraday_fill_exists(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000.0}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-11", 1.84, 1.85)
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_position(
        strategy_name="DemoStrategy",
        mode="live",
        symbol="159949",
        quantity=5300.0,
        avg_cost=1.8509,
        updated_at="2026-06-12T11:19:29",
    )
    store.upsert_signal(signal={
        "signal_id": "sig:accepted",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-12T11:19:29",
        "signal_date": "2026-06-12",
        "symbol": "159949",
        "side": "BUY",
        "quantity": 400.0,
        "order_type": "LIMIT",
        "reference_price": 1.902,
        "status": "accepted",
        "order_id": "CLIENT-1",
        "record_date": "2026-06-12",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:filled",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-12T11:19:29",
        "signal_date": "2026-06-12",
        "symbol": "159949",
        "side": "BUY",
        "quantity": 400.0,
        "order_type": "LIMIT",
        "reference_price": 1.902,
        "status": "filled",
        "order_id": "1099356531",
        "broker_order_id": "1099356531",
        "fill_quantity": 400.0,
        "fill_price": 1.874,
        "commission": 5.0,
        "fill_time": "2026-06-12T11:19:29",
        "record_date": "2026-06-12",
    })

    strategy = build_dashboard_payload(root)["strategies"][0]
    item = strategy["live"]["holdings"]["items"][0]

    assert item["qty"] == pytest.approx(5300.0)
    assert item["valuation_status"] == "unmarked_after_activity"
    expected_cost = 5300.0 * 1.8509
    expected_market_value = 5300.0 * 1.874
    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(expected_market_value)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(20000.0 - expected_cost)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(20000.0 - expected_cost + expected_market_value)
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(expected_market_value - expected_cost)


def test_strategy_dashboard_cash_uses_strategy_virtual_allocation_not_broker_total(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000.0}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-11", 1.848, 1.848)
    _write_daily_ohlc(stock_db, "518880", "2026-06-11", 8.506, 8.506)
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    base_159949_cost = 1494.0
    fill_159949_cost = 400.0 * 1.874 + 5.0
    qty_159949 = 1200.0
    store.upsert_position(
        strategy_name="DemoStrategy",
        mode="live",
        symbol="159949",
        quantity=qty_159949,
        avg_cost=(base_159949_cost + fill_159949_cost) / qty_159949,
        updated_at="2026-06-12T11:19:29",
    )
    base_518880_cost = 1000.0 * 8.506
    fill_518880_cost = 100.0 * 8.675 + 5.0
    qty_518880 = 1100.0
    store.upsert_position(
        strategy_name="DemoStrategy",
        mode="live",
        symbol="518880",
        quantity=qty_518880,
        avg_cost=(base_518880_cost + fill_518880_cost) / qty_518880,
        updated_at="2026-06-12T11:19:29",
    )
    for signal in [
        {
            "signal_id": "sig:159949",
            "symbol": "159949",
            "quantity": 400.0,
            "reference_price": 1.902,
            "order_id": "1099356531",
            "broker_order_id": "1099356531",
            "fill_quantity": 400.0,
            "fill_price": 1.874,
            "commission": 5.0,
        },
        {
            "signal_id": "sig:518880",
            "symbol": "518880",
            "quantity": 100.0,
            "reference_price": 8.675,
            "order_id": "1099356538",
            "broker_order_id": "1099356538",
            "fill_quantity": 100.0,
            "fill_price": 8.675,
            "commission": 5.0,
        },
    ]:
        store.upsert_signal(signal={
            "strategy_name": "DemoStrategy",
            "mode": "live",
            "timestamp": "2026-06-12T11:19:29",
            "signal_date": "2026-06-12",
            "side": "BUY",
            "order_type": "LIMIT",
            "status": "filled",
            "fill_time": "2026-06-12T11:19:29",
            "record_date": "2026-06-12",
            **signal,
        })

    strategy = build_dashboard_payload(root)["strategies"][0]
    items = {item["symbol"]: item for item in strategy["live"]["holdings"]["items"]}
    expected_market_value = 1200.0 * 1.874 + 1100.0 * 8.675
    expected_cash = 20000.0 - (base_159949_cost + fill_159949_cost + base_518880_cost + fill_518880_cost)

    assert items["159949"]["qty"] == pytest.approx(1200.0)
    assert items["518880"]["qty"] == pytest.approx(1100.0)
    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(expected_market_value)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(expected_cash)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(expected_cash)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(expected_cash + expected_market_value)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(expected_cash + expected_market_value)


def test_strategy_dashboard_surfaces_unassigned_broker_holdings_as_default_manual_strategy(tmp_path, monkeypatch):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_config.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={"to_state": "running", "initial_cash": 10000.0},
    )
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_position(
        strategy_name="DemoStrategy", mode="live", symbol="600519",
        quantity=100.0, avg_cost=10.0, updated_at="2026-06-12T09:30:00",
    )
    store.upsert_position(
        strategy_name="default", mode="live", symbol="600519",
        quantity=25.0, avg_cost=9.0, updated_at="2026-06-12T09:30:00",
    )
    store.upsert_position(
        strategy_name="OtherStrategy", mode="live", symbol="000002",
        quantity=100.0, avg_cost=5.0, updated_at="2026-06-12T09:30:00",
    )
    monkeypatch.setattr(
        "quant.scripts.strategy_dashboard_server._live_broker_position_snapshot",
        lambda _root: {
            "status": "ok",
            "source": "qmt",
            "generated_at": "2026-06-12T15:00:00",
            "error": "",
            "positions": [
                {"symbol": "600519", "quantity": 150.0, "avg_cost": 10.2, "market_value": 1800.0},
                {"symbol": "000001", "quantity": 200.0, "avg_cost": 4.0, "market_value": 1000.0},
                {"symbol": "000002", "quantity": 50.0, "avg_cost": 5.0, "market_value": 250.0},
            ],
        },
    )

    payload = build_dashboard_payload(root)

    default = next(strategy for strategy in payload["strategies"] if strategy["name"] == "default")
    by_symbol = {row["symbol"]: row for row in default["live"]["holdings"]["items"]}
    assert default["manual"] is True
    assert default["live"]["initial_cash"] == 0.0
    assert default["paper"]["initial_cash"] == 0.0
    assert set(by_symbol) == {"600519", "000001"}
    assert by_symbol["600519"]["qty"] == pytest.approx(50.0)
    assert by_symbol["600519"]["broker_qty"] == pytest.approx(150.0)
    assert by_symbol["600519"]["assigned_qty"] == pytest.approx(100.0)
    assert by_symbol["600519"]["market_value"] == pytest.approx(600.0)
    assert by_symbol["600519"]["unrealized_pnl"] == pytest.approx(90.0)
    assert by_symbol["000001"]["qty"] == pytest.approx(200.0)
    assert default["live"]["holdings"]["total_market_value"] == pytest.approx(1600.0)
    assert default["live"]["holdings"]["total_cost"] == pytest.approx(1310.0)
    assert default["live"]["holdings"]["nav"] == pytest.approx(1600.0)
    assert default["live"]["performance"]["total_nav"] == pytest.approx(1600.0)
    assert default["live"]["performance"]["total_return"] == pytest.approx(290.0 / 1310.0)


def test_live_broker_position_snapshot_uses_ttl_cache(tmp_path, monkeypatch):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    qmt_userdata = root / "qmt_userdata"
    live_config.mkdir(parents=True)
    qmt_userdata.mkdir(parents=True)
    (live_config / "brokers.yaml").write_text(
        yaml.safe_dump({"qmt": {"userdata_mini_path": str(qmt_userdata)}}),
        encoding="utf-8",
    )
    cache = getattr(dashboard_server, "_BROKER_POSITION_SNAPSHOT_CACHE", None)
    if cache is not None:
        cache.clear()
    calls = []

    def fake_subprocess_snapshot(_root):
        calls.append(_root)
        return {
            "status": "ok",
            "source": "qmt",
            "generated_at": f"call-{len(calls)}",
            "error": "",
            "positions": [{"symbol": "510880", "quantity": 200.0}],
        }

    monkeypatch.setattr(
        dashboard_server,
        "_live_broker_position_snapshot_subprocess",
        fake_subprocess_snapshot,
    )

    first = dashboard_server._live_broker_position_snapshot(root)
    second = dashboard_server._live_broker_position_snapshot(root)

    assert len(calls) == 1
    assert second == first
    assert second["generated_at"] == "call-1"


def test_strategy_dashboard_derives_live_curve_from_fills_when_snapshots_missing(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-03"
    positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    positions_path.parent.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "159949": {
                        "symbol": "159949",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 2.005,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"BRK-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 2.01,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "price": 2.0,
            "commission": 0.5,
        }],
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-02", 2.0, 2.0)
    _write_daily_ohlc(stock_db, "159949", "2026-06-03", 2.0, 2.01)
    _write_daily_ohlc(stock_db, "159949", "2026-06-04", 2.02, 2.03)

    for dt, nav_val, cash_val, mv in [
        ("2026-06-02", 20000.0, 20000.0, 0.0),
        ("2026-06-03", 20000 - 200.5 + 201.0, 20000 - 200.5, 201.0),
        ("2026-06-04", 20000 - 200.5 + 203.0, 20000 - 200.5, 203.0),
    ]:
        day_dir = root / "quant" / "infrastructure" / "var" / "live_trading" / dt
        day_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(
            day_dir / "snapshots.jsonl",
            [{
                "timestamp": f"{dt}T15:00:00",
                "date": dt,
                "strategy_name": "DemoStrategy",
                "nav": nav_val,
                "cash": cash_val,
                "market_value": mv,
                "realized_pnl": 0.0,
                "unrealized_pnl": mv - 200.5 if mv > 0 else 0.0,
                "total_pnl": nav_val - 20000.0,
            }],
        )

    strategy = build_dashboard_payload(root)["strategies"][0]
    curve = strategy["live"]["performance"]["pnl_curve"]

    assert [point["date"] for point in curve] == ["2026-06-02", "2026-06-03", "2026-06-04"]
    assert curve[0]["cash"] == pytest.approx(20000.0)
    assert curve[0]["nav"] == pytest.approx(20000.0)
    assert curve[1]["nav"] == pytest.approx(20000 - 200.5 + 201.0)
    assert curve[2]["nav"] == pytest.approx(20000 - 200.5 + 203.0)


def test_strategy_dashboard_does_not_publish_live_nav_after_latest_market_data(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records_prev = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    live_records_today = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-09"
    positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records_prev.mkdir(parents=True)
    live_records_today.mkdir(parents=True)
    positions_path.parent.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "510300": {
                        "symbol": "510300",
                        "strategy_name": "DemoStrategy",
                        "qty": 300.0,
                        "avg_cost": 4.7856666667,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"BRK-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records_prev / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "date": "2026-06-08",
            "strategy_name": "DemoStrategy",
            "nav": 10000.0,
            "cash": 10000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )
    _write_jsonl(
        live_records_today / "orders.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "broker_order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records_today / "fills.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:48",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-1",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "price": 4.769,
            "commission": 5.0,
        }],
    )
    _write_daily_ohlc(stock_db, "510300", "2026-06-08", 4.7, 4.739)

    strategy = build_dashboard_payload(root)["strategies"][0]
    curve = strategy["live"]["performance"]["pnl_curve"]

    assert strategy["live"]["records"]["orders"][0]["display_status"] == "filled"
    assert strategy["live"]["records"]["fills"][0]["timestamp"] == "2026-06-09T09:39:48"
    assert [row["date"] for row in curve] == ["2026-06-08"]
    assert strategy["live"]["performance"]["latest_snapshot"]["date"] == "2026-06-08"
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(9995.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(8564.3)
    assert strategy["live"]["performance"]["total_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["performance"]["unrealized_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["performance"]["total_return"] == pytest.approx(-5.0 / 10000.0)
    assert strategy["live"]["holdings"]["price_date"] is None
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(8564.3)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(9995.0)
    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(1430.7)
    assert strategy["live"]["holdings"]["total_cost"] == pytest.approx(1435.7)
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["holdings"]["items"][0]["valuation_status"] == "unmarked_after_activity"
    assert strategy["live"]["holdings"]["items"][0]["current_price"] == pytest.approx(4.769)
    assert strategy["live"]["holdings"]["items"][0]["price_source"] == "unmarked_fill_after_activity"
    assert strategy["live"]["holdings"]["items"][0]["unrealized_pnl"] == pytest.approx(-5.0)
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(
        10000.0
        - strategy["live"]["records"]["orders"][0]["filled_qty"] * strategy["live"]["records"]["orders"][0]["fill_price"]
        - strategy["live"]["records"]["orders"][0]["commission"]
    )
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(
        strategy["live"]["holdings"]["cash"] + strategy["live"]["holdings"]["total_market_value"]
    )
    assert strategy["live"]["holdings"]["total_pnl"] == pytest.approx(
        strategy["live"]["holdings"]["nav"] - 10000.0
    )


def test_strategy_dashboard_does_not_publish_position_baseline_curve_after_latest_market_data(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "GoldBarbell", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-11", 1.84, 1.85)
    _write_daily_ohlc(stock_db, "518880", "2026-06-11", 8.50, 8.50)
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_snapshot(
        strategy_name="GoldBarbell",
        mode="live",
        snapshot_date="2026-06-11",
        nav=20000.0,
        cash=10000.0,
        market_value=10000.0,
        source="live",
        recorded_at="2026-06-11T15:00:00",
    )
    store.upsert_position(
        strategy_name="GoldBarbell",
        mode="live",
        symbol="159949",
        quantity=1200.0,
        avg_cost=1.8738333333,
        updated_at="2026-06-12T14:34:25",
    )
    store.upsert_position(
        strategy_name="GoldBarbell",
        mode="live",
        symbol="518880",
        quantity=1100.0,
        avg_cost=8.5259090909,
        updated_at="2026-06-12T14:34:25",
    )
    store.upsert_signal(signal={
        "signal_id": "sig:gold-159949-20260612",
        "strategy_name": "GoldBarbell",
        "mode": "live",
        "timestamp": "2026-06-12T09:35:00",
        "signal_date": "2026-06-12",
        "record_date": "2026-06-12",
        "submit_date": "2026-06-12",
        "symbol": "159949",
        "side": "BUY",
        "quantity": 400.0,
        "order_type": "LIMIT",
        "reference_price": 1.874,
        "status": "filled",
        "order_id": "L-159949",
        "broker_order_id": "1099356531",
        "fill_quantity": 400.0,
        "fill_price": 1.874,
        "commission": 5.0,
        "fill_time": "2026-06-12T09:35:30",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:gold-518880-20260612",
        "strategy_name": "GoldBarbell",
        "mode": "live",
        "timestamp": "2026-06-12T09:36:00",
        "signal_date": "2026-06-12",
        "record_date": "2026-06-12",
        "submit_date": "2026-06-12",
        "symbol": "518880",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 8.675,
        "status": "filled",
        "order_id": "L-518880",
        "broker_order_id": "1099356538",
        "fill_quantity": 100.0,
        "fill_price": 8.675,
        "commission": 5.0,
        "fill_time": "2026-06-12T09:36:30",
    })

    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    curve = strategy["live"]["performance"]["pnl_curve"]

    assert payload["latest_market_data_date"] == "2026-06-11"
    assert strategy["live"]["holdings"]["cash_source"] == "position_baseline"
    assert strategy["live"]["holdings"]["price_date"] is None
    assert strategy["live"]["holdings"]["latest_activity_date"] == "2026-06-12"
    assert [point["date"] for point in curve] == ["2026-06-11"]
    assert strategy["live"]["performance"]["latest_snapshot"]["date"] == "2026-06-11"
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(strategy["live"]["holdings"]["nav"])


def test_strategy_dashboard_submitted_orders_without_fills_keep_cash_unchanged(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records_prev = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    live_records_today = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-09"
    live_config.mkdir(parents=True)
    live_records_prev.mkdir(parents=True)
    live_records_today.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 10000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        live_records_prev / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "date": "2026-06-08",
            "strategy_name": "DemoStrategy",
            "nav": 10000.0,
            "cash": 10000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )
    _write_jsonl(
        live_records_today / "orders.jsonl",
        [{
            "timestamp": "2026-06-09T09:39:47",
            "strategy_name": "DemoStrategy",
            "order_id": "BRK-UNFILLED",
            "broker_order_id": "BRK-UNFILLED",
            "symbol": "510300",
            "side": "BUY",
            "quantity": 300,
            "order_type": "LIMIT",
            "price": 4.769,
            "status": "submitted",
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["live"]["records"]["orders"][0]["display_status"] == "no_fill"
    assert strategy["live"]["records"]["orders"][0]["filled_qty"] == pytest.approx(0.0)
    assert strategy["live"]["holdings"]["items"] == []
    assert strategy["live"]["holdings"]["cash"] == pytest.approx(10000.0)
    assert strategy["live"]["holdings"]["total_market_value"] == pytest.approx(0.0)
    assert strategy["live"]["holdings"]["nav"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["cash"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["total_nav"] == pytest.approx(10000.0)
    assert strategy["live"]["performance"]["total_pnl"] == pytest.approx(0.0)


@pytest.mark.skip(reason="canonical snapshot materialization removed")
def test_strategy_dashboard_materializes_canonical_snapshot_when_legacy_snapshot_mismatches_initial_cash(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-03"
    paper_positions_path = root / "quant" / "infrastructure" / "var" / "paper_trading" / "strategy_positions.json"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    paper_config.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    paper_positions_path.parent.mkdir(parents=True, exist_ok=True)
    stock_db.parent.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    paper_positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "159949": {
                        "symbol": "159949",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 2.0,
                    }
                }
            },
            "realized_pnl": {"DemoStrategy": 0.0},
            "order_map": {"PAPER-1": "DemoStrategy"},
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        paper_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "broker_order_id": "PAPER-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 2.0,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        paper_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-03T09:31:01",
            "strategy_name": "DemoStrategy",
            "order_id": "PAPER-1",
            "symbol": "159949",
            "side": "BUY",
            "quantity": 100,
            "price": 2.0,
            "commission": 0.0,
        }],
    )
    _write_jsonl(
        paper_records / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-03T15:00:00",
            "date": "2026-06-03",
            "strategy_name": "DemoStrategy",
            "nav": 210.0,
            "cash": 0.0,
            "market_value": 210.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 10.0,
            "total_pnl": 10.0,
        }],
    )
    _write_daily_ohlc(stock_db, "159949", "2026-06-03", 2.0, 2.1)

    strategy = build_dashboard_payload(root)["strategies"][0]
    curve = strategy["paper"]["performance"]["pnl_curve"]

    assert curve[0]["date"] == "2026-06-02"
    assert curve[0]["cash"] == pytest.approx(20000.0)
    assert curve[0]["nav"] == pytest.approx(20000.0)
    assert curve[1]["date"] == "2026-06-03"
    assert curve[1]["cash"] == pytest.approx(19795.0)
    assert curve[1]["nav"] == pytest.approx(20005.0)
    assert strategy["paper"]["records"]["orders"][0]["commission"] == pytest.approx(5.0)
    assert strategy["paper"]["records"]["fills"][0]["commission"] == pytest.approx(5.0)
    assert strategy["paper"]["records"]["fills"][0]["commission_source"] == "estimated_paper_shared_model"
    assert strategy["paper"]["performance"]["total_commission"] == pytest.approx(5.0)
    assert canonical[0]["cash"] == pytest.approx(20000.0)
    assert canonical[0]["nav"] == pytest.approx(20000.0)
    assert canonical[-1]["cash"] == pytest.approx(19795.0)
    assert canonical[-1]["nav"] == pytest.approx(20005.0)


def test_strategy_dashboard_paper_reused_order_ids_match_fills_by_record_date(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    day1 = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-04"
    day2 = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-05"
    paper_config.mkdir(parents=True)
    day1.mkdir(parents=True)
    day2.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    for day, side, price in ((day1, "SELL", 2.017), (day2, "BUY", 1.989)):
        _write_jsonl(
            day / "orders.jsonl",
            [{
                "timestamp": f"{day.name}T09:31:00",
                "strategy_name": "DemoStrategy",
                "order_id": "PAPER_1",
                "broker_order_id": "PAPER_1",
                "symbol": "159949",
                "side": side,
                "quantity": 100,
                "order_type": "LIMIT",
                "price": price,
                "status": "submitted",
            }],
        )
        _write_jsonl(
            day / "fills.jsonl",
            [{
                "timestamp": f"{day.name}T09:31:01",
                "strategy_name": "DemoStrategy",
                "order_id": "PAPER_1",
                "symbol": "159949",
                "side": side,
                "quantity": 100,
                "price": price,
                "commission": 0.0,
            }],
        )

    strategy = build_dashboard_payload(root)["strategies"][0]
    orders = strategy["paper"]["records"]["orders"]
    fills = strategy["paper"]["records"]["fills"]

    assert [row["filled_qty"] for row in orders] == [pytest.approx(100.0), pytest.approx(100.0)]
    assert [row["commission"] for row in orders] == [pytest.approx(5.0), pytest.approx(5.0)]
    assert [row["commission"] for row in fills] == [pytest.approx(5.0), pytest.approx(5.0)]


def test_strategy_dashboard_does_not_mark_near_timestamp_order_as_pending(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    paper_records = root / "quant" / "infrastructure" / "var" / "paper_trading" / "2026-06-04"
    paper_config.mkdir(parents=True)
    paper_records.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_jsonl(
        paper_records / "signals.jsonl",
        [{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": 2.011998811939597,
            "quantity": 100,
            "side": "SELL",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-04T16:00:33.498762",
        }],
    )
    _write_jsonl(
        paper_records / "orders.jsonl",
        [{
            "broker_order_id": "PAPER_1",
            "order_id": "PAPER_1",
            "order_type": "LIMIT",
            "price": 2.011998811939597,
            "quantity": 100,
            "side": "SELL",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-04T16:00:33.498753",
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["paper"]["records"]["pending_orders"] == []


def test_strategy_dashboard_paper_due_signal_without_fill_displays_no_fill_order(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_etf_ohlcv.duckdb"
    paper_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "allocation_cash": 20000}]}),
        encoding="utf-8",
    )
    _write_daily_ohlc(stock_db, "518880", "2026-06-12", 8.55, 8.60)

    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_signal(signal={
        "signal_id": "sig:no-fill-paper",
        "strategy_name": "DemoStrategy",
        "mode": "paper",
        "timestamp": "2026-06-11T15:00:00",
        "signal_date": "2026-06-11",
        "symbol": "518880",
        "side": "BUY",
        "quantity": 1100.0,
        "order_type": "LIMIT",
        "reference_price": 8.506,
        "status": "accepted",
        "order_id": "SIGNAL-1",
        "submit_date": "2026-06-12",
        "record_date": "2026-06-11",
    })

    strategy = build_dashboard_payload(root)["strategies"][0]

    assert strategy["paper"]["records"]["pending_orders"] == []
    assert len(strategy["paper"]["records"]["orders"]) == 1
    order = strategy["paper"]["records"]["orders"][0]
    assert order["symbol"] == "518880"
    assert order["record_date"] == "2026-06-12"
    assert order["display_status"] == "no_fill"
    assert order["filled_qty"] == pytest.approx(0.0)


def test_strategy_dashboard_does_not_keep_d_close_signal_pending_after_execution_order(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": "CLIENT-1",
            "order_type": "LIMIT",
            "price": 2.9060032716214454,
            "quantity": 200,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510050",
            "timestamp": "2026-06-08T15:00:00",
            "projected_submit_date": "2026-06-09",
        }],
        orders=[{
            "broker_order_id": "BROKER-1",
            "order_id": "BROKER-1",
            "order_type": "LIMIT",
            "price": 2.9090084456355316,
            "quantity": 200,
            "side": "BUY",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "510050",
            "timestamp": "2026-06-09T09:39:47.176570",
        }],
        fills=[],
        as_of_date="2026-06-09",
    )

    assert pending == []


def test_strategy_dashboard_does_not_keep_retry_signal_pending_after_earlier_order(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": "CLIENT-RETRY",
            "order_type": "LIMIT",
            "price": 4.7690324738177505,
            "quantity": 300,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-09T09:50:49.780552",
            "projected_submit_date": "2026-06-09",
        }],
        orders=[{
            "broker_order_id": "BROKER-1",
            "order_id": "BROKER-1",
            "order_type": "LIMIT",
            "price": 4.769,
            "quantity": 300,
            "side": "BUY",
            "status": "submitted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-09T09:39:47.208000",
        }],
        fills=[],
        as_of_date="2026-06-09",
    )

    assert pending == []


def test_strategy_dashboard_marks_submit_failure_in_pending_actions(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": None,
            "order_type": "LIMIT",
            "price": 1.887081939845566,
            "quantity": 100,
            "reason": "risk_check_failed",
            "side": "BUY",
            "status": "rejected",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-09T09:39:47.176570",
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-09",
    )

    assert pending[0]["display_status"] == "failed"
    assert pending[0]["submit_date"] == "2026-06-09"
    assert pending[0]["reason"] == "risk_check_failed"


def test_strategy_dashboard_expires_past_submit_date_from_pending_orders(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": 2.011998811939597,
            "quantity": 100,
            "side": "SELL",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "159949",
            "timestamp": "2026-06-03T15:00:00",
            "projected_submit_date": "2026-06-04",
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-05",
    )

    assert pending == []


def test_strategy_dashboard_pending_orders_use_projected_submit_date(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": "SIGNAL-FRIDAY",
            "order_type": "LIMIT",
            "price": 10.0,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "600000",
            "timestamp": "2026-06-05T15:00:00",
            "projected_submit_date": "2026-06-08",
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-08",
    )

    assert pending[0]["signal_date"] == "2026-06-05"
    assert pending[0]["submit_date"] == "2026-06-08"


def test_strategy_dashboard_pending_orders_display_cost_bps_instead_of_limit(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": "SIGNAL-1",
            "order_type": "LIMIT",
            "price": None,
            "reference_price": 10.0,
            "execution_cost_bps": 25.0,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "600000",
            "timestamp": "2026-06-05T15:00:00",
            "projected_submit_date": "2026-06-08",
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-08",
    )

    assert pending[0]["cost_bps"] == pytest.approx(25.0)
    assert pending[0]["cost_bps_display"] == "+25.0 bps"
    assert pending[0]["price"] is None


def test_strategy_dashboard_pending_orders_backfill_submit_bps_from_legacy_limit_price(tmp_path):
    pending = project_pending_orders(
        signals=[{
            "order_id": "LEGACY-SIGNAL-1",
            "order_type": "LIMIT",
            "price": 10.025,
            "quantity": 100,
            "side": "BUY",
            "status": "accepted",
            "strategy_name": "DemoStrategy",
            "symbol": "510300",
            "timestamp": "2026-06-05T15:00:00",
            "projected_submit_date": "2026-06-08",
            "signal_close_price": 10.0,
        }],
        orders=[],
        fills=[],
        as_of_date="2026-06-08",
    )

    assert pending[0]["cost_bps"] == pytest.approx(25.0)
    assert pending[0]["cost_bps_display"] == "+25.0 bps"


def test_strategy_dashboard_renders_only_state_appropriate_mode_actions():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "function renderModeActions(configured, controlState, mode)" in html
    assert "controlState === 'stopped' || controlState === 'liquidating'" in html
    assert "controlState === 'running'" in html


def test_strategy_dashboard_nav_tile_labels_current_execution_state():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "navSourceText(perf, mode)" in html
    assert "current fills + cash" in html
    assert "total_nav_source === 'current_execution_state'" in html


def test_strategy_dashboard_pending_table_renders_submit_bps_column():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "['Signal Date', 'Submit Date', 'Symbol', 'Side', 'Qty', 'Type', 'Submit +bps', 'Status', 'Order ID', 'Reason']" in html
    assert "submitBps(row)" in html
    assert "value === 'failed'" in html
    assert "controlState === 'paused'" in html
    assert "not configured in ${escapeHtml(modeLabel(mode))}" in html
    assert "already started" in html
    assert "Initial ${initialCash}" in html
    assert "Initial Cash ${initialCashText(modeInitialCash(strategy, mode))}" in html
    assert "Total Return" in html
    assert "perf.total_return" in html
    assert "Total PnL %" not in html
    assert "perf.total_pnl_pct" not in html
    assert "function initialCashText(value)" in html
    assert "function modeInitialCash(strategy, mode)" in html
    assert "strategy?.initial_cash?.default" in html
    assert "dashboard_asset_version" in html
    assert "window.location.reload()" in html
    assert "locked-cash" not in html
    assert "function renderUnconfiguredStart" in html
    assert "data-initial-cash-input" in html
    assert "initial_cash" in html


def test_strategy_dashboard_uses_mode_subpages_with_shared_components():
    html = Path(".codex/strategy_dashboard.html").read_text(encoding="utf-8")

    assert "mode: initialMode()" in html
    assert "window.location.pathname" in html
    assert "window.history.pushState(null, '', `/${state.mode}${window.location.search}`)" in html
    assert "function renderModePage(strategy, mode)" in html
    assert "${renderModePage(strategy, mode)}" in html
    assert "function renderModeNavigation(strategy, activeMode)" in html
    assert "data-mode-tab=\"${mode}\"" in html
    assert "renderModeControl(strategy, mode)" in html
    assert "renderMetricsTable(strategy, mode)" in html
    assert "renderRunStatusBar(strategy, mode)" in html
    assert "Operations Health" not in html
    assert "renderOperationsHealth" not in html
    assert "Ops ${escapeHtml" not in html
    assert "Data Freshness" not in html
    assert "renderDataFreshness" not in html
    assert "run-status-timeline" in html
    assert "run-status-date-groups" in html
    assert "run-status-date-group" in html
    assert "run-status-date-label" in html
    assert "run-status-date-steps" in html
    assert ".run-status-date-group + .run-status-date-group" in html
    assert "border-left: 1px dashed" in html
    assert "function groupRunTimelineByDate(timeline)" in html
    assert "data-run-id=" in html
    assert "renderRunStatusTimeline(" in html
    assert "toggleRunDetail(" in html
    assert "renderRunCheckpointDetails(" in html
    assert "data-run-step=" in html
    assert "run-detail-card" in html
    assert "run-detail-line" in html
    assert "renderRunDetailLine(item.key, detail)" in html
    assert "信号 ${escapeHtml(formatRunQty(detail.signal_quantity))}" in html
    assert "提交 ${escapeHtml(formatRunQty(detail.submitted_quantity))}" in html
    assert "成交 ${escapeHtml(formatRunQty(detail.filled_quantity))}" in html
    assert "runActionText(item)" in html
    assert "runStatusClass(status)" in html
    assert "run-evidence-grid" not in html
    assert ".run-status-step.warning" in html
    assert "DATA_READY" in html
    assert "数据OK" in html
    assert "策略信号" in html
    assert "订单提交" in html
    assert "收盘OSS" not in html
    assert "EXECUTION_CONFIRMED" not in html
    assert "SNAPSHOT_WRITTEN" not in html
    assert "SUBMIT_READY" not in html
    assert "POSITION_SYNCED" not in html
    assert "COMPLETED" not in html
    assert "drawCurve(selected, state.mode)" in html
    assert "filterCurveByStart(" in html
    assert "alignSeriesDates(series)" in html
    assert "function alignSeriesDates(series)" in html
    assert "carried: true" in html
    assert "curveDateDomain(series)" in html
    assert "function curveX(dateText, domain, pad, width)" in html
    assert "const index = domain.dates.indexOf(dateKey)" in html
    assert "baseValue: strategyInitialCash" in html
    assert "explicitBase > 0 ? explicitBase : points[0].value" in html
    assert "width / 2" not in html
    assert "renderSplitTables" not in html
    assert "modeCard(" not in html


def test_strategy_dashboard_serves_live_and_paper_subpages(tmp_path):
    html_path = tmp_path / ".codex" / "strategy_dashboard.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text("<html><body>mode-tabs</body></html>", encoding="utf-8")
    client = create_app(tmp_path).test_client()

    root = client.get("/")
    live = client.get("/live")
    paper = client.get("/paper")
    dashboard = client.get("/api/dashboard")

    assert root.status_code == 200
    assert live.status_code == 200
    assert paper.status_code == 200
    assert dashboard.status_code == 200
    body = dashboard.get_json()
    assert body["dashboard_asset_version"] != "missing"
    assert "expected_market_data_date" in body["freshness"]
    assert set(body["scheduled_jobs"]) == {"data_update", "live_recovery", "live_pending", "paper_replay"}
    assert b"strategy_dashboard" in root.data or b"mode-tabs" in root.data
    assert b"strategy_dashboard" in live.data or b"mode-tabs" in live.data
    assert b"strategy_dashboard" in paper.data or b"mode-tabs" in paper.data


def test_strategy_dashboard_launcher_opens_current_port_and_restarts_stale_payload():
    script = Path("quant/scripts/open_strategy_dashboard.ps1").read_text(encoding="utf-8")
    shortcut = Path("策略管理看板.cmd").read_text(encoding="utf-8")

    assert "[int]$Port = 8791" in script
    assert "-Port 8791" in shortcut
    assert "$DashboardUrl = \"${Url}api/dashboard\"" in script
    assert "function Test-DashboardCompatible" in script
    assert "$Strategy.initial_cash" in script
    assert "$Strategy.live.initial_cash" in script
    assert "$Strategy.paper.initial_cash" in script
    assert "Get-NetTCPConnection" in script
    assert "Stop-DashboardOnPort" in script


def test_strategy_dashboard_surfaces_scheduled_job_failures(tmp_path):
    html_path = tmp_path / ".codex" / "strategy_dashboard.html"
    log_dir = tmp_path / "logs" / "scheduled"
    html_path.parent.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    html_path.write_text("<html><body>mode-tabs</body></html>", encoding="utf-8")
    (log_dir / "qmt_live_daily_20260604.log").write_text(
        "\n".join([
            "2026-06-04 16:00:12 qmt live daily start",
            "RuntimeError: No daily bars loaded for 2026-06-04",
            "2026-06-04 16:00:15 qmt live daily exit_code=1 paper_exit_code=0",
        ]),
        encoding="utf-16",
    )

    body = create_app(tmp_path).test_client().get("/api/dashboard").get_json()

    live_job = body["scheduled_jobs"]["live_pending"]
    assert live_job["status"] == "failed"
    assert live_job["exit_code"] == 1
    assert live_job["error"] == "RuntimeError: No daily bars loaded for 2026-06-04"


def test_strategy_dashboard_surfaces_live_recovery_job_status(tmp_path):
    html_path = tmp_path / ".codex" / "strategy_dashboard.html"
    log_dir = tmp_path / "logs" / "scheduled"
    html_path.parent.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    html_path.write_text("<html><body>mode-tabs</body></html>", encoding="utf-8")
    (log_dir / "qmt_live_recovery_20260605.log").write_text(
        "\n".join([
            "2026-06-05 23:05:00 qmt live recovery start read_only=true dry_run=False",
            "2026-06-05 23:05:02 qmt live recovery exit_code=0",
        ]),
        encoding="utf-8",
    )

    body = create_app(tmp_path).test_client().get("/api/dashboard").get_json()

    recovery_job = body["scheduled_jobs"]["live_recovery"]
    assert recovery_job["status"] == "ok"
    assert recovery_job["exit_code"] == 0


def test_strategy_dashboard_allocation_endpoint_rejects_configured_cash_change(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_config.mkdir(parents=True)
    config_path = live_config / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )

    app = create_app(root)
    res = app.test_client().post(
        "/api/strategies/DemoStrategy/allocation",
        json={"initial_cash": 35000},
    )

    assert res.status_code == 409
    body = res.get_json()
    assert body["allocation_locked"] is True
    assert body["current_allocation"]["live"]["initial_cash"] == pytest.approx(20000.0)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["strategies"][0]["initial_cash"] == pytest.approx(20000.0)
    assert "allocation_cash" not in config["strategies"][0]
    payload = build_dashboard_payload(root)
    assert payload["strategies"][0]["live"]["initial_cash"] == pytest.approx(20000.0)


def test_strategy_dashboard_start_unconfigured_mode_assigns_initial_cash(tmp_path):
    root = tmp_path
    strategy_dir = root / "quant" / "features" / "strategies" / "DemoStrategy"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    strategy_dir.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("class DemoStrategy: pass\n", encoding="utf-8")
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": []}),
        encoding="utf-8",
    )

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "start", "mode": "paper", "initial_cash": 25000},
    )

    assert res.status_code == 200
    body = res.get_json()["control"]
    assert body["mode"] == "paper"
    assert body["live_state"] == "running"
    config = yaml.safe_load((paper_config / "config.yaml").read_text(encoding="utf-8"))
    assert config["strategies"] == [{"name": "DemoStrategy", "enabled": True, "initial_cash": 25000.0}]
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["paper"]["configured"] is True
    assert strategy["paper"]["initial_cash"] == pytest.approx(25000.0)
    assert strategy["paper"]["control"]["live_state"] == "running"


def test_strategy_dashboard_start_unconfigured_mode_requires_initial_cash(tmp_path):
    root = tmp_path
    strategy_dir = root / "quant" / "features" / "strategies" / "DemoStrategy"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("class DemoStrategy: pass\n", encoding="utf-8")

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "start", "mode": "live"},
    )

    assert res.status_code == 400
    assert "initial_cash" in res.get_json()["error"]


def test_strategy_dashboard_control_endpoint_updates_modes_independently(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    live_config.mkdir(parents=True)
    paper_config.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    app = create_app(root)
    client = app.test_client()

    live_res = client.post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "pause", "mode": "live"},
    )
    paper_res = client.post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "resume", "mode": "paper"},
    )

    assert live_res.status_code == 200
    assert paper_res.status_code == 200
    control_live = get_strategy_control("DemoStrategy", root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb", mode="live")
    control_paper = get_strategy_control("DemoStrategy", root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb", mode="paper")
    assert control_live.mode == "live"
    assert control_live.live_state == "paused"
    assert control_paper.mode == "paper"
    assert control_paper.live_state == "running"
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["live"]["accepts_signals"] is False
    assert strategy["paper"]["accepts_signals"] is True
    assert strategy["live"]["ledger"]["mode"] == "live"
    assert strategy["paper"]["ledger"]["mode"] == "paper"
    assert payload["operations_health"]["status"] in {"ok", "warning"}


def test_strategy_dashboard_start_action_enables_paper_mode(tmp_path):
    root = tmp_path
    paper_config = root / "quant" / "infrastructure" / "var" / "paper_config"
    control_path = root / "quant" / "infrastructure" / "var" / "strategy_controls.json"
    paper_config.mkdir(parents=True)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    (paper_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    control_path.write_text(
        json.dumps({
            "paper_strategies": {
                "DemoStrategy": {
                    "strategy_name": "DemoStrategy",
                    "mode": "paper",
                    "live_enabled": False,
                    "live_state": "stopped",
                    "liquidation_requested": False,
                    "updated_at": "2026-06-04T15:00:00",
                }
            }
        }),
        encoding="utf-8",
    )

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "start", "mode": "paper"},
    )

    assert res.status_code == 200
    body = res.get_json()["control"]
    assert body["mode"] == "paper"
    assert body["live_state"] == "running"
    assert body["live_enabled"] is True
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["paper"]["accepts_signals"] is True
    assert strategy["paper"]["control"]["live_state"] == "running"


def test_strategy_dashboard_liquidate_stop_creates_mode_scoped_plan(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_positions_path = root / "quant" / "features" / "data" / "strategy_positions.json"
    live_config.mkdir(parents=True)
    live_positions_path.parent.mkdir(parents=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True}]}),
        encoding="utf-8",
    )
    live_positions_path.write_text(
        json.dumps({
            "positions": {
                "DemoStrategy": {
                    "600519": {
                        "symbol": "600519",
                        "strategy_name": "DemoStrategy",
                        "qty": 100.0,
                        "avg_cost": 10.0,
                        "market_value": 1100.0,
                        "unrealized_pnl": 100.0,
                    }
                }
            },
            "realized_pnl": {},
            "order_map": {},
        }),
        encoding="utf-8",
    )
    state_store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    state_store.upsert_position(
        strategy_name="DemoStrategy", mode="live", symbol="600519",
        quantity=100.0, avg_cost=10.0, realized_pnl=0.0,
    )

    res = create_app(root).test_client().post(
        "/api/strategies/DemoStrategy/control",
        json={"action": "liquidate_stop", "mode": "live", "note": "user requested clear"},
    )

    assert res.status_code == 200
    plan = res.get_json()["liquidation_plan"]
    assert plan["mode"] == "live"
    assert plan["strategy_name"] == "DemoStrategy"
    assert plan["orders"] == [{
        "symbol": "600519",
        "side": "SELL",
        "quantity": 100.0,
        "avg_cost": 10.0,
    }]
    payload = build_dashboard_payload(root)
    strategy = payload["strategies"][0]
    assert strategy["live"]["control"]["live_state"] == "liquidating"
    assert strategy["live"]["liquidation_plan"]["status"] == "liquidating"


def test_strategy_dashboard_builds_recent_three_day_run_status_bar(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={
            "to_state": "running",
            "signal_enabled": True,
            "submit_enabled": True,
            "initial_cash": 20000.0,
        },
    )
    for trading_date in ("2026-06-03", "2026-06-04", "2026-06-05"):
        _write_daily_ohlc(stock_db, "600519", trading_date, 10.0, 10.5)
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    store.upsert_signal(signal={
        "signal_id": "sig:0603-close",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-03T15:00:00",
        "signal_date": "2026-06-03",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 10.0,
        "status": "accepted",
        "order_id": "CLIENT-0603",
        "record_date": "2026-06-03",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:0604-fill",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-04T09:31:00",
        "signal_date": "2026-06-04",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 10.0,
        "status": "filled",
        "order_id": "BROKER-0604",
        "broker_order_id": "BROKER-0604",
        "fill_quantity": 100.0,
        "fill_price": 10.0,
        "commission": 1.0,
        "fill_time": "2026-06-04T09:31:05",
        "record_date": "2026-06-04",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:0604-close",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-04T15:00:00",
        "signal_date": "2026-06-04",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 10.0,
        "status": "accepted",
        "order_id": "CLIENT-0604",
        "record_date": "2026-06-04",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:0605-order",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-05T09:31:00",
        "signal_date": "2026-06-05",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 10.0,
        "status": "submitted",
        "order_id": "BROKER-0605",
        "broker_order_id": "BROKER-0605",
        "record_date": "2026-06-05",
    })
    store.upsert_signal(signal={
        "signal_id": "sig:0605-close",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-05T15:00:00",
        "signal_date": "2026-06-05",
        "symbol": "600519",
        "side": "BUY",
        "quantity": 200.0,
        "order_type": "LIMIT",
        "reference_price": 10.0,
        "status": "accepted",
        "order_id": "CLIENT-0605",
        "record_date": "2026-06-05",
    })

    strategy = build_dashboard_payload(root)["strategies"][0]
    status_bar = strategy["live"]["run_status_bar"]

    assert [item["label"] for item in status_bar["timeline"]] == [
        "06-03 数据OK",
        "06-03 策略信号",
        "06-04 提交订单",
        "06-04 数据OK",
        "06-04 策略信号",
        "06-05 提交订单",
        "06-05 数据OK",
        "06-05 策略信号",
    ]
    assert [(item["date"], item["key"]) for item in status_bar["timeline"]] == [
        ("2026-06-03", "DATA_READY"),
        ("2026-06-03", "SIGNAL_READY"),
        ("2026-06-04", "ORDER_SUBMITTED"),
        ("2026-06-04", "DATA_READY"),
        ("2026-06-04", "SIGNAL_READY"),
        ("2026-06-05", "ORDER_SUBMITTED"),
        ("2026-06-05", "DATA_READY"),
        ("2026-06-05", "SIGNAL_READY"),
    ]
    assert status_bar["timeline"][2]["signal_date"] == "2026-06-03"
    assert status_bar["timeline"][2]["details"][0]["submitted_quantity"] == 100.0
    assert status_bar["timeline"][2]["details"][0]["filled_quantity"] == 100.0
    assert status_bar["timeline"][5]["signal_date"] == "2026-06-04"
    assert status_bar["timeline"][5]["status"] == "blocked"
    assert status_bar["timeline"][5]["message"] == "no fill"
    assert status_bar["timeline"][7]["details"][0]["quantity"] == 200.0
    assert all(
        {"expected", "observed", "decision"}.issubset(item)
        for item in status_bar["timeline"]
    )
    assert status_bar["status"] == "blocked"


def test_strategy_dashboard_status_bar_requires_duckdb_signal_after_migration(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={
            "to_state": "running",
            "signal_enabled": True,
            "submit_enabled": True,
            "initial_cash": 20000.0,
        },
    )
    for trading_date in ("2026-06-04", "2026-06-05", "2026-06-08"):
        _write_daily_ohlc(stock_db, "600519", trading_date, 10.0, 10.5)
    _write_jsonl_legacy_only(
        live_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "CLIENT-0608",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "accepted",
            "submit_date": "2026-06-08",
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]
    day = next(item for item in strategy["live"]["run_status_bar"]["days"] if item["date"] == "2026-06-08")

    assert day["checkpoints"][1]["key"] == "SIGNAL_READY"
    assert day["checkpoints"][1]["status"] == "ok"
    assert day["checkpoints"][1]["message"] == "no signal"
    assert day["checkpoints"][1]["observed"] == "0 signal row(s)"
    assert day["checkpoints"][1]["decision"] == "ok no-op: no signal emitted"
    assert day["checkpoints"][2]["key"] == "ORDER_SUBMITTED"
    assert day["checkpoints"][2]["status"] == "ok"
    assert len(day["checkpoints"]) == 3

    migrate_all(root)
    migrated_strategy = build_dashboard_payload(root)["strategies"][0]
    migrated_day = next(item for item in migrated_strategy["live"]["run_status_bar"]["days"] if item["date"] == "2026-06-08")

    assert migrated_day["checkpoints"][1]["key"] == "SIGNAL_READY"
    assert migrated_day["checkpoints"][1]["status"] == "ok"
    assert migrated_day["checkpoints"][2]["key"] == "ORDER_SUBMITTED"
    assert migrated_day["checkpoints"][2]["status"] == "blocked"
    assert migrated_day["checkpoints"][2]["expected"] == "For due signals, submitted and filled quantities are reconciled."
    assert migrated_day["checkpoints"][2]["message"] == "no fill"
    assert migrated_day["checkpoints"][2]["observed"] == "submitted=0 filled=0 for 1 signal(s)"
    assert migrated_day["checkpoints"][2]["decision"] == "blocked: no fills for due signals"


def test_strategy_dashboard_run_status_uses_yellow_for_partial_fill(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={
            "to_state": "running",
            "signal_enabled": True,
            "submit_enabled": True,
            "initial_cash": 20000.0,
        },
    )
    for trading_date in ("2026-06-04", "2026-06-05", "2026-06-08"):
        _write_daily_ohlc(stock_db, "600519", trading_date, 10.0, 10.5)
    _write_jsonl(
        live_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "ORD-PARTIAL",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "accepted",
            "submit_date": "2026-06-08",
        }],
    )
    _write_jsonl(
        live_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-08T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "ORD-PARTIAL",
            "broker_order_id": "ORD-PARTIAL",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "partial",
        }],
    )
    _write_jsonl(
        live_records / "fills.jsonl",
        [{
            "timestamp": "2026-06-08T09:32:00",
            "strategy_name": "DemoStrategy",
            "order_id": "ORD-PARTIAL",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 50,
            "price": 10.0,
            "commission": 1.0,
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]
    day = next(item for item in strategy["live"]["run_status_bar"]["days"] if item["date"] == "2026-06-08")
    order = next(item for item in day["checkpoints"] if item["key"] == "ORDER_SUBMITTED")

    assert day["status"] == "warning"
    assert order["status"] == "warning"
    assert order["message"] == "partial fill"
    assert order["observed"] == "submitted=100 filled=50 for 1 signal(s)"
    assert order["decision"] == "warning: partially filled due signals"
    assert order["details"][0]["symbol"] == "600519"
    assert order["details"][0]["signal_quantity"] == 100.0
    assert order["details"][0]["submitted_quantity"] == 100.0
    assert order["details"][0]["filled_quantity"] == 50.0
    assert order["details"][0]["status"] == "partial"


def test_strategy_dashboard_run_status_separates_signal_generation_from_execution_ledger(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={
            "to_state": "running",
            "signal_enabled": True,
            "submit_enabled": True,
            "initial_cash": 20000.0,
        },
    )
    for trading_date in ("2026-06-08", "2026-06-09", "2026-06-10"):
        _write_daily_ohlc(stock_db, "510050", trading_date, 2.9, 3.0)
        _write_daily_ohlc(stock_db, "510880", trading_date, 3.2, 3.3)
    store = StrategyStateStore(root / "quant" / "infrastructure" / "var" / "strategy_dashboard.duckdb")
    for signal_id, timestamp, order_id, quantity in (
        ("sig:client-a", "2026-06-09T09:30:00", "CLIENT-A", 200.0),
        ("sig:client-b", "2026-06-09T09:31:00", "CLIENT-B", 300.0),
    ):
        store.upsert_signal(signal={
            "signal_id": signal_id,
            "strategy_name": "DemoStrategy",
            "mode": "live",
            "timestamp": timestamp,
            "signal_date": "2026-06-09",
            "symbol": "510050",
            "side": "BUY",
            "quantity": quantity,
            "order_type": "LIMIT",
            "reference_price": 2.9,
            "status": "accepted",
            "order_id": order_id,
            "submit_date": "2026-06-09",
            "record_date": "2026-06-09",
        })
    for signal_id, timestamp, order_id, quantity in (
        ("sig:broker-filled-a", "2026-06-09T09:32:00", "BROKER-1", 200.0),
        ("sig:broker-filled-b", "2026-06-09T09:32:30", "BROKER-2", 300.0),
    ):
        store.upsert_signal(signal={
            "signal_id": signal_id,
            "strategy_name": "DemoStrategy",
            "mode": "live",
            "timestamp": timestamp,
            "signal_date": "2026-06-09",
            "symbol": "510050",
            "side": "BUY",
            "quantity": quantity,
            "order_type": "LIMIT",
            "reference_price": 2.9,
            "status": "filled",
            "order_id": order_id,
            "broker_order_id": order_id,
            "fill_quantity": quantity,
            "fill_price": 2.9,
            "commission": 1.0,
            "fill_time": timestamp,
            "submit_date": "2026-06-09",
            "record_date": "2026-06-09",
        })
    store.upsert_signal(signal={
        "signal_id": "sig:close",
        "strategy_name": "DemoStrategy",
        "mode": "live",
        "timestamp": "2026-06-09T15:00:00",
        "signal_date": "2026-06-09",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 3.2,
        "status": "accepted",
        "order_id": "CLIENT-CLOSE",
        "record_date": "2026-06-09",
    })
    store.upsert_snapshot(
        strategy_name="DemoStrategy",
        mode="live",
        snapshot_date="2026-06-09",
        nav=20000.0,
        cash=20000.0,
        market_value=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        total_pnl=0.0,
        source="test",
    )

    strategy = build_dashboard_payload(root)["strategies"][0]
    day = next(item for item in strategy["live"]["run_status_bar"]["days"] if item["date"] == "2026-06-09")
    signal = next(item for item in day["checkpoints"] if item["key"] == "SIGNAL_READY")
    order = next(item for item in day["checkpoints"] if item["key"] == "ORDER_SUBMITTED")

    assert signal["message"] == "1 signal(s)"
    assert signal["details"] == [{
        "timestamp": "2026-06-09T15:00:00",
        "signal_date": "2026-06-09",
        "submit_date": "2026-06-10",
        "symbol": "510880",
        "side": "BUY",
        "quantity": 100.0,
        "order_type": "LIMIT",
        "reference_price": 3.2,
        "status": "accepted",
        "order_id": "CLIENT-CLOSE",
    }]
    assert order["status"] == "ok"
    assert order["observed"] == "submitted=500 filled=500 for 2 signal(s)"
    assert [
        (item["order_id"], item["submitted_quantity"], item["filled_quantity"], item["status"])
        for item in order["details"]
    ] == [
        ("CLIENT-A", 200.0, 200.0, "filled"),
        ("CLIENT-B", 300.0, 300.0, "filled"),
    ]


def test_strategy_dashboard_run_status_shows_close_oss_after_failed_execution(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={
            "to_state": "running",
            "signal_enabled": True,
            "submit_enabled": True,
            "initial_cash": 20000.0,
        },
    )
    for trading_date in ("2026-06-04", "2026-06-05", "2026-06-08"):
        _write_daily_ohlc(stock_db, "600519", trading_date, 10.0, 10.5)
    _write_jsonl(
        live_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "ORD-NOFILL",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "accepted",
            "submit_date": "2026-06-08",
        }],
    )
    _write_jsonl(
        live_records / "orders.jsonl",
        [{
            "timestamp": "2026-06-08T09:31:00",
            "strategy_name": "DemoStrategy",
            "order_id": "ORD-NOFILL",
            "broker_order_id": "ORD-NOFILL",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        live_records / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-08T15:05:00",
            "date": "2026-06-08",
            "strategy_name": "DemoStrategy",
            "nav": 20000.0,
            "cash": 20000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]
    day = next(item for item in strategy["live"]["run_status_bar"]["days"] if item["date"] == "2026-06-08")
    order = next(item for item in day["checkpoints"] if item["key"] == "ORDER_SUBMITTED")

    assert day["status"] == "blocked"
    assert order["status"] == "blocked"
    assert order["message"] == "no fill"
    assert order["observed"] == "submitted=100 filled=0 for 1 signal(s)"


def test_strategy_dashboard_run_status_waits_for_future_submit_date(tmp_path):
    root = tmp_path
    live_config = root / "quant" / "infrastructure" / "var" / "qmt_live_config"
    live_records = root / "quant" / "infrastructure" / "var" / "live_trading" / "2026-06-08"
    stock_db = root / "quant" / "infrastructure" / "var" / "duckdb" / "live" / "cn_ohlcv.duckdb"
    live_config.mkdir(parents=True)
    live_records.mkdir(parents=True)
    stock_db.parent.mkdir(parents=True, exist_ok=True)
    (live_config / "config.yaml").write_text(
        yaml.safe_dump({"strategies": [{"name": "DemoStrategy", "enabled": True, "initial_cash": 20000}]}),
        encoding="utf-8",
    )
    _init_dashboard_state(
        root,
        "DemoStrategy",
        live={
            "to_state": "running",
            "signal_enabled": True,
            "submit_enabled": True,
            "initial_cash": 20000.0,
        },
    )
    for trading_date in ("2026-06-05", "2026-06-08", "2026-06-09"):
        _write_daily_ohlc(stock_db, "600519", trading_date, 10.0, 10.5)
    _write_jsonl(
        live_records / "signals.jsonl",
        [{
            "timestamp": "2026-06-08T15:00:00",
            "strategy_name": "DemoStrategy",
            "order_id": "CLIENT-FUTURE",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "accepted",
        }],
    )
    _write_jsonl(
        live_records / "snapshots.jsonl",
        [{
            "timestamp": "2026-06-08T15:05:00",
            "date": "2026-06-08",
            "strategy_name": "DemoStrategy",
            "nav": 20000.0,
            "cash": 20000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )

    strategy = build_dashboard_payload(root)["strategies"][0]
    day = next(item for item in strategy["live"]["run_status_bar"]["days"] if item["date"] == "2026-06-08")
    order = next(item for item in day["checkpoints"] if item["key"] == "ORDER_SUBMITTED")

    assert day["status"] == "pending"
    assert order["status"] == "pending"
    assert order["message"] == "waiting submit date"
    assert order["observed"] == "1 pending signal(s), next submit_date=2026-06-09"
    assert order["details"][0]["status"] == "pending_submit"
    assert order["details"][0]["submit_date"] == "2026-06-09"


def _write_jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    _ensure_db_signal(path, rows)


def _write_jsonl_legacy_only(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_complete_live_day(day_dir: Path, strategy_name: str, symbol: str, order_id: str) -> None:
    trading_date = day_dir.name
    _write_jsonl(
        day_dir / "signals.jsonl",
        [{
            "timestamp": f"{trading_date}T15:00:00",
            "strategy_name": strategy_name,
            "order_id": order_id,
            "symbol": symbol,
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "accepted",
            "submit_date": trading_date,
        }],
    )
    _write_jsonl(
        day_dir / "orders.jsonl",
        [{
            "timestamp": f"{trading_date}T09:31:00",
            "strategy_name": strategy_name,
            "order_id": order_id,
            "broker_order_id": order_id,
            "symbol": symbol,
            "side": "BUY",
            "quantity": 100,
            "order_type": "LIMIT",
            "price": 10.05,
            "status": "submitted",
        }],
    )
    _write_jsonl(
        day_dir / "fills.jsonl",
        [{
            "timestamp": f"{trading_date}T09:32:00",
            "strategy_name": strategy_name,
            "order_id": order_id,
            "symbol": symbol,
            "side": "BUY",
            "quantity": 100,
            "price": 10.0,
            "commission": 1.0,
        }],
    )
    _write_jsonl(
        day_dir / "snapshots.jsonl",
        [{
            "timestamp": f"{trading_date}T15:05:00",
            "date": trading_date,
            "strategy_name": strategy_name,
            "nav": 20050.0,
            "cash": 19000.0,
            "market_value": 1050.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 50.0,
            "total_pnl": 50.0,
        }],
    )


def _write_snapshot_only_day(day_dir: Path, strategy_name: str) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    trading_date = day_dir.name
    _write_jsonl(
        day_dir / "snapshots.jsonl",
        [{
            "timestamp": f"{trading_date}T15:05:00",
            "date": trading_date,
            "strategy_name": strategy_name,
            "nav": 20000.0,
            "cash": 20000.0,
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
        }],
    )


def _ensure_db_signal(path: Path, rows: List[Dict[str, Any]]) -> None:
    var_dir = None
    parts = path.parts
    for i, p in enumerate(parts):
        if p == "var" and i + 1 < len(parts):
            var_dir = Path(*parts[:i + 1])
            break
    if var_dir is None:
        return
    import hashlib as _hl
    store = StrategyStateStore(var_dir / "strategy_dashboard.duckdb")
    kind_map = {"signals.jsonl": "signals", "orders.jsonl": "orders", "fills.jsonl": "fills", "snapshots.jsonl": "snapshots"}
    kind = kind_map.get(path.name, "")
    mode = "paper" if "paper_trading" in str(path) else "live"
    day_str = path.parent.name if path.parent.name else ""
    for row in rows:
        strategy_name = str(row.get("strategy_name") or "default")
        signal_date = str(row.get("signal_date") or row.get("timestamp") or day_str or "2026-06-03")[:10]
        if kind == "signals":
            parts_hash = [strategy_name, mode, signal_date, str(row.get("symbol", "")), str(row.get("side", "")), str(row.get("quantity")), str(row.get("order_type", "")), str(row.get("order_id", "")), str(row.get("timestamp", ""))]
            sid = f"sig:{_hl.sha1(json.dumps(parts_hash, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]}"
            store.upsert_signal(signal={
                "signal_id": sid, "strategy_name": strategy_name, "mode": mode,
                "timestamp": str(row.get("timestamp", "")), "signal_date": signal_date,
                "symbol": str(row.get("symbol", "")), "side": str(row.get("side", "")),
                "quantity": float(row.get("quantity", 0.0)), "order_type": str(row.get("order_type", "")),
                "reference_price": row.get("price"), "status": str(row.get("status", "generated")),
                "order_id": str(row.get("order_id", "")), "failure_reason": str(row.get("reason", "")),
                "submit_date": str(row.get("submit_date") or row.get("execution_date") or "")[:10],
                "cost_bps": row.get("execution_cost_bps"),
                "execution_reference_price": row.get("execution_reference_price"),
                "record_date": signal_date,
            })
        elif kind == "orders":
            oid = str(row.get("order_id", ""))
            boid = str(row.get("broker_order_id", ""))
            sig = store.get_signal_by_order(mode=mode, order_id=oid, signal_date=signal_date)
            if not sig and boid:
                sig = store.get_signal_by_order(mode=mode, order_id=boid, signal_date=signal_date)
            if not sig:
                sig = store.get_signal_by_signature(mode=mode, strategy_name=strategy_name, symbol=str(row.get("symbol", "")), side=str(row.get("side", "")), quantity=float(row.get("quantity", 0.0)), signal_date=signal_date)
            if not sig:
                sig = store.get_signal_for_submission(mode=mode, strategy_name=strategy_name, symbol=str(row.get("symbol", "")), side=str(row.get("side", "")), quantity=float(row.get("quantity", 0.0)), submit_date=signal_date)
            store.upsert_order(order={
                "signal_id": str((sig or {}).get("signal_id", "")),
                "strategy_name": strategy_name,
                "mode": mode,
                "timestamp": str(row.get("timestamp", "")),
                "signal_date": str((sig or {}).get("signal_date", "")),
                "submit_date": str(row.get("submit_date") or row.get("execution_date") or signal_date)[:10],
                "record_date": signal_date,
                "symbol": str(row.get("symbol", "")),
                "side": str(row.get("side", "")),
                "quantity": float(row.get("quantity", 0.0)),
                "order_type": str(row.get("order_type", "")),
                "price": row.get("price"),
                "status": str(row.get("status", "submitted")),
                "order_id": oid,
                "broker_order_id": boid,
                "execution_reference_price": row.get("execution_reference_price"),
                "cost_bps": row.get("execution_cost_bps"),
            })
        elif kind == "fills":
            oid = str(row.get("order_id", ""))
            sig = store.get_signal_by_order(mode=mode, order_id=oid, signal_date=signal_date)
            if not sig:
                sig = store.get_signal_by_signature(mode=mode, strategy_name=strategy_name, symbol=str(row.get("symbol", "")), side=str(row.get("side", "")), quantity=float(row.get("quantity", 0.0)), signal_date=signal_date)
            if not sig:
                sig = store.get_signal_for_submission(mode=mode, strategy_name=strategy_name, symbol=str(row.get("symbol", "")), side=str(row.get("side", "")), quantity=float(row.get("quantity", 0.0)), submit_date=signal_date)
            order = store.get_order_by_order_id(mode=mode, order_id=oid, record_date=signal_date, strategy_name=strategy_name, symbol=str(row.get("symbol", "")), side=str(row.get("side", "")))
            store.upsert_fill(fill={
                "fill_id": str(row.get("fill_id") or row.get("trade_id") or ""),
                "order_row_id": str((order or {}).get("order_row_id", "")),
                "signal_id": str((sig or {}).get("signal_id", "") or (order or {}).get("signal_id", "")),
                "strategy_name": strategy_name,
                "mode": mode,
                "timestamp": str(row.get("timestamp", "")),
                "signal_date": str((sig or {}).get("signal_date", "") or (order or {}).get("signal_date", "")),
                "record_date": signal_date,
                "symbol": str(row.get("symbol", "")),
                "side": str(row.get("side", "")),
                "quantity": float(row.get("quantity", 0.0)),
                "price": float(row.get("price", 0.0)),
                "commission": float(row.get("commission", 0.0)),
                "order_id": oid,
                "broker_order_id": str((order or {}).get("broker_order_id", "")),
                "source": "test_jsonl_helper",
            })
            _upsert_position_from_fill(store, strategy_name, mode, str(row.get("symbol", "")), str(row.get("side", "")), float(row.get("quantity", 0.0)), float(row.get("price", 0.0)), float(row.get("commission", 0.0)), str(row.get("timestamp", "")))
        elif kind == "snapshots":
            store.upsert_snapshot(strategy_name=strategy_name, mode=mode, snapshot_date=signal_date, nav=float(row.get("nav", 0.0)), cash=float(row.get("cash", 0.0)), market_value=float(row.get("market_value", 0.0)), realized_pnl=float(row.get("realized_pnl", 0.0)), unrealized_pnl=float(row.get("unrealized_pnl", 0.0)), total_pnl=float(row.get("total_pnl", 0.0)), source=str(row.get("source", mode)))


def _upsert_position_from_fill(
    store: StrategyStateStore,
    strategy_name: str,
    mode: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    commission: float,
    fill_time: str,
) -> None:
    current = store.get_position(strategy_name=strategy_name, mode=mode, symbol=symbol)
    current_qty = float((current or {}).get("quantity", 0.0))
    current_avg = float((current or {}).get("avg_cost", 0.0))
    current_rpnl = float((current or {}).get("realized_pnl", 0.0))
    if side.upper() == "BUY":
        new_qty = current_qty + quantity
        total_cost = (current_avg * current_qty) + (price * quantity) + commission
        new_avg = total_cost / new_qty if new_qty > 0 else 0.0
        new_rpnl = current_rpnl
    else:
        new_qty = max(0.0, current_qty - quantity)
        if current_qty > 0:
            realized = (price - current_avg) * min(quantity, current_qty) - commission
        else:
            realized = 0.0
        new_rpnl = current_rpnl + realized
        new_avg = current_avg if new_qty > 0 else 0.0
    if new_qty <= 0:
        store.delete_position(strategy_name=strategy_name, mode=mode, symbol=symbol)
    else:
        store.upsert_position(
            strategy_name=strategy_name, mode=mode, symbol=symbol,
            quantity=new_qty, avg_cost=new_avg, realized_pnl=new_rpnl,
            updated_at=fill_time,
        )


def _write_daily_ohlc(path: Path, symbol: str, trading_date: str, open_price: float, close: float):
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            create table if not exists daily_cn_ochl (
                timestamp timestamp,
                symbol varchar,
                open double,
                close double
            )
            """
        )
        con.execute(
            "insert into daily_cn_ochl values (?, ?, ?, ?)",
            [trading_date, symbol, open_price, close],
        )
    finally:
        con.close()
