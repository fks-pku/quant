"""Standalone research pipeline runner.

Usage:
    python quant/scripts/run_research.py [--mode discover|formal|scout_formal|full] [--source config] [--max 5]
"""
import argparse
import logging
import os
import re
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("run_research")


_SCOUT_FORMAL_MODE = "scout_formal"
_FULL_REPORT_MODES = {"full", "formal", _SCOUT_FORMAL_MODE}
_BACKTEST_RUNNER_MODES = {"full", "formal", _SCOUT_FORMAL_MODE, "strict", "walkforward"}


# =============================================================================
# Heuristic evaluator — classifies & scores papers without LLM
# =============================================================================

_STRATEGY_KEYWORDS = {
    "momentum": 0.7,
    "mean.reversion": 0.7,
    "trend.following": 0.7,
    "cross.sectional": 0.65,
    "factor.investing": 0.65,
    "pairs.trading": 0.6,
    "statistical.arbitrage": 0.65,
    "volatility.regime": 0.6,
    "breakout": 0.55,
    "carry.trade": 0.55,
}

_IRRELEVANT_TERMS = {
    "llm": -3.0,
    "large.language.model": -3.0,
    "multi.agent": -3.0,
    "blockchain": -3.0,
    "crypto": -1.5,
    "cryptocurrency": -1.5,
    "defi": -2.0,
    "decentralized": -2.0,
    "nft": -3.0,
    "smart.contract": -3.0,
    "reinforcement.learning": -0.5,
    "deep.learning": -0.5,
    "neural.network": -0.5,
}

_DESIRABLE_TERMS = {
    "daily": 0.5,
    "eod": 0.5,
    "oh?lcv?": 0.5,
    "equity": 0.4,
    "stock": 0.4,
    "futures": 0.3,
    "etf": 0.3,
    "backtest": 0.4,
    "sharpe": 0.4,
    "portfolio": 0.3,
    "alpha": 0.4,
    "risk.adjusted": 0.3,
    "low.turnover": 0.4,
    "liquid": 0.3,
}

_STRATEGY_TYPE_MAP = [
    (r"momentum|trend.following|cross.sectional", "momentum"),
    (r"mean.reversion|reversal|contrarian", "mean_reversion"),
    (r"pairs.trading|statistical.arbitrage|stat.arb", "stat_arb"),
    (r"volatility|regime|vix", "volatility"),
    (r"factor|fama|smart.beta|multi.factor", "factor"),
    (r"breakout|channel", "breakout"),
    (r"carry.trade|roll.yield", "carry"),
    (r"value.investing|fundamental", "value"),
]

_CN_RESEARCH_SYMBOLS = ["000300", "000905", "600519", "000001", "510300"]

_SYMBOL_MAP = {
    "equity": _CN_RESEARCH_SYMBOLS,
    "stock": _CN_RESEARCH_SYMBOLS,
    "futures": _CN_RESEARCH_SYMBOLS,
    "etf": ["510300", "510500", "159915"],
    "forex": _CN_RESEARCH_SYMBOLS,
    "commodity": _CN_RESEARCH_SYMBOLS,
}


