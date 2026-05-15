import inspect
import logging
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import DEFAULT_A_SHARE_SYMBOLS, ResearchConfig, ResearchResult, ResearchLogEntry, RawStrategy
from quant.features.research.scout import StrategyScout
from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.pool import CandidatePool
from quant.features.research.tracking.run_recorder import RunRecorder
from quant.features.research.discovery.quality import discovery_quality, discovery_score

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
        benchmark_data_loader: Optional[Callable[[List[str], str, str], Any]] = None,
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
        self._benchmark_data_loader = benchmark_data_loader

    def run_full_pipeline(self, sources: Optional[List[str]] = None, result: Optional[ResearchResult] = None) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        logger.info("Starting research pipeline")

        lineage_manifest = self._lineage_manifest(sources)
        run_id = None
        if self._tracking_enabled():
            run_id = self._experiment_store.start_run("research_pipeline", lineage_manifest)
            result.run_id = run_id
        lineage_manifest["run_id"] = run_id
        self._write_lineage_manifest(run_id, lineage_manifest)

        try:
            result = self._execute_pipeline(sources, result)
        except Exception as e:
            if run_id is not None:
                self._experiment_store.complete_run(run_id, "failed", error=str(e))
            raise

        if run_id is not None:
            self._experiment_store.complete_run(run_id, "completed")
        return result

    def run_discovery_only(self, sources: Optional[List[str]] = None, result: Optional[ResearchResult] = None) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        raw_strategies = self.scout.search(sources=sources, max_results=self.config.max_results_per_source)
        result.discovered = len(raw_strategies)
        self.research_store.write_discoveries(raw_strategies)
        for raw in raw_strategies:
            self._upsert_idea(raw, "discovered", "Discovered by scout")
        result.log.append(ResearchLogEntry(
            phase="discovery_only",
            title=f"Stored {result.discovered} ideas in local idea bank",
            source="",
            source_url="",
            verdict="info",
            reason="Discovery-only run: no admission evaluation, StrategySpec, validation, integration, or backtest was executed.",
            scores=self._discovery_summary(raw_strategies),
        ))
        return result

    def run_formal_research_from_idea_bank(
        self,
        statuses: Optional[List[str]] = None,
        max_ideas: Optional[int] = None,
        idea_ids: Optional[List[str]] = None,
        result: Optional[ResearchResult] = None,
    ) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        statuses = statuses or ["discovered"]
        idea_rows = self.research_store.list_ideas(statuses)
        if idea_ids:
            allowed_ids = {str(item) for item in idea_ids}
            idea_rows = [row for row in idea_rows if str(row.get("idea_id", "")) in allowed_ids]
        if max_ideas is not None:
            idea_rows = idea_rows[: int(max_ideas)]
        raw_strategies = [self._raw_from_idea_record(row) for row in idea_rows]
        result.discovered = len(raw_strategies)
        result.log.append(ResearchLogEntry(
            phase="local_idea_bank",
            title=f"Loaded {result.discovered} ideas from local idea bank",
            source="",
            source_url="",
            verdict="info",
            reason=f"Statuses: {statuses}",
            scores={"loaded": result.discovered},
        ))
        result = self._execute_formal_research(
            raw_strategies,
            result,
            use_seen_gate=False,
            queue_reason="Loaded from local idea bank for formal research.",
        )
        return result

    def run_fast_research_from_idea_bank(
        self,
        statuses: Optional[List[str]] = None,
        max_ideas: Optional[int] = None,
        idea_ids: Optional[List[str]] = None,
        result: Optional[ResearchResult] = None,
    ) -> ResearchResult:
        auto_backtest = self.config.auto_backtest
        rigor_enabled = self.config.rigor_enabled
        self.config.auto_backtest = False
        self.config.rigor_enabled = False
        try:
            return self.run_formal_research_from_idea_bank(
                statuses=statuses,
                max_ideas=max_ideas,
                idea_ids=idea_ids,
                result=result,
            )
        finally:
            self.config.auto_backtest = auto_backtest
            self.config.rigor_enabled = rigor_enabled

    def run_strict_backtest_stage(
        self,
        strategy_ids: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        max_strategies: Optional[int] = None,
        result: Optional[ResearchResult] = None,
    ) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        items = self._stage_strategy_items(strategy_ids=strategy_ids, statuses=statuses, max_strategies=max_strategies)
        result.log.append(ResearchLogEntry(
            phase="strict_backtest_stage",
            title=f"Loaded {len(items)} strategies for strict backtest",
            source="",
            source_url="",
            verdict="info" if items else "skip",
            reason="Standalone strict backtest stage.",
            scores={"loaded": len(items)},
        ))
        if not items:
            self.research_store.save_run_result(result)
            return result
        self._run_backtests(items, result, run_walkforward=False)
        self.research_store.save_run_result(result)
        self._write_candidate_scorecard(result.run_id)
        return result

    def run_walkforward_audit_stage(
        self,
        strategy_ids: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        max_strategies: Optional[int] = None,
        result: Optional[ResearchResult] = None,
    ) -> ResearchResult:
        if result is None:
            result = ResearchResult()
        items = self._stage_strategy_items(strategy_ids=strategy_ids, statuses=statuses, max_strategies=max_strategies)
        result.log.append(ResearchLogEntry(
            phase="walkforward_audit_stage",
            title=f"Loaded {len(items)} strategies for walk-forward audit",
            source="",
            source_url="",
            verdict="info" if items else "skip",
            reason="Standalone walk-forward strict audit stage.",
            scores={"loaded": len(items)},
        ))
        if not items:
            self.research_store.save_run_result(result)
            return result
        self._run_walkforward_audits(items, result)
        self.research_store.save_run_result(result)
        self._write_candidate_scorecard(result.run_id)
        return result

    def _tracking_enabled(self) -> bool:
        return getattr(self.config, "tracking_enabled", False) and self._experiment_store is not None

    def _lineage_manifest(self, sources: Optional[List[str]]) -> Dict[str, Any]:
        config_summary = self._config_summary()
        data_summary = self._data_summary(sources)
        return {
            "manifest_version": 1,
            "pipeline": "research_pipeline",
            "run_id": None,
            "code_version": RunRecorder.get_code_version(),
            "config_hash": RunRecorder.hash_config(config_summary),
            "data_hash": RunRecorder.hash_data(data_summary),
            "config_summary": config_summary,
            "data_summary": data_summary,
        }

    def _write_lineage_manifest(self, run_id: Optional[str], manifest: Dict[str, Any]) -> None:
        if self._artifact_store is None or not hasattr(self._artifact_store, "save_json"):
            return
        try:
            self._artifact_store.save_json(run_id or "research_pipeline", "lineage_manifest", manifest)
        except Exception as e:
            logger.warning(f"Failed to write research lineage manifest: {e}")

    def _config_summary(self) -> Dict[str, Any]:
        config = asdict(self.config)
        for key in list(config):
            if "api_key" in key.lower() and config[key]:
                config[key] = "***"
        return config

    def _data_summary(self, sources: Optional[List[str]]) -> Dict[str, Any]:
        return {
            "sources": list(sources or self.config.sources),
            "max_results_per_source": self.config.max_results_per_source,
            "default_symbols": list(self.config.default_symbols),
            "default_backtest_start": self.config.default_backtest_start,
            "default_backtest_end": self.config.default_backtest_end,
            "pit_enabled": self.config.pit_enabled,
            "pit_universe_snapshot_dir": self.config.pit_universe_snapshot_dir,
            "validation_enabled": self.config.validation_enabled,
            "validation_min_obs": self.config.validation_min_obs,
            "validation_config": dict(self.config.validation_config or {}),
            "evaluation_config": dict(self.config.evaluation_config or {}),
            "scout_config": dict(self.config.scout_config or {}),
            "rigor_enabled": self.config.rigor_enabled,
            "rigor_config": dict(self.config.rigor_config or {}),
        }

    def _execute_pipeline(self, sources: Optional[List[str]], result: ResearchResult) -> ResearchResult:
        raw_strategies = self.scout.search(sources=sources, max_results=self.config.max_results_per_source)
        result.discovered = len(raw_strategies)
        self.research_store.write_discoveries(raw_strategies)
        for raw in raw_strategies:
            self._upsert_idea(raw, "discovered", "Discovered by full pipeline scout")
        logger.info(f"Discovered {result.discovered} strategies")

        result.log.append(ResearchLogEntry(
            phase="stage1_scout", title=f"Scanned {result.discovered} strategies",
            source="", source_url="", verdict="info",
            reason=f"Sources: {sources or self.config.sources}",
            scores=self._discovery_summary(raw_strategies),
        ))
        return self._execute_formal_research(raw_strategies, result, use_seen_gate=True)

    def _execute_formal_research(
        self,
        raw_strategies: List[RawStrategy],
        result: ResearchResult,
        use_seen_gate: bool = True,
        queue_reason: str = "Stage 1 completed: discovery, admission evaluation, and StrategySpec drafting.",
    ) -> ResearchResult:
        research_queue: List[Tuple[RawStrategy, Any, Any]] = []
        evaluation_rows = []
        for raw in raw_strategies:
            try:
                item = self._stage_one_candidate(raw, result, evaluation_rows, use_seen_gate=use_seen_gate)
                if item is not None:
                    research_queue.append(item)
            except Exception as e:
                logger.error(f"Stage 1 pipeline error for '{raw.title}': {e}")
                result.errors.append(str(e))
                result.log.append(ResearchLogEntry(
                    phase="stage1_admission", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="error",
                    reason=str(e),
                ))
                self._record_hypothesis(raw, status="error", stage="stage1_admission", reason=str(e))
                self._upsert_idea(raw, "error", str(e))

        result.log.append(ResearchLogEntry(
            phase="stage1_queue",
            title=f"{len(research_queue)} ideas ready for formal research",
            source="",
            source_url="",
            verdict="info",
            reason=queue_reason,
            scores={"queued": len(research_queue), "evaluated": result.evaluated, "specified": result.specified},
        ))

        integrated_items = []
        for raw, report, strategy_spec in research_queue:
            try:
                strategy_id = self._stage_two_formal_research(raw, report, strategy_spec, result, evaluation_rows)
                if strategy_id:
                    integrated_items.append((strategy_id, raw))
            except Exception as e:
                logger.error(f"Stage 2 research error for '{raw.title}': {e}")
                result.errors.append(str(e))
                result.log.append(ResearchLogEntry(
                    phase="stage2_research", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="error",
                    reason=str(e),
                ))
                self._record_hypothesis(
                    raw,
                    status="error",
                    stage="stage2_research",
                    reason=str(e),
                    report=report,
                    strategy_spec=strategy_spec,
                )
                self._upsert_idea(raw, "error", str(e))

        self.research_store.write_evaluations(evaluation_rows)

        if self.config.auto_backtest and integrated_items:
            self._run_backtests(integrated_items, result)

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
        self._write_candidate_scorecard(result.run_id)
        logger.info(f"Pipeline complete: {result}")
        return result

    def _stage_one_candidate(
        self,
        raw: RawStrategy,
        result: ResearchResult,
        evaluation_rows: List[Tuple[Any, Any, str, str]],
        use_seen_gate: bool = True,
    ) -> Optional[Tuple[RawStrategy, Any, Any]]:
        strategy_hash = StrategyScout.hash_strategy(raw)
        if use_seen_gate and self.research_store.has_seen(strategy_hash):
            result.log.append(ResearchLogEntry(
                phase="stage1_scout", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="skip",
                reason="Previously discovered",
            ))
            self._record_hypothesis(raw, status="skipped", stage="stage1_scout", reason="Previously discovered")
            self._upsert_idea(raw, "skipped", "Previously discovered")
            return None
        if use_seen_gate:
            self.research_store.mark_seen(strategy_hash, raw)

        report = self.evaluator.evaluate(raw)
        result.evaluated += 1

        evaluation_score = self._evaluation_score(report)
        passes_filter = evaluation_score >= self.config.evaluation_threshold
        if report.data_requirement == "high-frequency":
            passes_filter = passes_filter and report.daily_adaptable

        if not passes_filter:
            reason = self._admission_rejection_reason(report, evaluation_score)
            result.log.append(ResearchLogEntry(
                phase="stage1_admission", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="fail",
                reason=reason,
                scores=self._evaluation_log_scores(report, evaluation_score),
            ))
            evaluation_rows.append((raw, report, "fail", reason))
            self._record_hypothesis(
                raw,
                status="rejected",
                stage="stage1_admission",
                reason=reason,
                report=report,
            )
            logger.info(f"'{raw.title}' filtered out (admission={evaluation_score})")
            self._upsert_idea(raw, "stage1_rejected", reason)
            result.rejected += 1
            return None

        strategy_spec = None
        if self._spec_builder is not None:
            strategy_spec = self._spec_builder.build(raw, report)
            result.specified += 1
            if strategy_spec.status != "ready":
                result.needs_manual_spec += 1
                reason = f"Spec status: {strategy_spec.status}"
                result.log.append(ResearchLogEntry(
                    phase="stage1_spec", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="skip",
                    reason=reason,
                ))
                evaluation_rows.append((raw, report, "manual_spec", reason))
                self._record_hypothesis(
                    raw,
                    status="needs_manual_spec",
                    stage="stage1_spec",
                    reason=reason,
                    report=report,
                    strategy_spec=strategy_spec,
                )
                self._upsert_idea(raw, "needs_manual_spec", reason)
                return None

        result.log.append(ResearchLogEntry(
            phase="stage1_spec", title=raw.title, source=raw.source,
            source_url=raw.source_url, verdict="pass",
            reason="Ready for formal research",
            scores={
                **self._evaluation_log_scores(report, evaluation_score),
                "strategy_id": getattr(strategy_spec, "strategy_id", ""),
                "formula": getattr(strategy_spec, "signal_formula_key", ""),
            },
        ))
        evaluation_rows.append((raw, report, "research_queue", "Ready for formal research"))
        self._record_hypothesis(
            raw,
            status="idea_candidate",
            stage="stage1_spec",
            reason="Ready for formal research",
            report=report,
            strategy_spec=strategy_spec,
        )
        self._upsert_idea(raw, "research_queue", "Ready for formal research")
        return raw, report, strategy_spec

    def _stage_two_formal_research(
        self,
        raw: RawStrategy,
        report: Any,
        strategy_spec: Any,
        result: ResearchResult,
        evaluation_rows: List[Tuple[Any, Any, str, str]],
    ) -> Optional[str]:
        validation_report = None
        if self.config.validation_enabled and strategy_spec is not None and self._validator is not None:
            result.log.append(ResearchLogEntry(
                phase="stage2_validation",
                title=raw.title,
                source=raw.source,
                source_url=raw.source_url,
                verdict="info",
                reason="Running HFQ real-data signal validation; this can take several minutes for full A-share data.",
                scores={
                    "strategy_id": getattr(strategy_spec, "strategy_id", ""),
                    "formula": getattr(strategy_spec, "signal_formula_key", ""),
                    "lookback_days": getattr(strategy_spec, "lookback_days", 0),
                    "horizon_days": getattr(strategy_spec, "horizon_days", 0),
                },
            ))
            vreport = self._validator.validate(strategy_spec)
            result.validated += 1
            vreport = self._append_ic_decay_warning(result, raw, vreport)
            validation_report = vreport
            if vreport.status == "error" or not vreport.fdr_significant or vreport.rank_ic < 0.02:
                result.log.append(ResearchLogEntry(
                    phase="stage2_validation", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="fail",
                    reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                    scores={"rank_ic": vreport.rank_ic, "hit_rate": vreport.hit_rate},
                ))
                evaluation_rows.append((raw, report, "validation_failed", f"Validation failed: IC={vreport.rank_ic:.4f}"))
                self._record_hypothesis(
                    raw,
                    status="validation_failed",
                    stage="stage2_validation",
                    reason=f"Validation failed but continuing full research: IC={vreport.rank_ic:.4f}",
                    report=report,
                    validation_report=vreport,
                    strategy_spec=strategy_spec,
                )
                self._upsert_idea(raw, "validation_failed", f"Validation failed but continuing full research: IC={vreport.rank_ic:.4f}")
            else:
                result.validated_passed += 1
                result.log.append(ResearchLogEntry(
                    phase="stage2_validation", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="pass",
                    reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                    scores={"rank_ic": vreport.rank_ic, "hit_rate": vreport.hit_rate},
                ))
                self._record_hypothesis(
                    raw,
                    status="validated",
                    stage="stage2_validation",
                    reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                    report=report,
                    validation_report=vreport,
                    strategy_spec=strategy_spec,
                )
                self._upsert_idea(raw, "validated", f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}")

        strategy_id = self.integrator.integrate(raw, report, spec=strategy_spec)
        if strategy_id:
            self._attach_validation_gate(strategy_id, validation_report)
            result.integrated += 1
            result.log.append(ResearchLogEntry(
                phase="stage2_integrate", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="pass",
                reason=f"Integrated as {strategy_id}",
                scores={
                    **self._evaluation_log_scores(report, self._evaluation_score(report)),
                    "type": report.strategy_type,
                },
            ))
            self._record_hypothesis(
                raw,
                status="candidate",
                stage="stage2_integrate",
                reason=f"Integrated as {strategy_id}",
                strategy_id=strategy_id,
                report=report,
                validation_report=validation_report,
                strategy_spec=strategy_spec,
            )
            self._attach_fast_research_conclusion(strategy_id, report, validation_report)
            self._write_promotion_dossier(strategy_id, raw, report, validation_report, result.run_id, strategy_spec)
            self._upsert_idea(raw, "candidate", f"Integrated as {strategy_id}")
            return strategy_id

        result.errors.append(f"Integration failed for '{raw.title}'")
        result.log.append(ResearchLogEntry(
            phase="stage2_integrate", title=raw.title, source=raw.source,
            source_url=raw.source_url, verdict="error",
            reason="Integration failed",
        ))
        evaluation_rows.append((raw, report, "error", "Integration failed"))
        self._record_hypothesis(
            raw,
            status="error",
            stage="stage2_integrate",
            reason="Integration failed",
            report=report,
            strategy_spec=strategy_spec,
        )
        self._upsert_idea(raw, "error", "Integration failed")
        return None

    def _admission_rejection_reason(self, report: Any, evaluation_score: float) -> str:
        reason_parts = [
            f"admission={evaluation_score:.1f} < {self.config.evaluation_threshold}",
            f"suitability={report.suitability_score:.1f}",
        ]
        if report.data_requirement == "high-frequency" and not report.daily_adaptable:
            reason_parts.append("high-frequency, not daily-adaptable")
        if getattr(report, "rejection_reason", ""):
            reason_parts.append(report.rejection_reason)
        return "; ".join(reason_parts)

    def _upsert_idea(self, raw: RawStrategy, status: str, reason: str = "") -> None:
        try:
            self.research_store.upsert_idea(raw, status=status, reason=reason)
        except Exception as e:
            logger.warning(f"Failed to upsert research idea '{raw.title}': {e}")

    @staticmethod
    def _raw_from_idea_record(row: Dict[str, Any]) -> RawStrategy:
        return RawStrategy(
            title=str(row.get("title", "")),
            description=str(row.get("description", "")),
            source=str(row.get("source", "")),
            source_url=str(row.get("source_url", "")),
            authors=str(row.get("authors") or "") or None,
            published_date=str(row.get("published_date") or "") or None,
            metadata=dict(row.get("metadata") or {}),
        )

    @staticmethod
    def _evaluation_log_scores(report: Any, evaluation_score: float) -> Dict[str, Any]:
        return {
            "suitability": report.suitability_score,
            "admission": evaluation_score,
            "signal_quality": getattr(report, "signal_quality_score", 0.0),
            "complexity": report.complexity_score,
            "edge": report.estimated_edge,
        }

    def _record_hypothesis(
        self,
        raw: RawStrategy,
        status: str,
        stage: str,
        reason: str,
        strategy_id: str = "",
        report: Any = None,
        validation_report: Any = None,
        strategy_spec: Any = None,
    ) -> None:
        try:
            self.research_store.upsert_hypothesis(
                {
                    "hypothesis_id": StrategyScout.hash_strategy(raw),
                    "strategy_id": strategy_id,
                    "title": raw.title,
                    "source": raw.source,
                    "source_url": raw.source_url,
                    "thesis": raw.description,
                    "status": status,
                    "stage": stage,
                    "decision_reason": reason,
                    "metrics": self._hypothesis_metrics(report, validation_report),
                    "evidence": {
                        "source": raw.source,
                        "source_url": raw.source_url,
                        "authors": raw.authors or "",
                        "published_date": raw.published_date or "",
                        "metadata": dict(raw.metadata or {}),
                        "discovery_quality": discovery_quality(raw),
                        "strategy_spec": self._strategy_spec_dict(strategy_spec),
                    },
                }
            )
        except Exception as e:
            logger.warning(f"Failed to record research hypothesis '{raw.title}': {e}")

    @staticmethod
    def _hypothesis_metrics(report: Any = None, validation_report: Any = None) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {}
        if report is not None:
            for field in (
                "suitability_score",
                "complexity_score",
                "estimated_edge",
                "economic_rationale_score",
                "factor_uniqueness_score",
                "data_availability_score",
                "implementation_score",
                "overfit_risk_score",
                "cost_capacity_score",
                "regime_robustness_score",
                "admission_score",
                "signal_quality_score",
                "research_confidence_score",
                "data_risk_score",
                "bias_risk_score",
            ):
                metrics[field] = getattr(report, field, 0.0)
            metrics["strategy_type"] = getattr(report, "strategy_type", "")
            metrics["risk_flags"] = list(getattr(report, "risk_flags", []) or [])
            metrics["required_data_fields"] = list(getattr(report, "required_data_fields", []) or [])
            metrics["validation_tests"] = list(getattr(report, "validation_tests", []) or [])
            metrics["score_breakdown"] = dict(getattr(report, "score_breakdown", {}) or {})
        if validation_report is not None:
            for field in (
                "rank_ic",
                "rank_ic_ir",
                "rank_ic_tstat",
                "rank_ic_p_value",
                "fdr_adjusted_p",
                "fdr_significant",
                "hit_rate",
                "long_short_spread",
                "n_observations",
                "ff_alpha_monthly",
                "ff_alpha_tstat",
                "ff_r2",
                "fama_macbeth_tstat",
                "universe_size",
                "universe_source",
                "data_rows",
                "data_symbol_count",
            ):
                metrics[field] = getattr(validation_report, field, 0.0)
            metrics["p_value"] = getattr(
                validation_report,
                "p_value",
                getattr(
                    validation_report,
                    "rank_ic_p_value",
                    getattr(validation_report, "fdr_adjusted_p", 0.0),
                ),
            )
            metrics["ic_decay"] = list(getattr(validation_report, "ic_decay", []) or [])
            metrics["data_start"] = getattr(validation_report, "data_start", "")
            metrics["data_end"] = getattr(validation_report, "data_end", "")
            metrics["universe_sample"] = list(getattr(validation_report, "universe_sample", []) or [])
            metrics["portfolio_diagnostics"] = dict(getattr(validation_report, "portfolio_diagnostics", {}) or {})
        return metrics

    def _write_promotion_dossier(
        self,
        strategy_id: str,
        raw: RawStrategy,
        report: Any,
        validation_report: Any = None,
        run_id: Optional[str] = None,
        strategy_spec: Any = None,
    ) -> None:
        if self._artifact_store is None or not hasattr(self._artifact_store, "save_json"):
            return
        try:
            name = f"promotion_dossier_{strategy_id}"
            meta = self._artifact_store.save_json(
                run_id or "research_pipeline",
                name,
                self._promotion_dossier(strategy_id, raw, report, validation_report, strategy_spec),
            )
            self._attach_promotion_dossier_artifact(strategy_id, meta)
        except Exception as e:
            logger.warning(f"Failed to write promotion dossier for {strategy_id}: {e}")

    def _promotion_dossier(
        self,
        strategy_id: str,
        raw: RawStrategy,
        report: Any,
        validation_report: Any = None,
        strategy_spec: Any = None,
    ) -> Dict[str, Any]:
        return {
            "strategy_id": strategy_id,
            "decision": "candidate_admission",
            "next_action": "walk_forward_or_paper_review",
            "hypothesis": {
                "hypothesis_id": StrategyScout.hash_strategy(raw),
                "title": raw.title,
                "thesis": raw.description,
                "source": raw.source,
                "source_url": raw.source_url,
                "authors": raw.authors or "",
                "published_date": raw.published_date or "",
            },
            "evaluation": self._hypothesis_metrics(report),
            "validation": self._hypothesis_metrics(None, validation_report) if validation_report is not None else {},
            "risk_flags": list(getattr(report, "risk_flags", []) or []),
            "discovery_quality": discovery_quality(raw),
            "required_data_fields": list(getattr(report, "required_data_fields", []) or []),
            "validation_tests": list(getattr(report, "validation_tests", []) or []),
            "strategy_spec": self._strategy_spec_dict(strategy_spec),
            "score_breakdown": dict(getattr(report, "score_breakdown", {}) or {}),
            "summary": getattr(report, "summary", ""),
        }

    @staticmethod
    def _strategy_spec_dict(strategy_spec: Any = None) -> Dict[str, Any]:
        if strategy_spec is None:
            return {}
        return {
            "strategy_id": getattr(strategy_spec, "strategy_id", ""),
            "strategy_type": getattr(strategy_spec, "strategy_type", ""),
            "signal_formula_key": getattr(strategy_spec, "signal_formula_key", ""),
            "universe": list(getattr(strategy_spec, "universe", []) or []),
            "horizon_days": getattr(strategy_spec, "horizon_days", 0),
            "lookback_days": getattr(strategy_spec, "lookback_days", 0),
            "execution_lag_days": getattr(strategy_spec, "execution_lag_days", 0),
            "required_fields": list(getattr(strategy_spec, "required_fields", []) or []),
            "status": getattr(strategy_spec, "status", ""),
            "reason": getattr(strategy_spec, "reason", ""),
        }

    def _attach_promotion_dossier_artifact(self, strategy_id: str, meta: Dict[str, Any]) -> None:
        if not meta:
            return
        candidate = self.research_store.get_candidate(strategy_id)
        if candidate is None:
            return
        research_meta = dict(candidate.get("research_meta") or {})
        research_meta["promotion_dossier_artifact"] = dict(meta)
        candidate["research_meta"] = research_meta
        self.research_store.upsert_candidate(candidate)

    def _write_candidate_scorecard(self, run_id: Optional[str]) -> None:
        if self._artifact_store is None or not hasattr(self._artifact_store, "save_table"):
            return
        if not hasattr(self.research_store, "list_hypotheses"):
            return
        try:
            rows = [self._scorecard_row(item) for item in self.research_store.list_hypotheses()]
            if not rows:
                return
            rows.sort(key=self._scorecard_sort_key)
            self._artifact_store.save_table(run_id or "research_pipeline", "candidate_scorecard", rows)
        except Exception as e:
            logger.warning(f"Failed to write candidate scorecard: {e}")

    @staticmethod
    def _scorecard_row(item: Dict[str, Any]) -> Dict[str, Any]:
        metrics = item.get("metrics") or {}
        return {
            "hypothesis_id": item.get("hypothesis_id", ""),
            "strategy_id": item.get("strategy_id", ""),
            "title": item.get("title", ""),
            "status": item.get("status", ""),
            "stage": item.get("stage", ""),
            "decision_reason": item.get("decision_reason", ""),
            "suitability_score": float(metrics.get("suitability_score", 0.0) or 0.0),
            "admission_score": float(metrics.get("admission_score", 0.0) or 0.0),
            "signal_quality_score": float(metrics.get("signal_quality_score", 0.0) or 0.0),
            "research_confidence_score": float(metrics.get("research_confidence_score", 0.0) or 0.0),
            "estimated_edge": float(metrics.get("estimated_edge", 0.0) or 0.0),
            "rank_ic": float(metrics.get("rank_ic", 0.0) or 0.0),
            "hit_rate": float(metrics.get("hit_rate", 0.0) or 0.0),
            "validation_tests": list(metrics.get("validation_tests", []) or []),
            "risk_flags": list(metrics.get("risk_flags", []) or []),
        }

    @staticmethod
    def _scorecard_sort_key(row: Dict[str, Any]) -> tuple:
        status_rank = {
            "candidate": 0,
            "idea_candidate": 1,
            "validated": 1,
            "needs_more_validation": 2,
            "needs_manual_spec": 3,
            "rejected": 4,
            "error": 5,
            "skipped": 6,
        }.get(row.get("status", ""), 9)
        score = float(row.get("admission_score", 0.0) or row.get("suitability_score", 0.0) or 0.0)
        return (status_rank, -score, row.get("title", ""))

    @staticmethod
    def _evaluation_score(report: Any) -> float:
        score = getattr(report, "admission_score", 0.0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        if score > 0.0:
            return score
        return float(getattr(report, "suitability_score", 0.0) or 0.0)

    @staticmethod
    def _discovery_summary(raw_strategies: List[RawStrategy]) -> Dict[str, Any]:
        if not raw_strategies:
            return {"source_count": 0, "avg_discovery_score": 0.0}
        sources = sorted(set(raw.source for raw in raw_strategies if raw.source))
        scores = [discovery_score(raw) for raw in raw_strategies if discovery_score(raw) > 0]
        return {
            "source_count": len(sources),
            "sources": sources,
            "avg_discovery_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "min_discovery_score": round(min(scores), 2) if scores else 0.0,
            "max_discovery_score": round(max(scores), 2) if scores else 0.0,
        }

    def _stage_strategy_items(
        self,
        strategy_ids: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        max_strategies: Optional[int] = None,
    ) -> List[Tuple[str, None]]:
        if strategy_ids:
            ordered = []
            seen = set()
            for sid in strategy_ids:
                text = str(sid).strip()
                if text and text not in seen:
                    ordered.append((text, None))
                    seen.add(text)
            return ordered[: int(max_strategies)] if max_strategies is not None else ordered

        statuses = statuses or ["candidate"]
        selected = []
        seen = set()
        if self.research_store is not None and hasattr(self.research_store, "list_by_status"):
            for status in statuses:
                for row in self.research_store.list_by_status(status):
                    sid = str(row.get("id") or "").strip()
                    if sid and sid not in seen:
                        selected.append((sid, None))
                        seen.add(sid)
        if not selected and self.research_store is not None and hasattr(self.research_store, "list_hypotheses"):
            allowed_statuses = set(statuses)
            for row in self.research_store.list_hypotheses():
                if str(row.get("status") or "") not in allowed_statuses:
                    continue
                sid = str(row.get("strategy_id") or "").strip()
                if sid and sid not in seen:
                    selected.append((sid, None))
                    seen.add(sid)
        if max_strategies is not None:
            selected = selected[: int(max_strategies)]
        return selected

    def _run_backtests(
        self,
        strategy_items: List[Any],
        result: ResearchResult,
        benchmark_data: Any = None,
        run_walkforward: bool = True,
    ) -> None:
        if self._backtest_fn is None:
            logger.warning("No backtest function injected — skipping backtests")
            return
        for item in strategy_items:
            if isinstance(item, tuple):
                sid, raw = item
            else:
                sid, raw = item, None
            try:
                symbols = self._strategy_symbols(sid)
                final_status = ""
                final_reasons = []
                wf_result = None
                validation_failure = self._validation_failure_reason(sid)
                if validation_failure:
                    final_status = "rejected"
                    final_reasons.append(validation_failure)
                rejected_before_backtest = result.rejected
                result.log.append(ResearchLogEntry(
                    phase="backtest",
                    title=sid,
                    source="",
                    source_url="",
                    verdict="info",
                    reason="Running strict project Backtester with execution constraints.",
                    scores={
                        "start": self.config.default_backtest_start,
                        "end": self.config.default_backtest_end,
                    },
                ))
                self._backtest_fn(sid, result, self.config, self.integrator, self.pool)
                strict_verdict, strict_reason = self._attach_strict_backtest_conclusion(sid)
                if strict_verdict == "fail":
                    final_status = "rejected"
                    final_reasons.append(strict_reason)

                if run_walkforward and self._rigor_hub is not None and self.config.rigor_enabled:
                    split_benchmark_data = benchmark_data
                    if split_benchmark_data is None:
                        split_benchmark_data = self._load_benchmark_data(
                            symbols,
                            self.config.default_backtest_start,
                            self.config.default_backtest_end,
                        )
                    result.log.append(ResearchLogEntry(
                        phase="rigor",
                        title=sid,
                        source="",
                        source_url="",
                        verdict="info",
                        reason="Running walk-forward strict audit.",
                        scores={
                            "symbols": len(symbols),
                            "start": self.config.default_backtest_start,
                            "end": self.config.default_backtest_end,
                        },
                    ))
                    wf_result = self._run_walkforward(
                        sid,
                        symbols,
                        self.config.default_backtest_start,
                        self.config.default_backtest_end,
                        split_benchmark_data,
                    )
                    if not wf_result.is_viable:
                        reason = f"Walk-forward strict audit failed: worst_oos_sharpe={wf_result.worst_oos_sharpe:.2f}"
                        self._attach_walkforward_result(sid, wf_result, "fail", reason)
                        self._attach_walkforward_audit_conclusion(sid, wf_result, "fail", reason)
                        final_status = "rejected"
                        final_reasons.append(reason)
                        result.log.append(ResearchLogEntry(
                            phase="rigor",
                            title=sid,
                            source="",
                            source_url="",
                            verdict="fail",
                            reason=reason,
                            scores={
                                "aggregate_oos_sharpe": getattr(wf_result, "aggregate_oos_sharpe", 0.0),
                                "worst_oos_sharpe": getattr(wf_result, "worst_oos_sharpe", 0.0),
                                "pct_profitable_splits": getattr(wf_result, "pct_profitable_splits", 0.0),
                                "deflated_sharpe_ratio": getattr(wf_result, "deflated_sharpe_ratio", None),
                            },
                        ))
                    else:
                        dsr = getattr(wf_result, "deflated_sharpe_ratio", None)
                        if dsr is not None and dsr < 0.95:
                            reason = f"Walk-forward strict audit warning: dsr={dsr:.2f} < 0.95"
                            self._attach_walkforward_result(sid, wf_result, "warn", reason)
                            self._attach_walkforward_audit_conclusion(sid, wf_result, "warn", reason)
                            if final_status != "rejected":
                                final_status = "needs_more_validation"
                                final_reasons.append(reason)
                            result.log.append(ResearchLogEntry(
                                phase="rigor", title=sid, source="", source_url="",
                                verdict="warning", reason=reason,
                                scores={"deflated_sharpe_ratio": dsr},
                            ))
                        else:
                            reason = "Walk-forward strict audit passed"
                            self._attach_walkforward_result(sid, wf_result, "pass", reason)
                            self._attach_walkforward_audit_conclusion(sid, wf_result, "pass", reason)
                            result.walkforward_passed += 1
                elif run_walkforward:
                    self._attach_research_stage_conclusion(
                        sid,
                        "walkforward_strict_audit",
                        "Walk-forward strict audit",
                        "not_run",
                        "本轮未运行 walk-forward strict audit；不能形成样本外稳定性通过结论。",
                        {},
                        "滚动 OOS split 重放 strict Backtester，用于最终稳定性审计。",
                    )
                final_reason = "; ".join(final_reasons)
                if final_status == "rejected":
                    if self._candidate_status(sid) != "rejected":
                        if self.pool.reject(sid, reason=final_reason) and result.rejected == rejected_before_backtest:
                            result.rejected += 1
                    self._archive_rejected_strategy(sid, final_reason)
                    self._update_hypothesis_status_for_strategy(
                        sid,
                        "rejected",
                        "go_no_go",
                        f"{final_reason}; strict Backtester executed for audit",
                    )
                    if raw is not None:
                        self._upsert_idea(raw, "rejected", final_reason)
                elif final_status == "needs_more_validation" and self._candidate_status(sid) == "candidate":
                    dsr_value = 0.0
                    try:
                        dsr_value = float(getattr(wf_result, "deflated_sharpe_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        dsr_value = 0.0
                    self._mark_needs_more_validation(sid, dsr_value, final_reason)
                self._attach_final_research_conclusion(sid, self._candidate_status(sid) or final_status or "candidate", final_reason)
            except Exception as e:
                logger.error(f"Backtest failed for {sid}: {e}")
                result.errors.append(f"Backtest error for {sid}: {e}")
                self.pool.reject(sid, reason=f"Backtest exception: {e}")
                self._archive_rejected_strategy(sid, f"Backtest exception: {e}")
                self._update_hypothesis_status_for_strategy(sid, "rejected", "backtest", f"Backtest exception: {e}")
                self._attach_final_research_conclusion(sid, "rejected", f"Backtest exception: {e}")
                if raw is not None:
                    self._upsert_idea(raw, "rejected", f"Backtest exception: {e}")
                result.rejected += 1

    def _run_walkforward_audits(self, strategy_items: List[Any], result: ResearchResult, benchmark_data: Any = None) -> None:
        if self._rigor_hub is None or not self.config.rigor_enabled:
            for item in strategy_items:
                sid = item[0] if isinstance(item, tuple) else item
                self._attach_research_stage_conclusion(
                    sid,
                    "walkforward_strict_audit",
                    "Walk-forward strict audit",
                    "not_run",
                    "本轮未配置 walk-forward strict audit runner；不能形成样本外稳定性通过结论。",
                    {},
                    "滚动 OOS split 重放 strict Backtester，用于最终稳定性审计。",
                )
            result.errors.append("Walk-forward audit runner is not configured")
            return

        for item in strategy_items:
            if isinstance(item, tuple):
                sid, raw = item
            else:
                sid, raw = item, None
            try:
                symbols = self._strategy_symbols(sid)
                split_benchmark_data = benchmark_data
                if split_benchmark_data is None:
                    split_benchmark_data = self._load_benchmark_data(
                        symbols,
                        self.config.default_backtest_start,
                        self.config.default_backtest_end,
                    )
                result.log.append(ResearchLogEntry(
                    phase="rigor",
                    title=sid,
                    source="",
                    source_url="",
                    verdict="info",
                    reason="Running standalone walk-forward strict audit.",
                    scores={
                        "symbols": len(symbols),
                        "start": self.config.default_backtest_start,
                        "end": self.config.default_backtest_end,
                    },
                ))
                wf_result = self._run_walkforward(
                    sid,
                    symbols,
                    self.config.default_backtest_start,
                    self.config.default_backtest_end,
                    split_benchmark_data,
                )
                final_status = ""
                final_reasons = []
                rejected_before = result.rejected
                if not wf_result.is_viable:
                    reason = f"Walk-forward strict audit failed: worst_oos_sharpe={wf_result.worst_oos_sharpe:.2f}"
                    self._attach_walkforward_result(sid, wf_result, "fail", reason)
                    self._attach_walkforward_audit_conclusion(sid, wf_result, "fail", reason)
                    final_status = "rejected"
                    final_reasons.append(reason)
                    result.log.append(ResearchLogEntry(
                        phase="rigor",
                        title=sid,
                        source="",
                        source_url="",
                        verdict="fail",
                        reason=reason,
                        scores={
                            "aggregate_oos_sharpe": getattr(wf_result, "aggregate_oos_sharpe", 0.0),
                            "worst_oos_sharpe": getattr(wf_result, "worst_oos_sharpe", 0.0),
                            "pct_profitable_splits": getattr(wf_result, "pct_profitable_splits", 0.0),
                            "deflated_sharpe_ratio": getattr(wf_result, "deflated_sharpe_ratio", None),
                        },
                    ))
                else:
                    dsr = getattr(wf_result, "deflated_sharpe_ratio", None)
                    if dsr is not None and dsr < 0.95:
                        reason = f"Walk-forward strict audit warning: dsr={dsr:.2f} < 0.95"
                        self._attach_walkforward_result(sid, wf_result, "warn", reason)
                        self._attach_walkforward_audit_conclusion(sid, wf_result, "warn", reason)
                        if self._candidate_status(sid) == "candidate":
                            final_status = "needs_more_validation"
                            final_reasons.append(reason)
                        result.log.append(ResearchLogEntry(
                            phase="rigor", title=sid, source="", source_url="",
                            verdict="warning", reason=reason,
                            scores={"deflated_sharpe_ratio": dsr},
                        ))
                    else:
                        reason = "Walk-forward strict audit passed"
                        self._attach_walkforward_result(sid, wf_result, "pass", reason)
                        self._attach_walkforward_audit_conclusion(sid, wf_result, "pass", reason)
                        result.walkforward_passed += 1

                final_reason = "; ".join(final_reasons)
                if final_status == "rejected":
                    if self._candidate_status(sid) != "rejected":
                        if self.pool.reject(sid, reason=final_reason) and result.rejected == rejected_before:
                            result.rejected += 1
                    self._archive_rejected_strategy(sid, final_reason)
                    self._update_hypothesis_status_for_strategy(sid, "rejected", "go_no_go", final_reason)
                    if raw is not None:
                        self._upsert_idea(raw, "rejected", final_reason)
                elif final_status == "needs_more_validation":
                    dsr_value = 0.0
                    try:
                        dsr_value = float(getattr(wf_result, "deflated_sharpe_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        dsr_value = 0.0
                    self._mark_needs_more_validation(sid, dsr_value, final_reason)
                self._attach_final_research_conclusion(sid, self._candidate_status(sid) or final_status or "candidate", final_reason)
            except Exception as e:
                logger.error(f"Walk-forward audit failed for {sid}: {e}")
                result.errors.append(f"Walk-forward audit error for {sid}: {e}")
                self._attach_walkforward_audit_conclusion(
                    sid,
                    type("WalkForwardError", (), {
                        "aggregate_oos_sharpe": 0.0,
                        "worst_oos_sharpe": 0.0,
                        "pct_profitable_splits": 0.0,
                        "deflated_sharpe_ratio": None,
                        "splits": [],
                    })(),
                    "fail",
                    f"Walk-forward audit exception: {e}",
                )
                self._attach_final_research_conclusion(sid, "rejected", f"Walk-forward audit exception: {e}")

    def _candidate_status(self, strategy_id: str) -> str:
        if self.research_store is not None:
            try:
                info = self.research_store.get_candidate(strategy_id)
                if info is not None:
                    return str(info.get("status", ""))
            except Exception:
                pass
        for entry in self.integrator.registry.values():
            if entry.get("id") == strategy_id:
                return str(entry.get("status", ""))
        return ""

    def _attach_fast_research_conclusion(self, strategy_id: str, report: Any, validation_report: Any = None) -> None:
        evaluation_score = self._evaluation_score(report)
        metrics = {
            "admission_score": evaluation_score,
            "signal_quality_score": getattr(report, "signal_quality_score", 0.0),
            "research_confidence_score": getattr(report, "research_confidence_score", 0.0),
        }
        if validation_report is None:
            verdict = "warn"
            conclusion = (
                f"快研究完成 admission={evaluation_score:.2f}，但本轮未运行 HFQ 信号验证；"
                "只能进入 strict 回测做执行层审计，不能作为独立通过结论。"
            )
        else:
            rank_ic = _float_or_default(getattr(validation_report, "rank_ic", 0.0), 0.0)
            fdr = _float_or_default(getattr(validation_report, "fdr_adjusted_p", 1.0), 1.0)
            hit_rate = _float_or_default(getattr(validation_report, "hit_rate", 0.0), 0.0)
            metrics.update({"rank_ic": rank_ic, "fdr_adjusted_p": fdr, "hit_rate": hit_rate})
            if getattr(validation_report, "status", "") == "error" or not bool(getattr(validation_report, "fdr_significant", False)) or rank_ic < 0.02:
                verdict = "fail"
                conclusion = (
                    f"快研究未通过：Rank IC={rank_ic:.4f}、FDR={fdr:.4f}、hit_rate={hit_rate:.2%}；"
                    "继续 strict 回测和 walk-forward audit 仅用于审计，不作为上线依据。"
                )
            else:
                verdict = "pass"
                conclusion = (
                    f"快研究通过：Rank IC={rank_ic:.4f}、FDR={fdr:.4f}、hit_rate={hit_rate:.2%}；"
                    "可以进入 strict 回测检查真实交易约束。"
                )
        self._attach_research_stage_conclusion(
            strategy_id,
            "fast_research",
            "快研究",
            verdict,
            conclusion,
            metrics,
            "来源/admission、StrategySpec、HFQ 信号验证和向量化组合诊断。",
        )

    def _attach_strict_backtest_conclusion(self, strategy_id: str) -> Tuple[str, str]:
        strict = self._strict_backtest_for_strategy(strategy_id)
        metrics = strict.get("metrics") or {}
        diagnostics = strict.get("diagnostics") or {}
        sharpe = _optional_float(metrics.get("sharpe"))
        cagr = _optional_float(metrics.get("cagr"))
        max_dd = _optional_float(metrics.get("max_drawdown_pct"))
        trades = metrics.get("total_trades")
        threshold = float(getattr(self.config, "backtest_sharpe_threshold", 0.5) or 0.5)
        payload = {
            "sharpe": sharpe,
            "cagr": cagr,
            "max_drawdown_pct": max_dd,
            "total_trades": trades,
            "total_commission": diagnostics.get("total_commission"),
            "insufficient_cash_rejected_orders": (diagnostics.get("rejection_counts") or {}).get("insufficient_cash", 0),
        }
        if not strict or sharpe is None:
            verdict = "warn"
            conclusion = "strict Backtester 未返回结构化结果；本阶段不能形成通过结论。"
        elif sharpe < threshold:
            verdict = "fail"
            conclusion = (
                f"strict 回测未通过：Sharpe={sharpe:.2f} < {threshold:.2f}，"
                f"CAGR={_percent_text(cagr)}，MaxDD={_percent_text(max_dd)}；策略不进入候选池。"
            )
        elif cagr is not None and cagr <= 0:
            verdict = "warn"
            conclusion = (
                f"strict 回测边际：Sharpe={sharpe:.2f} 达标但 CAGR={_percent_text(cagr)}，"
                "需要 walk-forward audit 再确认稳定性。"
            )
        else:
            verdict = "pass"
            conclusion = (
                f"strict 回测通过：Sharpe={sharpe:.2f}，CAGR={_percent_text(cagr)}，"
                f"MaxDD={_percent_text(max_dd)}；进入 walk-forward strict audit。"
            )
        self._attach_research_stage_conclusion(
            strategy_id,
            "strict_backtest",
            "严格回测",
            verdict,
            conclusion,
            payload,
            "项目 Backtester，含 T+1、停牌、涨跌停、手数、佣金、滑点、现金和成交约束。",
        )
        return verdict, conclusion

    def _attach_walkforward_audit_conclusion(self, strategy_id: str, wf_result: Any, verdict: str, reason: str) -> None:
        aggregate = _float_or_default(getattr(wf_result, "aggregate_oos_sharpe", 0.0), 0.0)
        worst = _float_or_default(getattr(wf_result, "worst_oos_sharpe", 0.0), 0.0)
        pct_profitable = _float_or_default(getattr(wf_result, "pct_profitable_splits", 0.0), 0.0)
        dsr = _optional_float(getattr(wf_result, "deflated_sharpe_ratio", None))
        if verdict == "pass":
            conclusion = (
                f"walk-forward strict audit 通过：aggregate OOS Sharpe={aggregate:.2f}，"
                f"worst={worst:.2f}，盈利 split={pct_profitable:.0%}。"
            )
        elif verdict == "warn":
            dsr_text = f"{dsr:.2f}" if dsr is not None else "n/a"
            conclusion = (
                f"walk-forward strict audit 仅警告：aggregate OOS Sharpe={aggregate:.2f}，"
                f"worst={worst:.2f}，DSR={dsr_text}；需要更多验证。"
            )
        else:
            conclusion = (
                f"walk-forward strict audit 未通过：aggregate OOS Sharpe={aggregate:.2f}，"
                f"worst={worst:.2f}，盈利 split={pct_profitable:.0%}；{reason}"
            )
        self._attach_research_stage_conclusion(
            strategy_id,
            "walkforward_strict_audit",
            "Walk-forward strict audit",
            verdict,
            conclusion,
            {
                "aggregate_oos_sharpe": aggregate,
                "worst_oos_sharpe": worst,
                "pct_profitable_splits": pct_profitable,
                "deflated_sharpe_ratio": dsr,
                "n_splits": len(list(getattr(wf_result, "splits", []) or [])),
            },
            "滚动 OOS split 重放 strict Backtester，用于最终稳定性审计。",
        )

    def _attach_final_research_conclusion(self, strategy_id: str, status: str, reason: str = "") -> None:
        verdict = "pass" if status in {"candidate", "paper_trading_candidate"} else "warn" if status == "needs_more_validation" else "fail"
        if status == "rejected":
            conclusion = f"最终 No-Go：{reason or '至少一个正式阶段未通过'}。"
        elif status == "needs_more_validation":
            conclusion = f"最终结论：需要更多验证；{reason or 'walk-forward 或 DSR 未达到上线阈值'}。"
        else:
            conclusion = f"最终状态为 {status}；可进入下一层人工复核、容量和 paper trading 审批。"
        self._attach_research_stage_conclusion(
            strategy_id,
            "final_decision",
            "最终 Go / No-Go",
            verdict,
            conclusion,
            {"status": status, "reason": reason},
            "汇总快研究、strict 回测和 walk-forward strict audit 的结构化结论。",
        )

    def _attach_research_stage_conclusion(
        self,
        strategy_id: str,
        stage_key: str,
        label: str,
        verdict: str,
        conclusion: str,
        scores: Optional[Dict[str, Any]] = None,
        method: str = "",
    ) -> None:
        if self.research_store is None or not hasattr(self.research_store, "list_hypotheses"):
            return
        try:
            for row in self.research_store.list_hypotheses():
                if row.get("strategy_id") != strategy_id:
                    continue
                updated = dict(row)
                metrics = dict(updated.get("metrics") or {})
                stages = dict(metrics.get("research_stage_conclusions") or {})
                stages[stage_key] = {
                    "label": label,
                    "verdict": verdict,
                    "conclusion": conclusion,
                    "method": method,
                    "scores": dict(scores or {}),
                }
                metrics["research_stage_conclusions"] = stages
                updated["metrics"] = metrics
                self.research_store.upsert_hypothesis(updated)
        except Exception as e:
            logger.warning(f"Failed to attach stage conclusion for {strategy_id}/{stage_key}: {e}")

    def _strict_backtest_for_strategy(self, strategy_id: str) -> Dict[str, Any]:
        if self.research_store is None or not hasattr(self.research_store, "list_hypotheses"):
            return {}
        try:
            for row in self.research_store.list_hypotheses():
                if row.get("strategy_id") == strategy_id:
                    strict = (row.get("metrics") or {}).get("strict_backtest")
                    return dict(strict or {}) if isinstance(strict, dict) else {}
        except Exception:
            return {}
        return {}

    def _attach_validation_gate(self, strategy_id: str, validation_report: Any = None) -> None:
        if validation_report is None:
            return
        gate_status = self._validation_gate_status(validation_report)
        gate = {
            "status": gate_status,
            "rank_ic": _float_or_default(getattr(validation_report, "rank_ic", 0.0), 0.0),
            "rank_ic_ir": _float_or_default(getattr(validation_report, "rank_ic_ir", 0.0), 0.0),
            "fdr_adjusted_p": _float_or_default(getattr(validation_report, "fdr_adjusted_p", 1.0), 1.0),
            "hit_rate": _float_or_default(getattr(validation_report, "hit_rate", 0.0), 0.0),
            "reason": self._validation_gate_reason(validation_report),
        }
        if self.research_store is not None:
            try:
                candidate = self.research_store.get_candidate(strategy_id)
                if candidate is not None:
                    meta = candidate.setdefault("research_meta", {})
                    meta["validation_gate"] = gate
                    self.research_store.upsert_candidate(candidate)
            except Exception as e:
                logger.warning(f"Failed to attach validation gate for {strategy_id}: {e}")
        for entry in self.integrator.registry.values():
            if entry.get("id") == strategy_id:
                entry.setdefault("research_meta", {})["validation_gate"] = gate
                break

    @staticmethod
    def _validation_gate_status(validation_report: Any) -> str:
        if getattr(validation_report, "status", "") == "error":
            return "failed"
        if not bool(getattr(validation_report, "fdr_significant", False)):
            return "failed"
        try:
            if float(getattr(validation_report, "rank_ic", 0.0) or 0.0) < 0.02:
                return "failed"
        except (TypeError, ValueError):
            return "failed"
        return "passed"

    @staticmethod
    def _validation_gate_reason(validation_report: Any) -> str:
        rank_ic = _float_or_default(getattr(validation_report, "rank_ic", 0.0), 0.0)
        fdr = _float_or_default(getattr(validation_report, "fdr_adjusted_p", 1.0), 1.0)
        if getattr(validation_report, "status", "") == "error":
            errors = "; ".join(str(item) for item in getattr(validation_report, "errors", []) or [])
            return f"Validation error: {errors or 'unknown'}"
        if not bool(getattr(validation_report, "fdr_significant", False)):
            return f"Validation failed: FDR={fdr:.4f}"
        if rank_ic < 0.02:
            return f"Validation failed: IC={rank_ic:.4f}"
        return f"Validation passed: IC={rank_ic:.4f}, FDR={fdr:.4f}"

    def _validation_failure_reason(self, strategy_id: str) -> str:
        gate = self._candidate_validation_gate(strategy_id)
        if not gate or gate.get("status") != "failed":
            return ""
        return str(gate.get("reason") or "Validation gate failed")

    def _candidate_validation_gate(self, strategy_id: str) -> Dict[str, Any]:
        if self.research_store is not None:
            try:
                candidate = self.research_store.get_candidate(strategy_id)
                if candidate is not None:
                    return dict((candidate.get("research_meta") or {}).get("validation_gate") or {})
            except Exception:
                pass
        for entry in self.integrator.registry.values():
            if entry.get("id") == strategy_id:
                return dict((entry.get("research_meta") or {}).get("validation_gate") or {})
        return {}

    def _archive_rejected_strategy(self, strategy_id: str, reason: str = "") -> None:
        strategies_dir_value = getattr(self.integrator, "strategies_dir", None)
        if strategies_dir_value is None:
            return
        strategies_dir = Path(strategies_dir_value)
        source_dir = strategies_dir / strategy_id
        rejected_strategy_root = strategies_dir.parent / "rejected_strategy"
        archive_dir = rejected_strategy_root / strategy_id
        try:
            if source_dir.exists():
                rejected_strategy_root.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_dir, archive_dir, dirs_exist_ok=True)
                shutil.rmtree(source_dir)
            self._mark_rejected_strategy_config(archive_dir, reason)
            self._attach_rejected_strategy_archive(strategy_id, archive_dir, reason)
        except Exception as e:
            logger.warning(f"Failed to archive rejected strategy {strategy_id}: {e}")

    def _mark_rejected_strategy_config(self, archive_dir: Path, reason: str = "") -> None:
        config_path = archive_dir / "config.yaml"
        if not config_path.exists():
            return
        try:
            text = config_path.read_text(encoding="utf-8")
            lines = []
            in_strategy = False
            for line in text.splitlines():
                stripped = line.strip()
                if line and not line.startswith((" ", "\t")):
                    in_strategy = stripped == "strategy:"
                if in_strategy and stripped.startswith("enabled:"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}enabled: false"
                elif in_strategy and stripped.startswith("status:"):
                    indent = line[: len(line) - len(line.lstrip())]
                    line = f"{indent}status: rejected"
                lines.append(line)
            suffix = "\n" if text.endswith("\n") else ""
            config_path.write_text("\n".join(lines) + suffix, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to mark rejected strategy config {config_path}: {e}")

    def _attach_rejected_strategy_archive(self, strategy_id: str, archive_dir: Path, reason: str = "") -> None:
        rel_archive = _repo_relative_path(archive_dir)
        if self.research_store is not None:
            try:
                candidate = self.research_store.get_candidate(strategy_id)
                if candidate is not None:
                    meta = candidate.setdefault("research_meta", {})
                    meta["rejected_strategy_dir"] = rel_archive
                    meta["strategy_pool_archived"] = True
                    if reason:
                        meta["rejection_reason"] = reason
                    self.research_store.upsert_candidate(candidate)
            except Exception as e:
                logger.warning(f"Failed to attach rejected strategy archive for {strategy_id}: {e}")
        for entry in self.integrator.registry.values():
            if entry.get("id") == strategy_id:
                meta = entry.setdefault("research_meta", {})
                meta["rejected_strategy_dir"] = rel_archive
                meta["strategy_pool_archived"] = True
                if reason:
                    meta["rejection_reason"] = reason
                break

    def _strategy_symbols(self, strategy_id: str) -> List[str]:
        entry = self.integrator.get_registry_entry(strategy_id) if hasattr(self.integrator, "get_registry_entry") else None
        meta = dict((entry or {}).get("research_meta") or {})
        spec = dict(meta.get("strategy_spec") or {})
        universe = spec.get("universe") or []
        symbols = [str(symbol) for symbol in universe if _is_a_share_symbol(str(symbol))]
        fallback = [str(symbol) for symbol in self.config.default_symbols if _is_a_share_symbol(str(symbol))]
        return symbols or fallback or list(DEFAULT_A_SHARE_SYMBOLS)

    def _append_ic_decay_warning(self, result: ResearchResult, raw: RawStrategy, vreport: Any) -> Any:
        decay_values = self._ic_decay_values(getattr(vreport, "ic_decay", []))
        if len(decay_values) < 4 or decay_values[0] == 0:
            return vreport
        remaining = abs(decay_values[3] / decay_values[0])
        if remaining >= 0.5:
            return vreport
        errors = list(getattr(vreport, "errors", []) or [])
        if "high_ic_decay" not in errors:
            errors.append("high_ic_decay")
        vreport = replace(vreport, errors=errors)
        result.log.append(ResearchLogEntry(
            phase="stage2_validation",
            title=raw.title,
            source=raw.source,
            source_url=raw.source_url,
            verdict="warn",
            reason=f"high_ic_decay: IC decay {decay_values[0]:.4f} -> {decay_values[3]:.4f} ({remaining:.0%} remaining)",
            scores={"ic_1d": decay_values[0], "ic_21d": decay_values[3], "remaining_pct": remaining, "errors": errors},
        ))
        return vreport

    @staticmethod
    def _ic_decay_values(ic_decay: Any) -> List[float]:
        values = []
        for item in ic_decay or []:
            value = item[1] if isinstance(item, (list, tuple)) and len(item) >= 2 else item
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        return values

    def _load_benchmark_data(self, symbols: List[str], start: str, end: str) -> Any:
        if self._benchmark_data_loader is None:
            return None
        try:
            return self._benchmark_data_loader(symbols, start, end)
        except Exception as e:
            logger.warning(f"Benchmark data unavailable for rigor regimes: {e}")
            return None

    def _run_walkforward(
        self,
        strategy_id: str,
        symbols: List[str],
        start: str,
        end: str,
        benchmark_data: Any,
    ) -> Any:
        kwargs = {
            "strategy_id": strategy_id,
            "symbols": symbols,
            "start": start,
            "end": end,
        }
        if benchmark_data is not None and self._walkforward_accepts_benchmark_data():
            kwargs["benchmark_data"] = benchmark_data
        return self._rigor_hub.run_walkforward(**kwargs)

    def _walkforward_accepts_benchmark_data(self) -> bool:
        try:
            parameters = inspect.signature(self._rigor_hub.run_walkforward).parameters
        except (TypeError, ValueError):
            return False
        return "benchmark_data" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

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
        self._update_hypothesis_status_for_strategy(strategy_id, "needs_more_validation", "rigor", reason)

    def _update_hypothesis_status_for_strategy(self, strategy_id: str, status: str, stage: str, reason: str) -> None:
        if self.research_store is None or not hasattr(self.research_store, "list_hypotheses"):
            return
        try:
            for row in self.research_store.list_hypotheses():
                if row.get("strategy_id") == strategy_id:
                    updated = dict(row)
                    updated["status"] = status
                    updated["stage"] = stage
                    updated["decision_reason"] = reason
                    self.research_store.upsert_hypothesis(updated)
        except Exception as e:
            logger.warning(f"Failed to update hypothesis status for {strategy_id}: {e}")

    def _attach_walkforward_result(self, strategy_id: str, wf_result: Any, verdict: str, reason: str) -> None:
        if self.research_store is None or not hasattr(self.research_store, "list_hypotheses"):
            return
        payload = self._walkforward_result_dict(wf_result, verdict, reason)
        try:
            for row in self.research_store.list_hypotheses():
                if row.get("strategy_id") != strategy_id:
                    continue
                updated = dict(row)
                metrics = dict(updated.get("metrics") or {})
                metrics["walkforward"] = payload
                updated["metrics"] = metrics
                self.research_store.upsert_hypothesis(updated)
        except Exception as e:
            logger.warning(f"Failed to attach walk-forward result for {strategy_id}: {e}")

    @staticmethod
    def _walkforward_result_dict(wf_result: Any, verdict: str, reason: str) -> Dict[str, Any]:
        splits = list(getattr(wf_result, "splits", []) or [])
        return {
            "verdict": verdict,
            "reason": reason,
            "is_viable": bool(getattr(wf_result, "is_viable", False)),
            "aggregate_oos_sharpe": _float_or_default(getattr(wf_result, "aggregate_oos_sharpe", 0.0), 0.0),
            "worst_oos_sharpe": _float_or_default(getattr(wf_result, "worst_oos_sharpe", 0.0), 0.0),
            "pct_profitable_splits": _float_or_default(getattr(wf_result, "pct_profitable_splits", 0.0), 0.0),
            "deflated_sharpe_ratio": _optional_float(getattr(wf_result, "deflated_sharpe_ratio", None)),
            "sharpe_degradation": _float_or_default(getattr(wf_result, "sharpe_degradation", 0.0), 0.0),
            "regime_breakdown": dict(getattr(wf_result, "regime_breakdown", {}) or {}),
            "bull_only_warning": bool(getattr(wf_result, "bull_only_warning", False)),
            "n_splits": len(splits),
            "splits": [ResearchEngine._walkforward_split_dict(split, idx) for idx, split in enumerate(splits, start=1)],
        }

    @staticmethod
    def _walkforward_split_dict(split: Any, idx: int) -> Dict[str, Any]:
        if not isinstance(split, dict):
            return {"split": idx}
        response = split.get("response") if isinstance(split.get("response"), dict) else {}
        response_metrics = response.get("metrics") if isinstance(response.get("metrics"), dict) else {}
        return {
            "split": split.get("split") or idx,
            "train_start": split.get("train_start_date") or split.get("train_start"),
            "train_end": split.get("train_end_date") or split.get("train_end"),
            "test_start": split.get("test_start_date") or split.get("test_start"),
            "test_end": split.get("test_end_date") or split.get("test_end"),
            "oos_sharpe": _optional_float(split.get("test_sharpe", response_metrics.get("sharpe"))),
            "max_drawdown": _optional_float(response_metrics.get("max_drawdown") or response_metrics.get("maxdd")),
            "turnover": _optional_float(response_metrics.get("turnover")),
            "regime": split.get("regime", ""),
        }


def _repo_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(__file__).resolve().parents[3]).as_posix()
    except Exception:
        return str(path).replace("\\", "/")


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_text(value: Any) -> str:
    number = _optional_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2%}"


def _is_a_share_symbol(symbol: str) -> bool:
    return len(symbol) == 6 and symbol.isdigit()


class _NullResearchStore(ResearchStore):
    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        pass

    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        return []

    def update_status(self, strategy_id: str, status: str, reason: str = "") -> bool:
        return False

    def upsert_hypothesis(self, info: Dict[str, Any]) -> None:
        pass

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_hypotheses(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def upsert_idea(self, raw: Any, status: str = "discovered", run_id: str = "", reason: str = "") -> None:
        pass

    def list_ideas(self, status: Optional[Any] = None) -> List[Dict[str, Any]]:
        return []

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
