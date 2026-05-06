"""Standalone research pipeline runner.

Usage:
    python quant/scripts/run_research.py [--source arxiv] [--max 5] [--threshold 6.0] [--backtest]
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

_SYMBOL_MAP = {
    "equity": ["SPY", "QQQ"],
    "stock": ["AAPL", "MSFT"],
    "futures": ["ES", "NQ"],
    "etf": ["SPY", "QQQ", "IWM"],
    "forex": ["EURUSD"],
    "commodity": ["GC", "CL"],
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

        symbols = ["SPY"]
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

class _KeywordArxivSource:
    def __init__(self, query: str = "", category: str = "q-fin", name: str = "arxiv"):
        from quant.infrastructure.research.sources import ArxivSource

        self._name = name
        self._query = query
        self._category = category
        self._source = ArxivSource(category=category)

    @property
    def source_name(self):
        return self._name

    def search(self, query, max_results: int = 10):
        arxiv_query = f"{self._query} AND cat:{self._category}" if self._query else f"cat:{self._category}"
        results = self._source.search(
            {"query": arxiv_query, "sort_by": "relevance", "sort_order": "descending"},
            max_results=max_results,
        )
        for result in results:
            result["source"] = self._name
        return results


_STRATEGY_QUERIES = [
    ("ti:momentum AND ti:strategy", "q-fin.TR"),
    ("ti:mean reversion trading", "q-fin.TR"),
    ("ti:factor AND ti:equity AND ti:alpha", "q-fin.PM"),
    ("ti:volatility AND ti:trading", "q-fin.TR"),
    ("ti:trend following", "q-fin.TR"),
]


def _create_keyword_scout():
    from quant.features.research.scout import StrategyScout

    sources = {}
    for query, cat in _STRATEGY_QUERIES:
        name = f"arxiv-{query.replace(' ', '-')[:20]}"
        sources[name] = _KeywordArxivSource(query=query, category=cat, name="arxiv")
    sources["arxiv-TR"] = _KeywordArxivSource(query="ti:trading strategy daily OR ti:systematic trading", category="q-fin.TR", name="arxiv")
    return StrategyScout(sources=sources)


def _run_backtest_for_cli(strategy_id, symbols, start_text, end_text, initial_cash, cost_config):
    from datetime import datetime

    import pandas as pd

    from quant.features.backtest.engine import Backtester
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.strategies.registry import StrategyRegistry
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider

    registry = StrategyRegistry()
    strategy_class = registry.get(strategy_id)
    if strategy_class is None:
        return None, None, [f"Strategy {strategy_id} not in registry for backtest"]

    start = datetime.strptime(start_text, "%Y-%m-%d")
    end = datetime.strptime(end_text, "%Y-%m-%d")
    db_provider = DuckDBProvider()
    db_provider.connect()
    all_data = []
    try:
        for symbol in symbols:
            bars = db_provider.get_bars(symbol, start, end, "1d")
            if not bars.empty:
                all_data.append(bars)
    finally:
        db_provider.disconnect()

    if not all_data:
        return None, None, [f"No data for {strategy_id}"]

    data_df = pd.concat(all_data, ignore_index=True)
    strategy = strategy_class(symbols=symbols)
    bt_config = {
        "backtest": {"slippage_bps": dict(cost_config or {}).get("slippage_bps", 5)},
        "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
    }
    backtester = Backtester(bt_config, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio)
    result = backtester.run(
        start=start,
        end=end,
        strategies=[strategy],
        initial_cash=float(initial_cash),
        data_provider=DataFrameProvider(data_df),
        symbols=symbols,
    )
    return result, data_df, []


def _make_script_backtest_fn():
    from datetime import datetime

    from quant.features.research.models import ResearchLogEntry

    def _run_backtest(strategy_id, result, config, integrator, pool):
        symbols = list(config.default_symbols)
        bt_result, data_df, errors = _run_backtest_for_cli(
            strategy_id,
            symbols,
            config.default_backtest_start,
            config.default_backtest_end,
            100000,
            {"slippage_bps": 5},
        )
        if errors:
            result.errors.extend(errors)
            return

        info = integrator.get_registry_entry(strategy_id)
        if info is None:
            return

        start = datetime.strptime(config.default_backtest_start, "%Y-%m-%d")
        end = datetime.strptime(config.default_backtest_end, "%Y-%m-%d")
        info["backtest"] = {
            "sharpe": round(bt_result.sharpe_ratio, 2),
            "max_dd": round(bt_result.max_drawdown_pct, 2),
            "cagr": round(bt_result.total_return * 100 / max(1, (end - start).days / 365.25), 2),
            "win_rate": round(bt_result.win_rate * 100, 2),
            "period": f"{config.default_backtest_start}-{config.default_backtest_end}",
        }
        meta = info.setdefault("research_meta", {})
        meta["backtest_result"] = info["backtest"]
        if bt_result.sharpe_ratio < config.backtest_sharpe_threshold:
            pool.reject(strategy_id, reason=f"Backtest Sharpe {bt_result.sharpe_ratio:.2f} below threshold")
            result.rejected += 1
            result.log.append(ResearchLogEntry(
                phase="backtest", title=info.get("name", strategy_id),
                source="", source_url="", verdict="fail",
                reason=f"Sharpe {bt_result.sharpe_ratio:.2f} < {config.backtest_sharpe_threshold}",
                scores={
                    "sharpe": round(bt_result.sharpe_ratio, 2),
                    "max_dd": round(bt_result.max_drawdown_pct, 2),
                    "win_rate": round(bt_result.win_rate * 100, 2),
                },
            ))
            return

        result.backtested += 1
        result.log.append(ResearchLogEntry(
            phase="backtest", title=info.get("name", strategy_id),
            source="", source_url="", verdict="pass",
            reason=f"Sharpe {bt_result.sharpe_ratio:.2f}",
            scores={
                "sharpe": round(bt_result.sharpe_ratio, 2),
                "max_dd": round(bt_result.max_drawdown_pct, 2),
                "win_rate": round(bt_result.win_rate * 100, 2),
            },
        ))

    return _run_backtest


def _make_script_rigor_backtest_runner():
    from quant.features.research.rigor import serialize_backtest_trades

    def _run(strategy_id, request):
        symbols = list(request.get("symbols") or [])
        bt_result, data_df, errors = _run_backtest_for_cli(
            strategy_id,
            symbols,
            request.get("start"),
            request.get("end"),
            request.get("initial_cash", 100000),
            request.get("cost_config", {}),
        )
        if errors:
            return {"metrics": {}, "equity_curve": [], "trades": [], "errors": errors}
        return {
            "metrics": {
                "sharpe": bt_result.sharpe_ratio,
                "sharpe_ratio": bt_result.sharpe_ratio,
                "max_drawdown_pct": bt_result.max_drawdown_pct,
                "total_return": bt_result.total_return,
                "win_rate": bt_result.win_rate,
            },
            "equity_curve": [],
            "trades": serialize_backtest_trades(bt_result.trades, data_df),
            "errors": [],
        }

    return _run


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run quant strategy research pipeline")
    parser.add_argument("--source", default="arxiv", help="Source (arxiv, ssrn, all)")
    parser.add_argument("--max", type=int, default=5, dest="max_results", help="Max results per source")
    parser.add_argument("--threshold", type=float, default=6.0, help="Suiteability threshold (0-10)")
    parser.add_argument("--backtest", action="store_true", help="Run backtests (requires DuckDB data)")
    parser.add_argument("--no-heuristic", action="store_true", help="Use LLM evaluator instead of heuristic")
    parser.add_argument("--llm", default=None, choices=["minimax", "openai", "claude", "ollama", "deepseek", "glm"],
                        help="LLM provider (only with --no-heuristic)")
    parser.add_argument("--llm-model", default=None, help="LLM model override")
    parser.add_argument("--research-store", default="file", choices=["file", "duckdb"], help="Research store backend")
    parser.add_argument("--research-db", default=None, help="DuckDB research state path when --research-store=duckdb")
    args = parser.parse_args()

    from quant.features.research.models import ResearchConfig
    from quant.features.research.research_engine import ResearchEngine

    var_root = Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research"
    var_root.mkdir(parents=True, exist_ok=True)

    config = ResearchConfig(
        sources=["arxiv"] if args.source != "all" else ["arxiv"],
        max_results_per_source=args.max_results,
        evaluation_threshold=args.threshold,
        auto_backtest=args.backtest,
        backtest_sharpe_threshold=0.5,
        default_symbols=["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"],
        default_backtest_start="2020-01-01",
        default_backtest_end="2024-12-31",
        research_store_backend=args.research_store,
    )

    if args.research_store == "duckdb":
        from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore
        store = DuckDBResearchStore(str(var_root), db_path=args.research_db)
    else:
        from quant.infrastructure.research.repository import FileResearchStore
        store = FileResearchStore(str(var_root))
    scout = _create_keyword_scout()

    if args.no_heuristic:
        evaluator = _create_llm_evaluator(args)
    else:
        evaluator = HeuristicEvaluator()
        logger.info("Using heuristic evaluator (no LLM required)")

    backtest_fn = None
    rigor_hub = None
    if args.backtest:
        from quant.features.research.rigor import RigorHub
        backtest_fn = _make_script_backtest_fn()
        rigor_hub = RigorHub(_make_script_rigor_backtest_runner(), config=config.rigor_config)
        logger.warning("Backtests enabled - requires DuckDB data")

    strategies_dir = str(Path(__file__).resolve().parent.parent / "features" / "strategies")
    engine = ResearchEngine(
        config=config,
        scout=scout,
        evaluator=evaluator,
        research_store=store,
        backtest_fn=backtest_fn,
        strategies_dir=strategies_dir,
        rigor_hub=rigor_hub,
    )

    print("=" * 70)
    print("  QUANT RESEARCH PIPELINE")
    print(f"  Source: arXiv (keyword search, 6 queries) | Max/query: {args.max_results}")
    print(f"  Threshold: {args.threshold} | Evaluator: {'heuristic' if not args.no_heuristic else 'LLM'}")
    print(f"  Backtest: {'ON' if args.backtest else 'OFF'}")
    print("=" * 70)

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
