import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType


ROOT = Path(__file__).resolve().parents[1]


def _python_files(path):
    return [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]


def _package_parts(path):
    relative = path.relative_to(ROOT).with_suffix("")
    return ("quant", *relative.parent.parts)


def _resolve_import_from(path, node):
    if node.level == 0:
        return node.module

    package = _package_parts(path)
    base = package[: len(package) - node.level + 1]
    suffix = tuple(node.module.split(".")) if node.module else ()
    return ".".join((*base, *suffix))


def _imported_modules(path, tree=None):
    tree = tree or ast.parse(path.read_text(encoding="utf-8"))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(path, node)
            if module:
                modules.append(module)
                modules.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return modules


def test_research_feature_does_not_import_infrastructure_or_sibling_features():
    files = _python_files(ROOT / "features" / "research")
    forbidden = ("quant.infrastructure", "quant.features.backtest", "quant.features.portfolio", "quant.features.strategies", "quant.features.trading")
    offenders = []
    for path in files:
        imports = _imported_modules(path)
        if any(module.startswith(token) for module in imports for token in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_infrastructure_research_does_not_import_features():
    files = _python_files(ROOT / "infrastructure" / "research")
    offenders = []
    for path in files:
        imports = _imported_modules(path)
        if any(module.startswith("quant.features") for module in imports):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_domain_ports_do_not_import_external_libraries_or_features():
    files = _python_files(ROOT / "domain" / "ports")
    forbidden = ("pandas", "numpy", "duckdb", "requests", "quant.features", "quant.infrastructure")
    offenders = []
    for path in files:
        imports = _imported_modules(path)
        if any(module.startswith(token) for module in imports for token in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_research_domain_ports_are_exported_with_expected_methods():
    from quant.domain.ports import (
        ExperimentStore,
        FactorData,
        ResearchArtifactStore,
        ResearchMarketData,
        ResearchSource,
    )

    assert ResearchSource.__abstractmethods__ == {"search", "source_name"}
    assert ResearchMarketData.__abstractmethods__ == {"get_daily_bars"}
    assert FactorData.__abstractmethods__ == {"get_factors", "list_factors"}
    assert ExperimentStore.__abstractmethods__ == {
        "start_run",
        "record_metrics",
        "complete_run",
        "get_run",
        "list_runs",
        "list_metrics",
        "get_artifacts",
    }
    assert ResearchArtifactStore.__abstractmethods__ == {"save_json", "save_table", "load_artifact"}


def test_research_feature_models_are_frozen_and_defaults_are_preserved():
    from quant.features.research.models import (
        CostEstimate,
        EnsembleResult,
        PurgedWalkForwardResult,
        RegimeLabel,
        ResearchConfig,
        ResearchResult,
        RunMetadata,
        StrategySpec,
        ValidationReport,
    )

    spec = StrategySpec(
        strategy_id="s1",
        strategy_type="momentum",
        signal_formula_key="mom_20",
        universe=["SPY"],
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=["close"],
        status="ready",
    )
    try:
        spec.status = "changed"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("StrategySpec must be frozen")

    report = ValidationReport(
        strategy_id="s1",
        status="pass",
        rank_ic=0.03,
        rank_ic_ir=0.5,
        ic_decay=[0.03, 0.02],
        fdr_adjusted_p=0.01,
        fdr_significant=True,
        ff_alpha_monthly=0.01,
        ff_alpha_tstat=2.0,
        ff_r2=0.4,
        long_short_spread=0.02,
        hit_rate=0.55,
        data_start="2020-01-01",
        data_end="2024-12-31",
        n_observations=252,
    )
    assert report.errors == ()

    assert PurgedWalkForwardResult([], 1.0, 0.2, None, 0.1, 0.75, True).is_viable is True
    assert RegimeLabel("low_vol", "2020-01-01", "2020-02-01", 0.8).regime == "low_vol"
    assert CostEstimate(1.0, 2.0, 3.0, 6.0, 0.1, True).capacity_ok is True
    assert RunMetadata("r1", "s1", "cfg", "data", "code", "running", "now").completed_at is None
    assert EnsembleResult(["s1"], [1.0], 1.2, -0.1, 0.15, 1.0, 0.0, 1.0).effective_n == 1.0

    config = ResearchConfig()
    assert config.validation_enabled is True
    assert config.validation_min_obs == 252
    assert config.tracking_db_path == ""
    assert config.ensemble_config == {}

    result = ResearchResult(run_id="r1", specified=2, validated_passed=1, ensemble_built=True)
    data = result.to_dict()
    assert data["run_id"] == "r1"
    assert data["specified"] == 2
    assert data["validated_passed"] == 1
    assert data["ensemble_built"] is True


def test_research_frozen_models_store_immutable_nested_state():
    from quant.features.research.models import PurgedWalkForwardResult, StrategySpec, ValidationReport

    universe = ["SPY"]
    fields = ["close"]
    spec = StrategySpec(
        strategy_id="s1",
        strategy_type="momentum",
        signal_formula_key="mom_20",
        universe=universe,
        horizon_days=5,
        lookback_days=20,
        execution_lag_days=1,
        required_fields=fields,
        status="ready",
    )
    universe.append("QQQ")
    fields.append("volume")

    assert spec.universe == ("SPY",)
    assert spec.required_fields == ("close",)
    assert not hasattr(spec.universe, "append")

    report = ValidationReport(
        strategy_id="s1",
        status="pass",
        rank_ic=0.03,
        rank_ic_ir=0.5,
        ic_decay=[0.03],
        fdr_adjusted_p=0.01,
        fdr_significant=True,
        ff_alpha_monthly=0.01,
        ff_alpha_tstat=2.0,
        ff_r2=0.4,
        long_short_spread=0.02,
        hit_rate=0.55,
        data_start="2020-01-01",
        data_end="2024-12-31",
        n_observations=252,
        errors=["warning"],
    )

    assert report.ic_decay == (0.03,)
    assert report.errors == ("warning",)
    assert not hasattr(report.errors, "append")

    split = {"train_start": "2020-01-01", "oos_sharpe": 1.0}
    walkforward = PurgedWalkForwardResult([split], 1.0, 0.2, None, 0.1, 0.75, True)
    split["oos_sharpe"] = -9.0

    assert isinstance(walkforward.splits[0], MappingProxyType)
    assert walkforward.splits[0]["oos_sharpe"] == 1.0
    try:
        walkforward.splits[0]["oos_sharpe"] = -9.0
    except TypeError:
        pass
    else:
        raise AssertionError("split mapping must be immutable")

    nested_values = []
    nested_meta = {"labels": nested_values, "flags": {"seen"}}
    nested_split = {"meta": nested_meta}
    nested_walkforward = PurgedWalkForwardResult([nested_split], 1.0, 0.2, None, 0.1, 0.75, True)
    nested_values.append("mutated")
    nested_meta["extra"] = "mutated"

    assert isinstance(nested_walkforward.splits[0]["meta"], MappingProxyType)
    assert nested_walkforward.splits[0]["meta"]["labels"] == ()
    assert nested_walkforward.splits[0]["meta"]["flags"] == frozenset({"seen"})
    assert "extra" not in nested_walkforward.splits[0]["meta"]
    try:
        nested_walkforward.splits[0]["meta"]["labels"] += ("mutated",)
    except TypeError:
        pass
    else:
        raise AssertionError("nested split mapping must be immutable")


def test_imported_modules_resolves_relative_imports():
    path = ROOT / "features" / "research" / "_example.py"
    tree = ast.parse(
        "from ..backtest import runner\n"
        "from ...infrastructure import research\n"
        "from quant.features import backtest\n"
        "from quant import infrastructure\n"
        "from .. import backtest\n"
        "from ... import infrastructure\n"
    )

    modules = _imported_modules(path, tree=tree)

    assert "quant.features.backtest" in modules
    assert "quant.infrastructure" in modules
    assert modules.count("quant.features.backtest") == 3
    assert modules.count("quant.infrastructure") == 3
