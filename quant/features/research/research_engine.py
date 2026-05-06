import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports import ExperimentStore, ResearchArtifactStore
from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import EvaluationReport, ResearchConfig, ResearchResult, ResearchLogEntry, RawStrategy
from quant.features.research.scout import StrategyScout
from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.pool import CandidatePool
from quant.features.research.tracking import RunRecorder
from quant.features.research.validation.strategy_spec_builder import StrategySpecBuilder

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
        experiment_store: Optional[ExperimentStore] = None,
        artifact_store: Optional[ResearchArtifactStore] = None,
        spec_builder: Optional[Any] = None,
        validator: Optional[Any] = None,
        rigor_hub: Optional[Any] = None,
    ):
        self.config = config or ResearchConfig()
        self.scout = scout or StrategyScout()
        self.evaluator = evaluator or StrategyEvaluator()
        _default_dir = Path(__file__).resolve().parent.parent / "strategies"
        self.research_store = research_store or _NullResearchStore()
        self.experiment_store = experiment_store
        self.artifact_store = artifact_store
        self.spec_builder = spec_builder
        if self.config.validation_enabled and self.spec_builder is None:
            self.spec_builder = StrategySpecBuilder(self.config.validation_config)
        self.validator = validator
        self.rigor_hub = rigor_hub
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
        run_id = None
        if self.config.tracking_enabled and self.experiment_store is not None:
            run_id = self.experiment_store.start_run(
                "research_pipeline",
                {
                    "config_hash": RunRecorder.hash_config(asdict(self.config)),
                    "data_hash": RunRecorder.hash_data({
                        "sources": sources or self.config.sources,
                        "max_results_per_source": self.config.max_results_per_source,
                    }),
                    "code_version": RunRecorder.get_code_version(),
                },
            )
            result.run_id = run_id

        try:
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
            pending_validation = []
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

                    validation_action = self._validation_action(raw, report, result, evaluation_rows)
                    if validation_action is False:
                        continue
                    if validation_action is not True:
                        pending_validation.append((raw, report, validation_action))
                        continue

                    self._integrate_candidate(raw, report, result, integrated_ids, evaluation_rows)
                except Exception as e:
                    logger.error(f"Pipeline error for '{raw.title}': {e}")
                    result.errors.append(str(e))
                    result.log.append(ResearchLogEntry(
                        phase="evaluate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="error",
                        reason=str(e),
                    ))

            self._validate_and_integrate_pending(pending_validation, result, integrated_ids, evaluation_rows)
            self.research_store.write_evaluations(evaluation_rows)

            if self.config.auto_backtest and integrated_ids:
                self._run_backtests(integrated_ids, result)

            if self.config.rigor_enabled and self.rigor_hub is not None and integrated_ids:
                self._run_rigor(integrated_ids, result)

            self.research_store.save_run_result(result)
            if run_id is not None:
                self.experiment_store.complete_run(run_id, "completed")
            logger.info(f"Pipeline complete: {result}")
            return result
        except Exception as e:
            if run_id is not None:
                self.experiment_store.complete_run(run_id, "failed", error=str(e))
            raise

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

    def _run_rigor(self, strategy_ids: List[str], result: ResearchResult) -> None:
        for sid in strategy_ids:
            try:
                walkforward = self.rigor_hub.evaluate(
                    sid,
                    list(self.config.default_symbols),
                    self.config.default_backtest_start,
                    self.config.default_backtest_end,
                    run_id=result.run_id,
                )
            except Exception as exc:
                result.errors.append(f"Rigor error for {sid}: {exc}")
                self.pool.reject(sid, reason=f"Rigor exception: {exc}")
                result.rejected += 1
                continue

            if walkforward.is_viable:
                result.walkforward_passed += 1
                result.log.append(ResearchLogEntry(
                    phase="walkforward", title=sid, source="", source_url="",
                    verdict="pass",
                    reason=f"worst_oos_sharpe={walkforward.worst_oos_sharpe:.2f}",
                    scores={
                        "aggregate_oos_sharpe": walkforward.aggregate_oos_sharpe,
                        "worst_oos_sharpe": walkforward.worst_oos_sharpe,
                        "pct_profitable_splits": walkforward.pct_profitable_splits,
                    },
                ))
                continue

            self.pool.reject(sid, reason=f"Walk-forward failed: worst_oos_sharpe={walkforward.worst_oos_sharpe:.2f}")
            result.rejected += 1
            result.log.append(ResearchLogEntry(
                phase="walkforward", title=sid, source="", source_url="",
                verdict="fail",
                reason=f"worst_oos_sharpe={walkforward.worst_oos_sharpe:.2f}",
                scores={
                    "aggregate_oos_sharpe": walkforward.aggregate_oos_sharpe,
                    "worst_oos_sharpe": walkforward.worst_oos_sharpe,
                    "pct_profitable_splits": walkforward.pct_profitable_splits,
                },
            ))

    def _validation_action(
        self,
        raw: RawStrategy,
        report: EvaluationReport,
        result: ResearchResult,
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> Any:
        if not self.config.validation_enabled or self.spec_builder is None:
            return True

        spec = self.spec_builder.build(raw, report)
        result.specified += 1

        if spec.status != "ready":
            result.needs_manual_spec += 1
            reason = spec.reason or f"spec status={spec.status}"
            result.log.append(ResearchLogEntry(
                phase="specify", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="skip",
                reason=reason,
                scores={"strategy_type": report.strategy_type, "spec_status": spec.status},
            ))
            evaluation_rows.append((raw, report, "needs_manual_spec", reason))
            logger.info(f"'{raw.title}' needs manual specification ({spec.status})")
            return False

        if self.validator is None:
            result.log.append(ResearchLogEntry(
                phase="validate", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="skip",
                reason="No validator injected",
                scores={
                    "strategy_type": report.strategy_type,
                    "spec_status": spec.status,
                    "formula": spec.signal_formula_key,
                },
            ))
            return True

        return spec

    def _validate_and_integrate_pending(
        self,
        pending_validation: List[Tuple[RawStrategy, EvaluationReport, Any]],
        result: ResearchResult,
        integrated_ids: List[str],
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> None:
        if not pending_validation or self.validator is None:
            return

        specs = [item[2] for item in pending_validation]
        if hasattr(self.validator, "validate_many"):
            try:
                validation_reports = self.validator.validate_many(
                    specs,
                    self.config.default_backtest_start,
                    self.config.default_backtest_end,
                )
            except Exception as exc:
                logger.error(f"Batch validation failed: {exc}")
                self._validate_pending_individually(pending_validation, result, integrated_ids, evaluation_rows)
                return
        else:
            self._validate_pending_individually(pending_validation, result, integrated_ids, evaluation_rows)
            return

        for (raw, evaluation_report, spec), validation_report in zip(pending_validation, validation_reports):
            result.validated += 1
            if self._record_validation_result(raw, evaluation_report, spec, validation_report, result, evaluation_rows):
                self._integrate_candidate(raw, evaluation_report, result, integrated_ids, evaluation_rows)

    def _validate_pending_individually(
        self,
        pending_validation: List[Tuple[RawStrategy, EvaluationReport, Any]],
        result: ResearchResult,
        integrated_ids: List[str],
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> None:
        for raw, evaluation_report, spec in pending_validation:
            try:
                validation_report = self.validator.validate(
                    spec,
                    self.config.default_backtest_start,
                    self.config.default_backtest_end,
                )
            except Exception as exc:
                result.validated += 1
                self._record_validation_exception(raw, evaluation_report, spec, exc, result, evaluation_rows)
                continue

            result.validated += 1
            if self._record_validation_result(raw, evaluation_report, spec, validation_report, result, evaluation_rows):
                self._integrate_candidate(raw, evaluation_report, result, integrated_ids, evaluation_rows)

    def _record_validation_result(
        self,
        raw: RawStrategy,
        report: EvaluationReport,
        spec: Any,
        validation_report: Any,
        result: ResearchResult,
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> bool:
        if validation_report.status == "pass":
            result.validated_passed += 1
            result.log.append(ResearchLogEntry(
                phase="validate", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="pass",
                reason=f"rank_ic={validation_report.rank_ic:.4f}",
                scores={
                    "rank_ic": validation_report.rank_ic,
                    "rank_ic_ir": validation_report.rank_ic_ir,
                    "fdr_adjusted_p": validation_report.fdr_adjusted_p,
                    "hit_rate": validation_report.hit_rate,
                    "formula": spec.signal_formula_key,
                },
            ))
            return True

        reason = "; ".join(validation_report.errors) or f"validation status={validation_report.status}"
        result.rejected += 1
        result.log.append(ResearchLogEntry(
            phase="validate", title=raw.title, source=raw.source,
            source_url=raw.source_url, verdict="fail",
            reason=reason,
            scores={
                "rank_ic": validation_report.rank_ic,
                "fdr_adjusted_p": validation_report.fdr_adjusted_p,
                "hit_rate": validation_report.hit_rate,
                "formula": spec.signal_formula_key,
            },
        ))
        evaluation_rows.append((raw, report, "validation_fail", reason))
        logger.info(f"'{raw.title}' failed validation: {reason}")
        return False

    def _record_validation_exception(
        self,
        raw: RawStrategy,
        report: EvaluationReport,
        spec: Any,
        exc: Exception,
        result: ResearchResult,
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> None:
        reason = f"Validation error for {raw.title}: {exc}"
        result.errors.append(reason)
        result.rejected += 1
        result.log.append(ResearchLogEntry(
            phase="validate", title=raw.title, source=raw.source,
            source_url=raw.source_url, verdict="error",
            reason=str(exc),
            scores={
                "strategy_type": report.strategy_type,
                "spec_status": getattr(spec, "status", ""),
                "formula": getattr(spec, "signal_formula_key", ""),
            },
        ))
        evaluation_rows.append((raw, report, "validation_error", str(exc)))
        logger.error(reason)

    def _integrate_candidate(
        self,
        raw: RawStrategy,
        report: EvaluationReport,
        result: ResearchResult,
        integrated_ids: List[str],
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> None:
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
            return

        result.errors.append(f"Integration failed for '{raw.title}'")
        result.log.append(ResearchLogEntry(
            phase="integrate", title=raw.title, source=raw.source,
            source_url=raw.source_url, verdict="error",
            reason="Integration failed",
        ))
        evaluation_rows.append((raw, report, "error", "Integration failed"))


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
