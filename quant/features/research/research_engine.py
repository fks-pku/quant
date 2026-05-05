import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import ResearchConfig, ResearchResult, ResearchLogEntry, RawStrategy
from quant.features.research.scout import StrategyScout
from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.pool import CandidatePool

logger = logging.getLogger(__name__)


class ResearchEngine:
    def __init__(
        self,
        config: Optional[ResearchConfig] = None,
        scout: Optional[StrategyScout] = None,
        evaluator: Optional[StrategyEvaluator] = None,
        integrator: Optional[StrategyIntegrator] = None,
        pool: Optional[CandidatePool] = None,
        backtest_fn: Optional[Callable] = None,
        strategies_dir: Optional[str] = None,
        research_store: Optional[ResearchStore] = None,
    ):
        self.config = config or ResearchConfig()
        self.scout = scout or StrategyScout()
        self.evaluator = evaluator or StrategyEvaluator()
        _default_dir = Path(__file__).resolve().parent.parent / "strategies"
        self.research_store = research_store or _NullResearchStore()
        shared_registry = {}
        self.integrator = integrator or StrategyIntegrator(
            Path(strategies_dir) if strategies_dir else _default_dir,
            strategy_registry=shared_registry,
            research_store=self.research_store,
        )
        self.pool = pool or CandidatePool(strategy_registry=self.integrator.registry, research_store=self.research_store)
        self._backtest_fn = backtest_fn

    def run_full_pipeline(self, sources: Optional[List[str]] = None, result: Optional[ResearchResult] = None) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        logger.info("Starting research pipeline")

        raw_strategies = self.scout.search(sources=sources, max_results=self.config.max_results_per_source)
        result.discovered = len(raw_strategies)
        self.research_store.write_discoveries(raw_strategies)
        logger.info(f"Discovered {result.discovered} strategies")

        result.log.append(ResearchLogEntry(
            phase="scout", title=f"Scanned {result.discovered} strategies",
            source="", source_url="", verdict="info",
            reason=f"Sources: {sources or self.config.sources}",
        ))

        integrated_ids = []
        evaluation_rows = []
        for raw in raw_strategies:
            try:
                strategy_hash = StrategyScout.hash_strategy(raw)
                if self.research_store.has_seen(strategy_hash):
                    result.log.append(ResearchLogEntry(
                        phase="scout", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="skip",
                        reason="Previously discovered",
                    ))
                    continue
                self.research_store.mark_seen(strategy_hash, raw)

                report = self.evaluator.evaluate(raw)
                result.evaluated += 1

                passes_filter = report.suitability_score >= self.config.evaluation_threshold
                if report.data_requirement == "high-frequency":
                    passes_filter = passes_filter and report.daily_adaptable

                if not passes_filter:
                    reason_parts = [f"suitability={report.suitability_score:.1f} < {self.config.evaluation_threshold}"]
                    if report.data_requirement == "high-frequency" and not report.daily_adaptable:
                        reason_parts.append("high-frequency, not daily-adaptable")
                    result.log.append(ResearchLogEntry(
                        phase="evaluate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="fail",
                        reason="; ".join(reason_parts),
                        scores={
                            "suitability": report.suitability_score,
                            "complexity": report.complexity_score,
                            "edge": report.estimated_edge,
                        },
                    ))
                    evaluation_rows.append((raw, report, "fail", "; ".join(reason_parts)))
                    logger.info(f"'{raw.title}' filtered out (suitability={report.suitability_score})")
                    result.rejected += 1
                    continue

                strategy_id = self.integrator.integrate(raw, report)
                if strategy_id:
                    result.integrated += 1
                    integrated_ids.append(strategy_id)
                    result.log.append(ResearchLogEntry(
                        phase="integrate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="pass",
                        reason=f"Integrated as {strategy_id}",
                        scores={
                            "suitability": report.suitability_score,
                            "complexity": report.complexity_score,
                            "edge": report.estimated_edge,
                            "type": report.strategy_type,
                        },
                    ))
                    evaluation_rows.append((raw, report, "pass", f"Integrated as {strategy_id}"))
                else:
                    result.errors.append(f"Integration failed for '{raw.title}'")
                    result.log.append(ResearchLogEntry(
                        phase="integrate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="error",
                        reason="Integration failed",
                    ))
                    evaluation_rows.append((raw, report, "error", "Integration failed"))
            except Exception as e:
                logger.error(f"Pipeline error for '{raw.title}': {e}")
                result.errors.append(str(e))
                result.log.append(ResearchLogEntry(
                    phase="evaluate", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="error",
                    reason=str(e),
                ))

        self.research_store.write_evaluations(evaluation_rows)

        if self.config.auto_backtest and integrated_ids:
            self._run_backtests(integrated_ids, result)

        self.research_store.save_run_result(result)
        logger.info(f"Pipeline complete: {result}")
        return result

    def _run_backtests(self, strategy_ids: List[str], result: ResearchResult) -> None:
        if self._backtest_fn is None:
            logger.warning("No backtest function injected — skipping backtests")
            return
        for sid in strategy_ids:
            try:
                self._backtest_fn(sid, result, self.config, self.integrator, self.pool)
            except Exception as e:
                logger.error(f"Backtest failed for {sid}: {e}")
                result.errors.append(f"Backtest error for {sid}: {e}")
                self.pool.reject(sid, reason=f"Backtest exception: {e}")
                result.rejected += 1


class _NullResearchStore(ResearchStore):
    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        pass

    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        return []

    def update_status(self, strategy_id: str, status: str, reason: str = "") -> bool:
        return False

    def has_seen(self, strategy_hash: str) -> bool:
        return False

    def mark_seen(self, strategy_hash: str, raw: Any) -> None:
        pass

    def write_discoveries(self, raw_strategies: Iterable[Any]) -> None:
        pass

    def write_evaluations(self, evaluations: Iterable[Tuple[Any, Any, str, str]]) -> None:
        pass

    def save_run_result(self, result: Any) -> None:
        pass
