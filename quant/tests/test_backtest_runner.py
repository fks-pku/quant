"""Tests for backtest runner CLI."""

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.backtest_runner import main, parse_args


class TestParseArgs:
    def test_required_args(self):
        args = parse_args([
            "--strategy", "SimpleMomentum",
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--symbols", "AAPL,MSFT",
        ])
        assert args.strategy == "SimpleMomentum"
        assert args.start == "2024-01-01"
        assert args.end == "2024-03-01"
        assert args.symbols == "AAPL,MSFT"

    def test_defaults(self):
        args = parse_args([
            "--strategy", "SimpleMomentum",
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--symbols", "AAPL",
        ])
        assert args.initial_cash == 100000
        assert args.slippage_bps == 5
        assert args.output_dir == "./backtest_output"
        assert args.db == "./var/duckdb/quant.duckdb"

    def test_custom_values(self):
        args = parse_args([
            "--strategy", "SimpleMomentum",
            "--start", "2024-01-01",
            "--end", "2024-03-01",
            "--symbols", "AAPL,MSFT",
            "--initial-cash", "50000",
            "--slippage-bps", "10",
            "--output-dir", "/tmp/results",
        ])
        assert args.initial_cash == 50000
        assert args.slippage_bps == 10
        assert args.output_dir == "/tmp/results"

    def test_missing_required_arg(self):
        with pytest.raises(SystemExit):
            parse_args(["--strategy", "SimpleMomentum"])


def _make_synthetic_data(symbols, start, days):
    rows = []
    for symbol in symbols:
        base_price = 100.0
        for i in range(days):
            ts = start + timedelta(days=i)
            if ts.weekday() >= 5:
                continue
            change = np.random.normal(0.001, 0.02)
            base_price *= (1 + change)
            base_price = max(base_price, 1.0)
            rows.append({
                "timestamp": ts,
                "open": base_price * 0.99,
                "high": base_price * 1.01,
                "low": base_price * 0.98,
                "close": base_price,
                "volume": 1000000,
                "symbol": symbol,
            })
    return pd.DataFrame(rows)


class TestRunnerWithSyntheticData:
    def test_runner_with_synthetic_data(self, monkeypatch, tmp_path):
        np.random.seed(42)
        symbols = ["HK.00700", "HK.09988"]
        start = datetime(2024, 1, 2)
        end = datetime(2024, 3, 1)
        days = 60
        synthetic = _make_synthetic_data(symbols, start, days)

        from quant.core.walkforward import DataFrameProvider

        class FakeDuckDBProvider:
            def __init__(self, *a, **kw):
                self._connected = False
            def connect(self):
                self._connected = True
            def disconnect(self):
                pass
            def get_bars(self, symbol, start, end, timeframe):
                subset = synthetic[synthetic["symbol"] == symbol]
                subset = subset[
                    (subset["timestamp"] >= start) & (subset["timestamp"] <= end)
                ]
                return subset

        class FakeDuckDBStorage:
            def __init__(self, *a, **kw):
                pass
            def get_lot_size(self, symbol):
                return 100
            def close(self):
                pass

        monkeypatch.setattr(
            "quant.backtest_runner.DuckDBProvider", FakeDuckDBProvider
        )
        monkeypatch.setattr(
            "quant.backtest_runner.DuckDBStorage", FakeDuckDBStorage
        )

        output_dir = tmp_path / "bt_out"
        result = main([
            "--strategy", "SimpleMomentum",
            "--start", "2024-01-02",
            "--end", "2024-03-01",
            "--symbols", "HK.00700,HK.09988",
            "--output-dir", str(output_dir),
            "--initial-cash", "100000",
        ])

        assert result is not None
        assert result.final_nav > 0

        equity_csv = output_dir / "SimpleMomentum_2024-01-02_2024-03-01_equity.csv"
        assert equity_csv.exists()

        metrics_json = output_dir / "SimpleMomentum_2024-01-02_2024-03-01_metrics.json"
        assert metrics_json.exists()
        with open(metrics_json) as f:
            metrics = json.load(f)
        assert metrics["strategy"] == "SimpleMomentum"
        assert metrics["initial_cash"] == 100000
        assert "final_nav" in metrics
        assert "sharpe_ratio" in metrics

    def test_runner_unknown_strategy_exits(self, monkeypatch):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--strategy", "NonExistent",
                "--start", "2024-01-01",
                "--end", "2024-03-01",
                "--symbols", "AAPL",
            ])
        assert exc_info.value.code == 1