class HeuristicEvaluator:
    def evaluate(self, raw):
        from quant.features.research.evaluation_rubric import heuristic_evaluation

        return heuristic_evaluation(raw, config={"default_symbols": list(_CN_RESEARCH_SYMBOLS)})

    def _legacy_evaluate(self, raw):
        from quant.features.research.models import EvaluationReport
        text = f"{raw.title.lower()} {raw.description.lower()[:2000]}"
        normalized = re.sub(r"[^a-z0-9\s.]", " ", text)

        keyword_score = 0.0
        matched_strategies = []
        for kw, weight in _STRATEGY_KEYWORDS.items():
            if re.search(kw.replace(".", r"\."), normalized):
                keyword_score += weight
                matched_strategies.append(kw.replace(".", " "))

        penalty = 0.0
        for kw, weight in _IRRELEVANT_TERMS.items():
            if re.search(kw.replace(".", r"\."), normalized):
                penalty += abs(weight)

        bonus = 0.0
        for kw, weight in _DESIRABLE_TERMS.items():
            if re.search(kw.replace(".", r"\."), normalized):
                bonus += weight

        strategy_type = "unknown"
        for pattern, stype in _STRATEGY_TYPE_MAP:
            if re.search(pattern, normalized):
                strategy_type = stype
                break

        symbols = list(_CN_RESEARCH_SYMBOLS)
        for token, syms in _SYMBOL_MAP.items():
            if token in normalized:
                symbols = syms
                break

        suitability = max(0, min(10, (keyword_score + bonus - penalty) + 4.5))
        complexity = 3.0 + len(matched_strategies) * 0.8
        data_req = "medium"
        daily_adapted = True
        estimated_edge = max(0, 0.03 * (suitability / 6.0))
        summary = f"Detected: {', '.join(matched_strategies[:3]) or 'none'}"

        risk_flags = []
        if penalty > 2:
            risk_flags.append("irrelevant_topic")
        if suitability < 4:
            risk_flags.append("low_relevance")

        return EvaluationReport(
            suitability_score=round(suitability, 1),
            complexity_score=round(complexity, 1),
            data_requirement=data_req,
            daily_adaptable=daily_adapted,
            estimated_edge=round(estimated_edge, 3),
            recommended_symbols=symbols,
            strategy_type=strategy_type,
            summary=summary,
            economic_rationale_score=round(bonus / 2, 1),
            factor_uniqueness_score=round(keyword_score / 3, 1),
            data_availability_score=1.5,
            implementation_score=round(min(2, suitability / 5), 1),
            overfit_risk_score=1.0,
            cost_capacity_score=0.8,
            regime_robustness_score=0.7,
            risk_flags=risk_flags,
            rejection_reason="" if suitability >= 6 else f"suitability={suitability:.1f} < 6.0",
        )


# =============================================================================
# Enhanced scout — searches multiple arXiv categories
# =============================================================================

class _KeywordArxivAdapter:
    """ArXiv adapter that searches by keyword queries, not just category."""

    def __init__(self, query: str = "", category: str = "q-fin", name: str = "arxiv"):
        import urllib.parse
        self._name = name
        self._encoded_query = urllib.parse.quote(query)
        self._category = category
        self._base_url = "http://export.arxiv.org/api/query"

    def search(self, max_results: int = 10):
        from quant.features.research.models import RawStrategy
        from quant.features.research.scout import ArxivAdapter
        try:
            import requests
            url = f"{self._base_url}?search_query={self._encoded_query}+AND+cat:{self._category}&start=0&max_results={max_results}&sortBy=relevance&sortOrder=descending"
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            parser = ArxivAdapter()
            results = parser._parse_xml(resp.text)
            for r in results:
                r.source = self._name
            return results
        except Exception:
            return []


_STRATEGY_QUERIES = [
    ("ti:momentum AND ti:strategy", "q-fin.TR"),
    ("ti:mean reversion trading", "q-fin.TR"),
    ("ti:factor AND ti:equity AND ti:alpha", "q-fin.PM"),
    ("ti:volatility AND ti:trading", "q-fin.TR"),
    ("ti:trend following", "q-fin.TR"),
]


def _create_keyword_scout():
    from quant.features.research.scout import StrategyScout

    class KeywordScout(StrategyScout):
        def __init__(self):
            super().__init__()
            adapters = {}
            for query, cat in _STRATEGY_QUERIES:
                name = f"arxiv-{query.replace(' ', '-')[:20]}"
                adapters[name] = _KeywordArxivAdapter(query=query, category=cat, name="arxiv")
            adapters["arxiv-TR"] = _KeywordArxivAdapter(query="ti:trading strategy daily OR ti:systematic trading", category="q-fin.TR", name="arxiv")
            self._adapters = adapters

    return KeywordScout()


