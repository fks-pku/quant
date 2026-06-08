import ast
import inspect
from pathlib import Path

import yaml

from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.backtest.walkforward import WalkForwardEngine
from quant.features.research.models import DEFAULT_RESEARCH_INITIAL_CASH, ResearchConfig
from quant.features.research.rigor.backtest_hub import RigorHub
from quant.features.trading.portfolio import Portfolio
from quant.infrastructure.execution.brokers.paper import PaperBroker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRATEGY_CASH = 10_000.0


def test_strategy_initial_cash_defaults_are_10000():
    research_config = yaml.safe_load((PROJECT_ROOT / "quant/features/research/config/research.yaml").read_text(encoding="utf-8"))
    shared_config = yaml.safe_load((PROJECT_ROOT / "quant/shared/config/config.yaml").read_text(encoding="utf-8"))

    assert DEFAULT_RESEARCH_INITIAL_CASH == DEFAULT_STRATEGY_CASH
    assert ResearchConfig().default_initial_cash == DEFAULT_STRATEGY_CASH
    assert research_config["research"]["default_initial_cash"] == DEFAULT_STRATEGY_CASH
    assert shared_config["system"]["initial_cash"] == DEFAULT_STRATEGY_CASH
    assert shared_config["live_trading"]["strategy_initial_cash"] == DEFAULT_STRATEGY_CASH

    assert inspect.signature(Backtester.run).parameters["initial_cash"].default == DEFAULT_STRATEGY_CASH
    assert inspect.signature(WalkForwardEngine.run).parameters["initial_cash"].default == DEFAULT_STRATEGY_CASH
    assert inspect.signature(BenchmarkProvider.get_benchmark_equity).parameters["initial_cash"].default == DEFAULT_STRATEGY_CASH
    assert inspect.signature(RigorHub.run_walkforward).parameters["initial_cash"].default == DEFAULT_STRATEGY_CASH
    assert Portfolio().initial_cash == DEFAULT_STRATEGY_CASH
    assert PaperBroker().initial_cash == DEFAULT_STRATEGY_CASH


def test_strategy_runner_initial_cash_constants_are_10000():
    script_paths = sorted((PROJECT_ROOT / "quant/scripts").glob("run_*.py"))
    constants = {}

    for path in script_paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            matched = [name for name in names if name in {"INITIAL_CASH", "DEFAULT_INITIAL_CASH", "CASH"}]
            if matched:
                value = ast.literal_eval(node.value)
                constants[f"{path.name}:{matched[0]}"] = float(value)

    assert constants
    assert all(value == DEFAULT_STRATEGY_CASH for value in constants.values())
