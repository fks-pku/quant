"""Standalone research pipeline runner.

Usage:
    python quant/scripts/run_research.py [--mode discover|formal|full] [--source arxiv] [--max 5] [--threshold 6.0] [--backtest]
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
    return (
        StrategySpecBuilder(validation_cfg),
        FactorValidator(
            _create_research_market_data(config),
            config=validation_cfg,
            factor_data_port=_create_factor_data(config),
        ),
    )


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run quant strategy research pipeline")
    parser.add_argument("--mode", default="full", choices=["full", "discover", "formal"], help="Pipeline mode")
    parser.add_argument("--source", default="arxiv", help="Source (arxiv, ssrn, all)")
    parser.add_argument("--max", type=int, default=5, dest="max_results", help="Max results per source")
    parser.add_argument("--max-ideas", type=int, default=None, help="Max local ideas to research in formal mode")
    parser.add_argument("--idea-id", action="append", dest="idea_ids", help="Specific idea_bank id to research in formal mode")
    parser.add_argument("--idea-status", action="append", dest="idea_statuses", help="Idea bank status to load in formal mode")
    parser.add_argument("--threshold", type=float, default=6.0, help="Suiteability threshold (0-10)")
    parser.add_argument("--backtest", action="store_true", help="Run backtests (requires DuckDB data)")
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

    config = ResearchConfig(
        sources=["arxiv"] if args.source != "all" else ["arxiv"],
        max_results_per_source=args.max_results,
        evaluation_threshold=args.threshold,
        auto_backtest=args.backtest,
        validation_enabled=not args.no_validation,
        backtest_sharpe_threshold=0.5,
        default_symbols=list(_CN_RESEARCH_SYMBOLS),
        default_backtest_start="2020-01-01",
        default_backtest_end="2024-12-31",
    )

    if getattr(config, "tracking_enabled", False) and config.tracking_db_path:
        from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore

        store = DuckDBResearchStore(db_path=config.tracking_db_path, artifact_root=str(var_root))
    else:
        store = FileResearchStore(str(var_root))
    scout = _create_keyword_scout()

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
    if args.backtest:
        from quant.api.research_bp import _make_backtest_fn, _make_walkforward_runner
        backtest_fn = _make_backtest_fn()
        walkforward_runner = _make_walkforward_runner()
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
    )

    print("=" * 70)
    print("  QUANT RESEARCH PIPELINE")
    print(f"  Mode: {args.mode}")
    print(f"  Source: arXiv (keyword search, 6 queries) | Max/query: {args.max_results}")
    print(f"  Threshold: {args.threshold} | Evaluator: {'heuristic' if not args.no_heuristic else 'LLM'}")
    print(f"  Backtest: {'ON' if args.backtest else 'OFF'}")
    print("=" * 70)

    if args.mode == "discover":
        result = engine.run_discovery_only()
    elif args.mode == "formal":
        result = engine.run_formal_research_from_idea_bank(statuses=args.idea_statuses, idea_ids=args.idea_ids, max_ideas=args.max_ideas)
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
        print("  No candidates passed the threshold.")

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