def _create_configured_scout(config):
    from quant.features.research.discovery.source_hub import SourceHub
    from quant.features.research.scout import StrategyScout
    from quant.infrastructure.research.sources import build_research_sources

    scout_cfg = getattr(config, "scout_config", {}) or {}
    source_hub = SourceHub(
        build_research_sources(),
        query_plan=scout_cfg.get("query_plan"),
        quality_config=scout_cfg,
    )
    return StrategyScout.from_source_hub(
        source_hub,
        sources=getattr(config, "sources", None),
        config=scout_cfg,
    )


def _resolve_source_arg(value: str, configured_sources):
    text = str(value or "config").strip()
    if text in ("config", "default", ""):
        return list(configured_sources or [])
    if text == "all":
        return list(configured_sources or [])
    return [item.strip() for item in text.split(",") if item.strip()]


def _create_research_market_data(config):
    from quant.infrastructure.research.market_data import DuckDBResearchMarketData

    return DuckDBResearchMarketData(pit_as_of_date=getattr(config, "default_backtest_end", None))


def _create_factor_data(config):
    from quant.infrastructure.research.factors.ff_factor_store import FFFactorStore

    validation_cfg = getattr(config, "validation_config", {}) or {}
    return FFFactorStore(cache_dir=validation_cfg.get("factor_cache_dir"))


def _create_validation_components(config):
    if not getattr(config, "validation_enabled", True):
        return None, None

    from quant.features.research.validation import FactorValidator, StrategySpecBuilder

    validation_cfg = dict(getattr(config, "validation_config", {}) or {})
    validation_cfg.setdefault("min_observations", getattr(config, "validation_min_obs", 252))
    validation_cfg.setdefault("start_date", getattr(config, "default_backtest_start", "2016-01-01"))
    validation_cfg.setdefault("end_date", getattr(config, "default_backtest_end", "2026-05-31"))
    market_data = _create_research_market_data(config)
    validation_cfg.setdefault("default_universe", _default_research_universe(market_data))
    return (
        StrategySpecBuilder(validation_cfg),
        FactorValidator(
            market_data,
            config=validation_cfg,
            factor_data_port=_create_factor_data(config),
        ),
    )


def _default_research_universe(market_data):
    if not hasattr(market_data, "get_universe_symbols"):
        return []
    try:
        return [str(symbol) for symbol in market_data.get_universe_symbols("cn") if str(symbol).isdigit()]
    except Exception as exc:
        logger.warning("Failed to resolve full CN research universe: %s", exc)
        return []


def _normalize_mode(mode: str) -> str:
    return str(mode or "full").lower().replace("-", "_")


def _mode_requires_backtest_runner(mode: str, explicit_backtest: bool = False) -> bool:
    return bool(explicit_backtest) or _normalize_mode(mode) in _BACKTEST_RUNNER_MODES


def _apply_mode_defaults(config, mode: str, explicit_backtest: bool = False) -> None:
    normalized = _normalize_mode(mode)
    if normalized == "fast":
        config.auto_backtest = False
        config.rigor_enabled = False
    elif normalized == "strict":
        config.auto_backtest = True
        config.rigor_enabled = False
    elif normalized == "walkforward":
        config.auto_backtest = False
        config.rigor_enabled = True
    elif normalized in _FULL_REPORT_MODES:
        config.auto_backtest = True
        config.rigor_enabled = True
    elif explicit_backtest:
        config.auto_backtest = True


def _run_scout_stage_to_idea_bank(store, scout, config, status: str = "discovered") -> int:
    rows = scout.search(sources=config.sources, max_results=config.max_results_per_source)
    store.write_discoveries(rows)
    for raw in rows:
        store.upsert_idea(raw, status=status, reason="Research scout stage")
    return len(rows)


def _select_top_idea_ids_for_formal(store, statuses=None, max_ideas: int | None = None):
    statuses = statuses or ["discovered"]
    rows = store.list_ideas(statuses)
    ranked = sorted(rows, key=lambda row: (-_idea_discovery_score(row), str(row.get("updated_at") or ""), str(row.get("title") or "")))
    if max_ideas is not None:
        ranked = ranked[: int(max_ideas)]
    return [str(row.get("idea_id")) for row in ranked if row.get("idea_id")]


