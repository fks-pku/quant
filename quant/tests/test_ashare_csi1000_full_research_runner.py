import importlib
import json
from datetime import datetime


def test_csi1000_full_research_runner_uses_csi1000_contract():
    runner = importlib.import_module("quant.scripts.run_ashare_csi1000_strict_index_enhanced_full_research")

    assert runner.STRATEGY_ID == "ashare_csi1000_strict_index_enhanced"
    assert runner.INDEX_CODE == "000852.SH"
    assert runner.BENCHMARK_SYMBOL == "000852"
    assert runner.INITIAL_CASH == 2_000_000.0
    assert runner.SOURCE_URLS[0] == "https://bigquant.com/wiki/doc/GpphcQ6EdE"
    assert "codesharev3" in runner.SOURCE_URLS[1]
    assert runner.STRATEGY_PARAMS["benchmark_symbol"] == "000852"
    assert runner.STRATEGY_PARAMS["max_positions"] == 120
    assert runner.STRATEGY_PARAMS["max_single_weight"] == 0.055


def test_dedicated_full_research_writer_updates_latest_metadata(tmp_path):
    base_runner = importlib.import_module("quant.scripts.run_ashare_dividend_low_vol_quality_full_research")
    output_root = tmp_path / "reports"
    strict_report = {
        "metrics": {"sharpe": 0.0, "cagr": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0},
        "benchmark": {},
        "capacity": {},
        "equity_curve": {"strategy": []},
    }
    walkforward = {"verdict": "fail", "is_viable": False}

    full_report_path, payload_path = base_runner._write_reports(
        output_root,
        ["600001"],
        strict_report,
        walkforward,
        datetime(2026, 1, 1),
        datetime(2026, 1, 31),
    )

    latest_dir = output_root / "latest"
    metadata = json.loads((latest_dir / "metadata.json").read_text(encoding="utf-8"))

    assert full_report_path.exists()
    assert payload_path.exists()
    assert (latest_dir / "full_research_report.html").exists()
    assert (latest_dir / "last_result.json").exists()
    assert metadata["report_id"] == base_runner.STRATEGY_ID
    latest_path = metadata["full_report"]["latest_path"].replace("\\", "/")
    assert latest_path.endswith("reports/latest/full_research_report.html")
    assert metadata["stage_reports"]["strict_backtest"]["filename"] == "strict_backtest_report.html"


def test_dedicated_walkforward_excludes_flat_no_trade_splits():
    base_runner = importlib.import_module("quant.scripts.run_ashare_dividend_low_vol_quality_full_research")
    strict_report = {
        "capacity": {"max_adv_participation": 0.01},
        "equity_curve": {
            "strategy": [
                {"date": "2018-01-01", "value": 100.0},
                {"date": "2019-12-31", "value": 100.0},
                {"date": "2020-01-01", "value": 100.0},
                {"date": "2021-12-31", "value": 110.0},
                {"date": "2022-01-01", "value": 110.0},
                {"date": "2023-12-31", "value": 110.0},
                {"date": "2024-01-01", "value": 110.0},
                {"date": "2025-12-31", "value": 121.0},
            ]
        },
    }

    result = base_runner._walkforward_from_strict_equity(strict_report)

    assert result["total_splits"] == 4
    assert result["evaluated_splits"] == 2
    assert result["no_trade_splits"] == 2
    assert result["splits"][0]["has_trades"] is False
    assert result["splits"][0]["trade_count"] == 0
    assert result["splits"][0]["verdict"] == "excluded_no_trade"
    assert result["splits"][1]["has_trades"] is True
