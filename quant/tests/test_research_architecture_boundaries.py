import ast

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_files(path):
    return [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]


def _scan_imports(source, forbidden):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for token in forbidden:
                    if alias.name.startswith(token):
                        return True
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for token in forbidden:
                    if node.module.startswith(token):
                        return True
    return False


def test_research_feature_does_not_import_infrastructure_or_sibling_features():
    files = _python_files(ROOT / "features" / "research")
    forbidden = (
        "quant.infrastructure",
        "quant.features.backtest",
        "quant.features.portfolio",
        "quant.features.strategies",
        "quant.features.trading",
    )
    offenders = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        if _scan_imports(source, forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_infrastructure_research_does_not_import_features():
    infra_path = ROOT / "infrastructure" / "research"
    if not infra_path.exists():
        return
    files = _python_files(infra_path)
    offenders = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        if _scan_imports(source, ("quant.features",)):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_domain_ports_do_not_import_external_libraries_or_features():
    files = _python_files(ROOT / "domain" / "ports")
    forbidden = (
        "pandas",
        "numpy",
        "duckdb",
        "requests",
        "quant.features",
        "quant.infrastructure",
    )
    offenders = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        if _scan_imports(source, forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
