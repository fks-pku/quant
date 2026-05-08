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
        experiment_store: Optional[Any] = None,
        artifact_store: Optional[Any] = None,
        spec_builder: Optional[Any] = None,
        validator: Optional[Any] = None,
        rigor_hub: Optional[Any] = None,
        ensemble: Optional[Any] = None,
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
        self._experiment_store = experiment_store
        self._artifact_store = artifact_store
        self._spec_builder = spec_builder
        self._validator = validator
        self._rigor_hub = rigor_hub
        self._ensemble = ensemble

    def run_full_pipeline(self, sources: Optional[List[str]] = None, result: Optional[ResearchResult] = None) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        logger.info("Starting research pipeline")

        run_id = None
        if self._tracking_enabled():
            run_id = self._experiment_store.start_run("research_pipeline", {})
            result.run_id = run_id

        try:
            result = self._execute_pipeline(sources, result)
        except Exception as e:
            if run_id is not None:
                self._experiment_store.complete_run(run_id, "failed", error=str(e))
            raise

        if run_id is not None:
            self._experiment_store.complete_run(run_id, "completed")
        return result

    def _tracking_enabled(self) -> bool:
        return getattr(self.config, "tracking_enabled", False) and self._experiment_store is not None

    def _execute_pipeline(self, sources: Optional[List[str]], result: ResearchResult) -> ResearchResult:
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

                if self.config.validation_enabled and self._spec_builder is not None:
                    spec = self._spec_builder.build(raw, report)
                    result.specified += 1
                    if spec.status != "ready":
                        result.needs_manual_spec += 1
                        result.log.append(ResearchLogEntry(
                            phase="validation", title=raw.title, source=raw.source,
                            source_url=raw.source_url, verdict="skip",
                            reason=f"Spec status: {spec.status}",
                        ))
                    elif self._validator is not None:
                        vreport = self._validator.validate(spec)
                        result.validated += 1
                        if vreport.status == "error" or not vreport.fdr_significant or abs(vreport.rank_ic) < 0.02:
                            result.log.append(ResearchLogEntry(
                                phase="validation", title=raw.title, source=raw.source,
                                source_url=raw.source_url, verdict="fail",
                                reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                                scores={"rank_ic": vreport.rank_ic, "hit_rate": vreport.hit_rate},
                            ))
                            result.rejected += 1
                            evaluation_rows.append((raw, report, "fail", f"Validation failed: IC={vreport.rank_ic:.4f}"))
                            continue
                        else:
                            result.validated_passed += 1
                            result.log.append(ResearchLogEntry(
                                phase="validation", title=raw.title, source=raw.source,
                                source_url=raw.source_url, verdict="pass",
                                reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                                scores={"rank_ic": vreport.rank_ic, "hit_rate": vreport.hit_rate},
                            ))

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

        if getattr(self.config, "ensemble_enabled", False) and self._ensemble is not None:
            try:
                candidate_ids = [
                    c.get("id") for c in self.pool.list_candidates()
                    if c.get("status") == "candidate"
                ]
                if len(candidate_ids) >= 2:
                    ensemble_result = self._ensemble.build(candidate_ids)
                    result.ensemble_built = True
                    logger.info(
                        f"Ensemble built: {len(ensemble_result.strategy_ids)} strategies, "
                        f"diversification={ensemble_result.diversification_ratio:.2f}"
                    )
            except Exception as e:
                logger.warning(f"Ensemble build failed: {e}")

        self.research_store.save_run_result(result)
        logger.info(f"Pipeline complete: {result}")
        return result

    def _run_backtests(self, strategy_ids: List[str], result: ResearchResult) -> None:
        if self._backtest_fn is None:
            logger.warning("No backtest function injected — skipping backtests")
            return
        for sid in strategy_ids:
            try:
                if self._rigor_hub is not None and self.config.rigor_enabled:
                    wf_result = self._rigor_hub.run_walkforward(
                        strategy_id=sid,
                        symbols=self.config.default_symbols,
                        start=self.config.default_backtest_start,
                        end=self.config.default_backtest_end,
                    )
                    if not wf_result.is_viable:
                        self.pool.reject(sid, reason=f"Walk-forward failed: worst_oos_sharpe={wf_result.worst_oos_sharpe:.2f}")
                        result.rejected += 1
                        continue
                    dsr = getattr(wf_result, "deflated_sharpe_ratio", None)
                    if dsr is not None and dsr < 0.95:
                        reason = f"Deflated Sharpe ratio warning: dsr={dsr:.2f} < 0.95"
                        self._mark_needs_more_validation(sid, dsr, reason)
                        result.log.append(ResearchLogEntry(
                            phase="rigor", title=sid, source="", source_url="",
                            verdict="warning", reason=reason,
                            scores={"deflated_sharpe_ratio": dsr},
                        ))
                        continue
                    result.walkforward_passed += 1
                self._backtest_fn(sid, result, self.config, self.integrator, self.pool)
            except Exception as e:
                logger.error(f"Backtest failed for {sid}: {e}")
                result.errors.append(f"Backtest error for {sid}: {e}")
                self.pool.reject(sid, reason=f"Backtest exception: {e}")
                result.rejected += 1

    def _mark_needs_more_validation(self, strategy_id: str, dsr: float, reason: str) -> None:
        info = self.research_store.get_candidate(strategy_id) if self.research_store is not None else None
        if info is not None:
            info["status"] = "needs_more_validation"
            meta = info.setdefault("research_meta", {})
            meta["dsr_warning"] = dsr
            meta["needs_more_validation_reason"] = reason
            self.research_store.upsert_candidate(info)
        elif self.research_store is not None:
            self.research_store.update_status(strategy_id, "needs_more_validation", reason=reason)

        for entry in self.integrator.registry.values():
            if entry.get("id") == strategy_id:
                entry["status"] = "needs_more_validation"
                meta = entry.setdefault("research_meta", {})
                meta["dsr_warning"] = dsr
                meta["needs_more_validation_reason"] = reason
                break


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
