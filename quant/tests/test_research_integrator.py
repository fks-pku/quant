import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.models import RawStrategy, EvaluationReport


def test_normalize_name():
    integrator = StrategyIntegrator(strategies_dir=Path("/tmp"))
    assert integrator._normalize_name("Cross-Sectional Momentum!") == "cross_sectional_momentum"


def test_generate_strategy_code_contains_class():
    integrator = StrategyIntegrator(strategies_dir=Path("/tmp"))
    raw = RawStrategy("Test Momentum", "Buy winners", "arxiv", "url")
    report = EvaluationReport(8.0, 3.0, "medium", True, 0.1, ["AAPL"], "momentum", "Good")
    code = integrator._generate_strategy_code("test_momentum", raw, report)
    assert "class TestMomentumStrategy" in code
    assert "@strategy" in code
    assert "on_data" in code
