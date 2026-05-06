import ast
import hashlib
import logging
import sys
import types
from pathlib import Path

from quant.domain.ports import ResearchSource
from quant.features.research.models import RawStrategy, ResearchConfig


ROOT = Path(__file__).resolve().parents[1]

flask_stub = types.ModuleType("flask")
flask_stub.Blueprint = lambda *args, **kwargs: types.SimpleNamespace(route=lambda *a, **k: lambda fn: fn)
flask_stub.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
flask_stub.request = types.SimpleNamespace(get_json=lambda: {})
sys.modules.setdefault("flask", flask_stub)


class DictSource(ResearchSource):
    def __init__(self, name, payloads, fail=False):
        self._name = name
        self._payloads = payloads
        self._fail = fail
        self.calls = []

    @property
    def source_name(self):
        return self._name

    def search(self, query, max_results=10):
        self.calls.append((query, max_results))
        if self._fail:
            raise RuntimeError("source failed")
        return self._payloads[:max_results]


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
                modules.extend(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
    return modules


def test_source_hub_normalizes_dicts_into_raw_strategy():
    from quant.features.research.discovery import SourceHub
    from quant.features.research.models import RawStrategy

    source = DictSource(
        "arxiv",
        [
            {
                "title": "Daily Momentum",
                "description": "Ranks liquid stocks by daily momentum.",
                "source": "arxiv",
                "source_url": "https://example.test/1",
                "authors": "A. Researcher",
                "published_date": "2026-04-01",
            }
        ],
    )

    results = SourceHub({"arxiv": source}).search(sources=["arxiv"], max_results=5)

    assert results == [
        RawStrategy(
            title="Daily Momentum",
            description="Ranks liquid stocks by daily momentum.",
            source="arxiv",
            source_url="https://example.test/1",
            authors="A. Researcher",
            published_date="2026-04-01",
        )
    ]
    assert source.calls == [({}, 5)]


def test_source_hub_sets_missing_optional_fields_to_none():
    from quant.features.research.discovery import SourceHub

    source = DictSource(
        "blog",
        [
            {
                "title": "Opening Range Reversal",
                "description": "Daily reversal setup.",
                "source": "blog",
                "source_url": "https://example.test/blog",
            }
        ],
    )

    result = SourceHub({"blog": source}).search(sources=["blog"], max_results=1)[0]

    assert result.authors is None
    assert result.published_date is None


def test_source_hub_removes_duplicate_title_description_pairs():
    from quant.features.research.discovery import SourceHub

    first = DictSource(
        "arxiv",
        [
            {
                "title": " Volatility Carry ",
                "description": "Earns carry from volatility risk premia across futures.",
                "source": "arxiv",
                "source_url": "https://example.test/first",
            }
        ],
    )
    duplicate = DictSource(
        "nber",
        [
            {
                "title": "volatility   carry",
                "description": "Earns carry from volatility risk premia across futures.",
                "source": "nber",
                "source_url": "https://example.test/second",
            }
        ],
    )

    results = SourceHub({"arxiv": first, "nber": duplicate}).search(max_results=10)

    assert len(results) == 1
    assert results[0].source_url == "https://example.test/first"


def test_source_hub_skips_unknown_source_names():
    from quant.features.research.discovery import SourceHub

    known = DictSource(
        "arxiv",
        [
            {
                "title": "Known",
                "description": "Known source.",
                "source": "arxiv",
                "source_url": "https://example.test/known",
            }
        ],
    )

    results = SourceHub({"arxiv": known}).search(sources=["missing"], max_results=10)

    assert results == []
    assert known.calls == []


def test_source_hub_logs_adapter_exceptions_and_continues(caplog):
    from quant.features.research.discovery import SourceHub

    failing = DictSource("bad", [], fail=True)
    working = DictSource(
        "good",
        [
            {
                "title": "Good",
                "description": "Good source.",
                "source": "good",
                "source_url": "https://example.test/good",
            }
        ],
    )

    with caplog.at_level(logging.WARNING):
        results = SourceHub({"bad": failing, "good": working}).search(max_results=10)

    assert [result.title for result in results] == ["Good"]
    assert "Source bad search failed" in caplog.text


def test_strategy_scout_search_api_accepts_injected_sources():
    from quant.features.research.scout import StrategyScout

    source = DictSource(
        "arxiv",
        [
            {
                "title": "Injected",
                "description": "Injected source.",
                "source": "arxiv",
                "source_url": "https://example.test/injected",
            }
        ],
    )

    results = StrategyScout(sources={"arxiv": source}).search(sources=["arxiv"], max_results=1)

    assert [result.title for result in results] == ["Injected"]
    assert source.calls == [({}, 1)]


def test_strategy_scout_hash_strategy_preserves_legacy_seen_hash_formula():
    from quant.features.research.scout import StrategyScout

    raw = RawStrategy(
        title="  Daily   Momentum  ",
        description="Ranks   liquid\nstocks by daily momentum.",
        source="arxiv",
        source_url="https://example.test/hash",
    )
    expected_text = f"{raw.title.lower().strip()}::{raw.description.lower().strip()[:200]}"

    assert StrategyScout.hash_strategy(raw) == hashlib.md5(expected_text.encode()).hexdigest()


def test_api_research_scout_composition_wires_default_source_names():
    from quant.api.research_bp import _make_research_scout

    scout = _make_research_scout(ResearchConfig())

    assert {"arxiv", "ssrn"}.issubset(scout._source_hub._sources.keys())


def test_api_research_scout_composition_wires_configured_nber_source():
    from quant.api.research_bp import _make_research_scout

    scout = _make_research_scout(ResearchConfig(sources=["nber"]))

    assert set(scout._source_hub._sources.keys()) == {"nber"}


def test_script_keyword_scout_uses_source_hub_injected_sources():
    from quant.scripts.run_research import _create_keyword_scout

    scout = _create_keyword_scout()

    assert not hasattr(scout, "_adapters")
    assert len(scout._source_hub._sources) > 1


def test_feature_discovery_and_scout_do_not_import_infrastructure_or_http_fetching_libraries():
    files = [
        ROOT / "features" / "research" / "scout.py",
        *(ROOT / "features" / "research" / "discovery").glob("*.py"),
    ]
    forbidden = ("quant.infrastructure", "requests", "urllib", "xml.etree")
    offenders = []

    for path in files:
        imports = _imported_modules(path)
        if any(module.startswith(token) for module in imports for token in forbidden):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_infrastructure_sources_do_not_import_features():
    files = (ROOT / "infrastructure" / "research" / "sources").glob("*.py")
    offenders = []

    for path in files:
        imports = _imported_modules(path)
        if any(module.startswith("quant.features") for module in imports):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