def _idea_discovery_score(row) -> float:
    try:
        return float(((row.get("metadata") or {}).get("discovery_quality") or {}).get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _load_research_config():
    from quant.features.research.configuration import research_config_kwargs_from_data
    from quant.features.research.models import ResearchConfig
    from quant.shared.utils.config_loader import ConfigLoader

    config_dir = Path(__file__).resolve().parent.parent / "features" / "research" / "config"
    try:
        data = ConfigLoader(config_dir=str(config_dir)).load("research.yaml")
    except FileNotFoundError:
        return ResearchConfig()
    return ResearchConfig(**research_config_kwargs_from_data(data))


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run quant strategy research pipeline")
    parser.add_argument(
        "--mode",
        default="full",
        choices=["full", "discover", "formal", "fast", "strict", "walkforward", "scout_formal", "scout-formal"],
        help="Pipeline mode",
    )
    parser.add_argument(
        "--source",
        default="config",
        help="Source (config, all, or comma-separated source names)",
    )
    parser.add_argument("--max", type=int, default=5, dest="max_results", help="Max results per source")
    parser.add_argument("--max-ideas", type=int, default=None, help="Max local ideas to research in formal mode")
    parser.add_argument("--idea-id", action="append", dest="idea_ids", help="Specific idea_bank id to research in formal mode")
    parser.add_argument("--strategy-id", action="append", dest="strategy_ids", help="Specific strategy id for strict/walkforward modes")
    parser.add_argument("--idea-status", action="append", dest="idea_statuses", help="Idea bank status to load in formal mode")
    parser.add_argument("--threshold", type=float, default=None, help="Deprecated; stage1 now gates on daily A-share fit")
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Force strict backtest runner for non-full modes; full/formal enable it by default",
    )
    parser.add_argument(
        "--walkforward-workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="Parallel workers for purged walk-forward splits in full/formal/walkforward modes",
    )
    parser.add_argument("--no-validation", action="store_true", help="Disable statistical validation gate")
    parser.add_argument("--no-heuristic", action="store_true", help="Use LLM evaluator instead of heuristic")
    parser.add_argument("--llm", default=None, choices=["minimax", "openai", "claude", "ollama", "deepseek", "glm"],
                        help="LLM provider (only with --no-heuristic)")
    parser.add_argument("--llm-model", default=None, help="LLM model override")
    args = parser.parse_args()

    from quant.features.research.models import ResearchConfig
    from quant.features.research.research_engine import ResearchEngine
    from quant.infrastructure.research.repository import FileResearchStore

    var_root = Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research"
    var_root.mkdir(parents=True, exist_ok=True)

    needs_backtest_runner = _mode_requires_backtest_runner(args.mode, explicit_backtest=args.backtest)
    config = _load_research_config()
    config.sources = _resolve_source_arg(args.source, config.sources)
    config.max_results_per_source = args.max_results
    if args.threshold is not None:
        config.evaluation_threshold = args.threshold
    config.validation_enabled = not args.no_validation
    if not config.default_symbols:
        config.default_symbols = list(_CN_RESEARCH_SYMBOLS)
    rigor_config = dict(config.rigor_config or {})
    purged_walkforward = dict(rigor_config.get("purged_walkforward") or {})
    purged_walkforward["parallel_workers"] = max(1, args.walkforward_workers)
    rigor_config["purged_walkforward"] = purged_walkforward
    config.rigor_config = rigor_config
    _apply_mode_defaults(config, args.mode, explicit_backtest=args.backtest)

    if getattr(config, "tracking_enabled", False) and config.tracking_db_path:
        from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore

        store = DuckDBResearchStore(db_path=config.tracking_db_path, artifact_root=str(var_root))
    else:
        store = FileResearchStore(str(var_root))
    scout = _create_configured_scout(config)

    if args.mode == "discover":
        evaluator = HeuristicEvaluator()
        logger.info("Discovery mode: evaluator will not be used")
    elif args.no_heuristic:
        evaluator = _create_llm_evaluator(args)
    else:
        evaluator = HeuristicEvaluator()
        logger.info("Using heuristic evaluator (no LLM required)")

    backtest_fn = None
    walkforward_runner = None
    archived_candidate_resolver = None
    if needs_backtest_runner:
        from quant.api.research_bp import _archived_candidate_info, _make_backtest_fn, _make_walkforward_runner
        backtest_fn = _make_backtest_fn()
        walkforward_runner = _make_walkforward_runner()
        archived_candidate_resolver = _archived_candidate_info
        logger.warning("Backtests enabled — requires DuckDB data")

    strategies_dir = str(Path(__file__).resolve().parent.parent / "features" / "strategies")
    rigor_hub = None
    if config.rigor_enabled:
        from quant.features.research.rigor.backtest_hub import RigorHub
        rigor_hub = RigorHub(backtest_runner=walkforward_runner, config=config.rigor_config) if walkforward_runner else None
    spec_builder, validator = _create_validation_components(config)
    engine = ResearchEngine(
        config=config,
        scout=scout,
        evaluator=evaluator,
        research_store=store,
        backtest_fn=backtest_fn,
        strategies_dir=strategies_dir,
        rigor_hub=rigor_hub,
        spec_builder=spec_builder,
        validator=validator,
        archived_candidate_resolver=archived_candidate_resolver,
    )

    print("=" * 70)
    print("  QUANT RESEARCH PIPELINE")
    print(f"  Mode: {args.mode}")
    print(f"  Sources: {', '.join(config.sources)} | Max/query: {args.max_results}")
    print(f"  Stage1 gate: daily A-share fit | Evaluator: {'heuristic' if not args.no_heuristic else 'LLM'}")
    print(f"  Backtest runner: {'ON' if needs_backtest_runner else 'OFF'}")
    if needs_backtest_runner:
        print(f"  Walk-forward workers: {max(1, args.walkforward_workers)}")
    print("=" * 70)

    mode = _normalize_mode(args.mode)
    if mode == "discover":
        result = engine.run_discovery_only()
    elif mode == "fast":
        result = engine.run_fast_research_from_idea_bank(statuses=args.idea_statuses, idea_ids=args.idea_ids, max_ideas=args.max_ideas)
    elif mode == "strict":
        result = engine.run_strict_backtest_stage(strategy_ids=args.strategy_ids, statuses=args.idea_statuses, max_strategies=args.max_ideas)
    elif mode == "walkforward":
        result = engine.run_walkforward_audit_stage(strategy_ids=args.strategy_ids, statuses=args.idea_statuses, max_strategies=args.max_ideas)
    elif mode == "formal":
        result = engine.run_formal_research_from_idea_bank(statuses=args.idea_statuses, idea_ids=args.idea_ids, max_ideas=args.max_ideas)
    elif mode == _SCOUT_FORMAL_MODE:
        scout_count = _run_scout_stage_to_idea_bank(store, scout, config)
        formal_statuses = args.idea_statuses or ["discovered"]
        max_gate_ideas = args.max_ideas if args.max_ideas is not None else args.max_results
        formal_idea_ids = args.idea_ids or _select_top_idea_ids_for_formal(
            store,
            statuses=formal_statuses,
            max_ideas=max_gate_ideas,
        )
        print(f"  Scout stage stored/updated: {scout_count}")
        print(f"  Formal pre-full gate idea ids: {', '.join(formal_idea_ids) if formal_idea_ids else 'none'}")
        if formal_idea_ids:
            result = engine.run_formal_research_from_idea_bank(statuses=formal_statuses, idea_ids=formal_idea_ids)
        else:
            result = engine.run_formal_research_from_idea_bank(statuses=formal_statuses, max_ideas=0)
    else:
        result = engine.run_full_pipeline()

    print()
    print("=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Discovered:  {result.discovered}")
    print(f"  Evaluated:   {result.evaluated}")
    print(f"  Integrated:  {result.integrated}")
    print(f"  Backtested:  {result.backtested}")
    print(f"  Rejected:    {result.rejected}")
    print(f"  Errors:      {len(result.errors)}")
    print()

    if result.errors:
        print("  ERRORS:")
        for err in result.errors:
            print(f"    ! {err}")
        print()

    print("  PIPELINE LOG:")
    for entry in result.log:
        icon = {"pass": "+", "fail": "-", "skip": "~", "info": "*", "error": "!"}.get(entry.verdict, "?")
        print(f"    [{icon}] [{entry.phase}] {entry.title[:75]}")
        if entry.reason:
            print(f"         {entry.reason}")
        if entry.scores:
            scores_str = " | ".join(f"{k}={v}" for k, v in entry.scores.items())
            print(f"         {scores_str}")

    from quant.features.research.pool import CandidatePool
    pool = CandidatePool(research_store=store)
    candidates = pool.list_candidates()
    if candidates:
        print()
        print(f"  CANDIDATES ({len(candidates)}):")
        for c in candidates:
            meta = c.get("research_meta", {})
            default_symbols = config.default_symbols
            print(f"    [{c['id']}] {c['name'][:60]}")
            print(f"         suitability={meta.get('suitability_score','?')}  edge={meta.get('estimated_edge','?')}  type={meta.get('strategy_type','?')}")
            _print_generated_files(c["id"])
    else:
        print()
        print("  No candidates passed the configured gates.")

    _print_store_contents(store, var_root)

    print()
    print("  Done.")


