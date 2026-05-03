import logging
from pathlib import Path
from typing import Callable, List, Optional

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
    ):
        self.config = config or ResearchConfig()
        self.scout = scout or StrategyScout()
        self.evaluator = evaluator or StrategyEvaluator()
        _default_dir = Path(__file__).resolve().parent.parent / "strategies"
        self.integrator = integrator or StrategyIntegrator(Path(strategies_dir) if strategies_dir else _default_dir)
        self.pool = pool or CandidatePool()
        self._backtest_fn = backtest_fn

    def run_full_pipeline(self, sources: Optional[List[str]] = None, result: Optional[ResearchResult] = None) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        logger.info("Starting research pipeline")

        raw_strategies = self.scout.search(sources=sources, max_results=self.config.max_results_per_source)
        result.discovered = len(raw_strategies)
        logger.info(f"Discovered {result.discovered} strategies")

        result.log.append(ResearchLogEntry(
            phase="scout", title=f"Scanned {result.discovered} strategies",
            source="", source_url="", verdict="info",
            reason=f"Sources: {sources or self.config.sources}",
        ))

        integrated_ids = []
        for raw in raw_strategies:
            try:
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
                else:
                    result.errors.append(f"Integration failed for '{raw.title}'")
                    result.log.append(ResearchLogEntry(
                        phase="integrate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="error",
                        reason="Integration failed",
                    ))
            except Exception as e:
                logger.error(f"Pipeline error for '{raw.title}': {e}")
                result.errors.append(str(e))
                result.log.append(ResearchLogEntry(
                    phase="evaluate", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="error",
                    reason=str(e),
                ))

        if self.config.auto_backtest and integrated_ids:
            self._run_backtests(integrated_ids, result)

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