def _create_llm_evaluator(args):
    from quant.features.research.evaluator import StrategyEvaluator
    provider = args.llm or "deepseek"
    default_models = {"deepseek": "deepseek-chat", "glm": "glm-5.1", "openai": "gpt-4o-mini", "claude": "claude-sonnet-4-20250514", "ollama": "llama3", "minimax": "MiniMax-M2.7"}
    model = args.llm_model or default_models.get(provider, "deepseek-chat")
    llm = _create_llm_adapter(provider, model)
    if llm is None:
        logger.error("No LLM adapter available. Add --no-llm to use heuristic mode.")
        sys.exit(1)
    return StrategyEvaluator(llm_adapter=llm)


def _create_llm_adapter(provider, model):
    if provider == "minimax":
        from quant.features.cio.llm_adapters.minimax_adapter import MiniMaxAdapter
        return MiniMaxAdapter(model=model, api_key=os.environ.get("MINIMAX_API_KEY", ""),
                              group_id=os.environ.get("MINIMAX_GROUP_ID", ""))
    elif provider == "openai":
        from quant.features.cio.llm_adapters.openai_adapter import OpenAIAdapter
        return OpenAIAdapter(model=model, api_key=os.environ.get("OPENAI_API_KEY", ""))
    elif provider == "claude":
        from quant.features.cio.llm_adapters.claude_adapter import ClaudeAdapter
        return ClaudeAdapter(model=model, api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    elif provider == "ollama":
        from quant.features.cio.llm_adapters.ollama_adapter import OllamaAdapter
        return OllamaAdapter(model=model)
    elif provider == "deepseek":
        from quant.features.cio.llm_adapters.deepseek_adapter import DeepSeekAdapter
        return DeepSeekAdapter(model=model, api_key=os.environ.get("DEEPSEEK_API_KEY", ""))
    elif provider == "glm":
        from quant.features.cio.llm_adapters.glm_adapter import GLMAdapter
        return GLMAdapter(model=model, api_key=os.environ.get("GLM_API_KEY", ""))
    return None


def _print_generated_files(strategy_id):
    strategies_dir = Path(__file__).resolve().parent.parent / "features" / "strategies" / strategy_id
    if strategies_dir.exists():
        for f in sorted(strategies_dir.iterdir()):
            print(f"         + {f.name}")


def _print_store_contents(store, var_root):
    print()
    print(f"  Store: {var_root}")
    for p in sorted(var_root.iterdir()):
        if p.is_file():
            size = p.stat().st_size
            print(f"    {p.name} ({size:,} bytes)")


if __name__ == "__main__":
    main()
