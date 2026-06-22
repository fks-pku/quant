import inspect
import logging
import math
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import (
    DEFAULT_A_SHARE_SYMBOLS,
    DEFAULT_RESEARCH_INITIAL_CASH,
    ResearchConfig,
    ResearchResult,
    ResearchLogEntry,
    RawStrategy,
)
from quant.features.research.scout import StrategyScout
from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.pool import CandidatePool
from quant.features.research.tracking.run_recorder import RunRecorder
from quant.features.research.discovery.quality import discovery_quality, discovery_score
from quant.features.research.production_gate import evaluate_production_readiness

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
        archived_candidate_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
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
        self._archived_candidate_resolver = archived_candidate_resolver

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
            "production_gate_config": dict(self.config.production_gate_config or {}),
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
        self.research_store.write_initial_screening_table(
            self._initial_screening_rows(evaluation_rows, result)
        )

        if self.config.auto_backtest and integrated_items:
            self._run_backtests(integrated_items, result)

        if integrated_items or result.backtested > 0 or result.walkforward_passed > 0:
            result.full_report_allowed = True
        elif result.evaluated > 0:
            result.full_report_allowed = False

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
        fit_gate = self._daily_a_share_fit_gate(raw, report)
        passes_filter = bool(fit_gate.get("passed"))

        if not passes_filter:
            reason = str(fit_gate.get("reason") or "Not suitable for daily A-share research")
            result.log.append(ResearchLogEntry(
                phase="stage1_admission", title=raw.title, source=raw.source,
                source_url=raw.source_url, verdict="fail",
                reason=reason,
                scores={**self._evaluation_log_scores(report, evaluation_score), **dict(fit_gate.get("metrics") or {})},
            ))
            evaluation_rows.append((raw, report, "fail", reason))
            self._record_hypothesis(
                raw,
                status="rejected",
                stage="stage1_admission",
                reason=reason,
                report=report,
            )
            logger.info(f"'{raw.title}' filtered out ({reason})")
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
                **dict(fit_gate.get("metrics") or {}),
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
            strategy_code_path = self._write_screening_strategy_source(raw, report, strategy_spec)
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
                    "signal_source": "analytics_signal_kernels",
                    "strategy_code_path": str(strategy_code_path or ""),
                },
            ))
            vreport = self._validator.validate(strategy_spec)
            result.validated += 1
            vreport = self._append_ic_decay_warning(result, raw, vreport)
            validation_report = vreport
            gate = self._fast_validation_gate(vreport)
            gate_reason = str(gate.get("reason") or "")
            gate_metrics = dict(gate.get("metrics") or {})
            if strategy_code_path is not None:
                gate_metrics["signal_source"] = "analytics_signal_kernels"
                gate_metrics["strategy_code_path"] = str(strategy_code_path)
            if not bool(gate.get("passed")):
                result.log.append(ResearchLogEntry(
                    phase="stage2_validation", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="fail",
                    reason=gate_reason,
                    scores=gate_metrics,
                ))
                evaluation_rows.append((raw, report, "validation_failed", gate_reason))
                if self._pre_full_gate_enabled():
                    result.rejected += 1
                    result.full_report_allowed = False
                    record_reason = gate_reason
                else:
                    record_reason = f"{gate_reason}; continuing full research for audit"
                self._record_hypothesis(
                    raw,
                    status="validation_failed",
                    stage="stage2_validation",
                    reason=record_reason,
                    report=report,
                    validation_report=vreport,
                    strategy_spec=strategy_spec,
                )
                self._upsert_idea(raw, "validation_failed", record_reason)
                if self._pre_full_gate_enabled():
                    return None
            else:
                result.validated_passed += 1
                result.log.append(ResearchLogEntry(
                    phase="stage2_validation", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="pass",
                    reason=gate_reason,
                    scores=gate_metrics,
                ))
                self._record_hypothesis(
                    raw,
                    status="validated",
                    stage="stage2_validation",
                    reason=gate_reason,
                    report=report,
                    validation_report=vreport,
                    strategy_spec=strategy_spec,
                )
                self._upsert_idea(raw, "validated", gate_reason)

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

    def _write_screening_strategy_source(self, raw: RawStrategy, report: Any, strategy_spec: Any) -> Optional[Path]:
        writer = getattr(self.integrator, "write_screening_source", None)
        if writer is None:
            return None
        return writer(raw, report, strategy_spec)

    def _pre_full_gate_enabled(self) -> bool:
        cfg = dict(getattr(self.config, "validation_config", {}) or {})
        return bool(cfg.get("pre_full_gate_enabled", True))

    @staticmethod
    def _daily_a_share_fit_gate(raw: RawStrategy, report: Any) -> Dict[str, Any]:
        data_requirement = str(getattr(report, "data_requirement", "") or "unknown").strip().lower()
        daily_adaptable = bool(getattr(report, "daily_adaptable", False))
        risk_flags = {str(flag).strip().lower() for flag in getattr(report, "risk_flags", []) or []}
        required_fields = {str(field).strip().lower() for field in getattr(report, "required_data_fields", []) or []}
        discovery = dict((getattr(raw, "metadata", {}) or {}).get("discovery_quality") or {})
        matched_terms = {str(term).strip().lower() for term in discovery.get("matched_terms", []) or []}
        blocking_flags = {
            "high_frequency_not_daily",
            "hf_not_daily",
            "intraday_dependency",
            "alternative_data_required",
            "non_equity_market",
            "non_price_signal",
            "crypto_capacity_risk",
        }
        intraday_fields = {"intraday_or_order_book", "tick", "order_book", "limit_order_book"}
        failures = []
        if data_requirement == "high-frequency" and not daily_adaptable:
            failures.append("high-frequency data requirement is not daily-adaptable")
        blocked = sorted(risk_flags.intersection(blocking_flags))
        if blocked:
            failures.append(f"blocking risk flags: {', '.join(blocked)}")
        fields = sorted(required_fields.intersection(intraday_fields))
        if fields:
            failures.append(f"requires intraday fields: {', '.join(fields)}")

        metrics = {
            "daily_a_share_fit": not failures,
            "data_requirement": data_requirement,
            "daily_adaptable": daily_adaptable,
            "matched_terms": sorted(matched_terms),
        }
        if failures:
            return {
                "passed": False,
                "reason": "Not suitable for daily A-share research: " + "; ".join(failures),
                "metrics": metrics,
            }
        return {
            "passed": True,
            "reason": f"Suitable for daily A-share research: data_requirement={data_requirement}",
            "metrics": metrics,
        }

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
            metrics["factor_decomposition"] = dict(getattr(validation_report, "factor_decomposition", {}) or {})
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

    def _initial_screening_rows(
        self,
        evaluation_rows: Iterable[Tuple[Any, Any, str, str]],
        result: ResearchResult,
    ) -> List[Dict[str, Any]]:
        rows: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for raw, report, verdict, reason in evaluation_rows:
            key = self._screening_key(raw)
            item = rows.setdefault(
                key,
                {
                    "raw": raw,
                    "report": report,
                    "status": "",
                    "reason": "",
                    "strategy_id": "",
                    "formula": "",
                    "strategy_code_path": "",
                    "rank_ic": None,
                    "sharpe": None,
                },
            )
            item["status"] = str(verdict or "")
            item["reason"] = str(reason or "")

        for entry in result.log:
            key = self._screening_key(entry)
            if key not in rows:
                continue
            scores = dict(getattr(entry, "scores", {}) or {})
            phase = str(getattr(entry, "phase", "") or "")
            verdict = str(getattr(entry, "verdict", "") or "")
            if scores.get("strategy_code_path"):
                rows[key]["strategy_code_path"] = str(scores.get("strategy_code_path") or "")
            if phase == "stage1_spec":
                rows[key]["strategy_id"] = str(scores.get("strategy_id", "") or rows[key].get("strategy_id", ""))
                rows[key]["formula"] = str(scores.get("formula", "") or rows[key].get("formula", ""))
            elif phase == "stage2_validation" and verdict in {"pass", "fail"}:
                rows[key]["status"] = "validated" if verdict == "pass" else "validation_failed"
                rows[key]["reason"] = str(getattr(entry, "reason", "") or rows[key].get("reason", ""))
                rows[key]["rank_ic"] = _optional_float(scores.get("rank_ic"))
                rows[key]["sharpe"] = _optional_float(scores.get("top_bucket_after_cost_sharpe"))
            elif phase == "stage2_integrate" and verdict == "pass":
                rows[key]["status"] = "candidate"
                rows[key]["reason"] = str(getattr(entry, "reason", "") or rows[key].get("reason", ""))

        return [self._initial_screening_output_row(item) for item in rows.values()]

    def _initial_screening_output_row(self, item: Dict[str, Any]) -> Dict[str, Any]:
        raw = item.get("raw")
        report = item.get("report")
        source = str(getattr(raw, "source", "") or "")
        source_url = str(getattr(raw, "source_url", "") or "")
        strategy_id = str(item.get("strategy_id", "") or "")
        formula = str(item.get("formula", "") or "")
        strategy_code_path = str(item.get("strategy_code_path", "") or "")
        rank_ic = item.get("rank_ic")
        sharpe = item.get("sharpe")
        status = str(item.get("status", "") or "")
        code_file = self._screening_code_file(strategy_id, formula, status, strategy_code_path)
        code_url = self._screening_code_url(strategy_id, formula, status, strategy_code_path)
        reason = str(item.get("reason", "") or "")
        return {
            "idea": str(getattr(raw, "title", "") or ""),
            "source": source,
            "策略解释": _compact_text(str(getattr(raw, "description", "") or getattr(report, "summary", "") or ""), 240),
            "策略实现代码文件": code_file,
            "rank_ic": "" if rank_ic is None else f"{float(rank_ic):.4f}",
            "sharpe": "" if sharpe is None else f"{float(sharpe):.2f}",
            "结论": _compact_text(f"{status}: {reason}".strip(": "), 260),
            "source_url": source_url,
            "strategy_code_url": code_url,
            "strategy_id": strategy_id,
            "formula": formula,
        }

    def _screening_code_file(self, strategy_id: str, formula: str, status: str, strategy_code_path: str = "") -> str:
        path = self._screening_code_path(strategy_id, formula, status, strategy_code_path)
        if path is None:
            return ""
        label = self._repo_relative_path(path)
        if formula and path.name != "strategy.py":
            return f"{label}::{formula}"
        return label

    def _screening_code_url(self, strategy_id: str, formula: str, status: str, strategy_code_path: str = "") -> str:
        path = self._screening_code_path(strategy_id, formula, status, strategy_code_path)
        return "" if path is None else str(path)

    def _screening_code_path(
        self,
        strategy_id: str,
        formula: str,
        status: str,
        strategy_code_path: str = "",
    ) -> Optional[Path]:
        if strategy_code_path:
            return Path(strategy_code_path)
        if strategy_id and status == "candidate":
            strategies_dir = Path(str(getattr(self.integrator, "strategies_dir", Path("quant/features/strategies"))))
            if not strategies_dir.is_absolute():
                strategies_dir = self._repo_root() / strategies_dir
            return strategies_dir / strategy_id / "strategy.py"
        if formula:
            return self._repo_root() / "quant" / "features" / "research" / "validation" / "signal_library.py"
        return None

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _repo_relative_path(cls, path: Path) -> str:
        try:
            return path.resolve().relative_to(cls._repo_root()).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _screening_key(item: Any) -> Tuple[str, str, str]:
        return (
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "source", "") or ""),
            str(getattr(item, "source_url", "") or ""),
        )

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
                elif strict_verdict == "warn" and final_status != "rejected":
                    final_status = "needs_more_validation"
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
                if final_status == "" and strict_verdict == "pass" and self._candidate_status(sid) == "rejected":
                    final_status = "candidate"
                    final_reasons.append(
                        "strict checklist passed under current standards; walk-forward audit is informational"
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
                elif final_status == "needs_more_validation" and self._candidate_status(sid) != "rejected":
                    dsr_value = 0.0
                    try:
                        dsr_value = float(getattr(wf_result, "deflated_sharpe_ratio", 0.0) or 0.0)
                    except (TypeError, ValueError):
                        dsr_value = 0.0
                    self._mark_needs_more_validation(sid, dsr_value, final_reason)
                elif final_status == "candidate":
                    if self.research_store is not None and hasattr(self.research_store, "update_status"):
                        self.research_store.update_status(sid, "candidate", reason=final_reason)
                    self._update_hypothesis_status_for_strategy(sid, "candidate", "go_no_go", final_reason)
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

    def _fast_validation_gate(self, validation_report: Any) -> Dict[str, Any]:
        cfg = dict(getattr(self.config, "validation_config", {}) or {})
        min_rank_ic = float(cfg.get("fast_gate_min_rank_ic", cfg.get("min_rank_ic", 0.02)))
        min_top_sharpe = float(cfg.get("fast_gate_min_top_bucket_after_cost_sharpe", 0.5))
        min_top_return = float(cfg.get("fast_gate_min_top_bucket_after_cost_annualized_return", 0.05))
        max_top_drawdown = abs(float(cfg.get("fast_gate_max_top_bucket_after_cost_drawdown", 0.50)))
        fail_on_high_ic_decay = bool(cfg.get("fast_gate_fail_on_high_ic_decay", False))

        if validation_report is None:
            return {
                "passed": False,
                "status": "failed",
                "reason": "Validation failed: no validation report",
                "metrics": {},
                "failures": ["no_validation_report"],
            }

        rank_ic = _required_float(getattr(validation_report, "rank_ic", None))
        fdr = _float_or_default(getattr(validation_report, "fdr_adjusted_p", 1.0), 1.0)
        hit_rate = _float_or_default(getattr(validation_report, "hit_rate", 0.0), 0.0)
        metrics: Dict[str, Any] = {
            "rank_ic": rank_ic if rank_ic is not None else "not_calculable",
            "rank_ic_ir": _float_or_default(getattr(validation_report, "rank_ic_ir", 0.0), 0.0),
            "fdr_adjusted_p": fdr,
            "fdr_significant": bool(getattr(validation_report, "fdr_significant", False)),
            "hit_rate": hit_rate,
        }
        failures: List[str] = []

        if getattr(validation_report, "status", "") == "error":
            errors = "; ".join(str(item) for item in getattr(validation_report, "errors", []) or [])
            failures.append(f"validation_error={errors or 'unknown'}")
        if rank_ic is None:
            failures.append("rank_ic=not_calculable")
        elif rank_ic <= min_rank_ic:
            failures.append(f"rank_ic={rank_ic:.4f} <= {min_rank_ic:.4f}")
        if fail_on_high_ic_decay and "high_ic_decay" in set(getattr(validation_report, "errors", []) or []):
            failures.append("high_ic_decay")

        diagnostics = getattr(validation_report, "portfolio_diagnostics", {}) or {}
        if isinstance(diagnostics, dict) and diagnostics:
            top_sharpe = _required_float(diagnostics.get("top_bucket_after_cost_sharpe"))
            top_return = _required_float(diagnostics.get("top_bucket_after_cost_annualized_return"))
            top_drawdown = _required_float(diagnostics.get("top_bucket_after_cost_max_drawdown"))
            for key, value in {
                "top_bucket_after_cost_sharpe": top_sharpe,
                "top_bucket_after_cost_annualized_return": top_return,
                "top_bucket_after_cost_max_drawdown": top_drawdown,
            }.items():
                if value is not None:
                    metrics[key] = value
            if top_sharpe is None:
                failures.append("top_bucket_after_cost_sharpe=not_calculable")
            elif top_sharpe < min_top_sharpe:
                failures.append(f"top_bucket_after_cost_sharpe={top_sharpe:.2f} < {min_top_sharpe:.2f}")
            if top_return is None:
                failures.append("top_bucket_after_cost_annualized_return=not_calculable")
            elif top_return <= min_top_return:
                failures.append(f"top_bucket_after_cost_annualized_return={top_return:.2%} <= {min_top_return:.2%}")
            if top_drawdown is None:
                failures.append("top_bucket_after_cost_max_drawdown=not_calculable")
            elif abs(top_drawdown) >= max_top_drawdown:
                failures.append(f"top_bucket_after_cost_max_drawdown={top_drawdown:.2%} beyond {max_top_drawdown:.2%}")
        else:
            failures.append("portfolio_diagnostics=not_available")

        passed = not failures
        if passed:
            if "top_bucket_after_cost_sharpe" in metrics:
                reason = (
                    f"Validation passed: IC={rank_ic:.4f}, "
                    f"Top20 after-cost Sharpe={metrics['top_bucket_after_cost_sharpe']:.2f}, "
                    f"CAGR={metrics['top_bucket_after_cost_annualized_return']:.2%}, "
                    f"MaxDD={metrics['top_bucket_after_cost_max_drawdown']:.2%}"
                )
            else:
                reason = f"Validation passed: IC={rank_ic:.4f}"
        else:
            reason = "Validation failed: " + "; ".join(failures)
        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "reason": reason,
            "metrics": metrics,
            "failures": failures,
        }

    def _attach_fast_research_conclusion(self, strategy_id: str, report: Any, validation_report: Any = None) -> None:
        evaluation_score = self._evaluation_score(report)
        metrics = {
            "admission_score": evaluation_score,
            "signal_quality_score": getattr(report, "signal_quality_score", 0.0),
            "research_confidence_score": getattr(report, "research_confidence_score", 0.0),
        }
        if validation_report is not None:
            gate = self._fast_validation_gate(validation_report)
            metrics.update(dict(gate.get("metrics") or {}))
            metrics["validation_failures"] = list(gate.get("failures") or [])
            rank_ic = _float_or_default(metrics.get("rank_ic"), 0.0)
            if bool(gate.get("passed")):
                verdict = "pass"
                conclusion = (
                    f"快研究通过：Rank IC={rank_ic:.4f}、"
                    f"Top20 扣费 Sharpe={_float_or_default(metrics.get('top_bucket_after_cost_sharpe'), 0.0):.2f}、"
                    f"CAGR={_float_or_default(metrics.get('top_bucket_after_cost_annualized_return'), 0.0):.2%}、"
                    f"MaxDD={_float_or_default(metrics.get('top_bucket_after_cost_max_drawdown'), 0.0):.2%}；"
                    "可进入 strict 回测检查真实交易约束。"
                )
            else:
                verdict = "fail"
                conclusion = (
                    f"快研究未通过：{gate.get('reason', '')}；"
                    "后续 strict 回测和 walk-forward 仅作为审计，不作为上线依据。"
                )
            self._attach_research_stage_conclusion(
                strategy_id,
                "fast_research",
                "快研究",
                verdict,
                conclusion,
                metrics,
                "来源/admission、StrategySpec、HFQ 信号验证和扣费组合诊断。",
            )
            return
        verdict = "warn"
        conclusion = (
            f"快研究完成 admission={evaluation_score:.2f}，但本轮未运行 HFQ 信号验证；"
            "只能进入 strict 回测做执行层审计，不能作为独立通过结论。"
        )
        self._attach_research_stage_conclusion(
            strategy_id,
            "fast_research",
            "快研究",
            verdict,
            conclusion,
            metrics,
            "来源/admission 和 StrategySpec；本轮未运行 HFQ 信号验证。",
        )

    def _attach_strict_backtest_conclusion(self, strategy_id: str) -> Tuple[str, str]:
        strict = self._strict_backtest_for_strategy(strategy_id)
        metrics = strict.get("metrics") or {}
        diagnostics = strict.get("diagnostics") or {}
        capacity = strict.get("capacity") or {}
        survivorship = ((strict.get("data_quality") or {}).get("survivorship_audit") or {})
        sharpe = _optional_float(metrics.get("sharpe"))
        cagr = _optional_float(metrics.get("cagr"))
        max_dd = _optional_float(metrics.get("max_drawdown_pct"))
        trades = metrics.get("total_trades")
        gate = evaluate_production_readiness(
            {"strict_backtest": strict},
            self.config.production_gate_config,
        ) if strict else None
        payload = {
            "sharpe": sharpe,
            "cagr": cagr,
            "max_drawdown_pct": max_dd,
            "total_trades": trades,
            "max_adv_participation": capacity.get("max_adv_participation"),
            "total_commission": diagnostics.get("total_commission"),
            "insufficient_cash_rejected_orders": (diagnostics.get("rejection_counts") or {}).get("insufficient_cash", 0),
            "survivorship_audit_kind": survivorship.get("kind"),
            "survivorship_material": bool(survivorship.get("material")),
            "daily_basic_not_ohlc_symbols": survivorship.get("daily_basic_not_ohlc_symbols"),
            "missing_symbols_below_top20_excluding_920": survivorship.get("missing_symbols_below_top20_excluding_920"),
            "bar_symbols_missing_fund_meta": survivorship.get("bar_symbols_missing_fund_meta"),
            "fund_meta_delisted_symbols": survivorship.get("fund_meta_delisted_symbols"),
            "production_gate": gate or {},
        }
        if not strict:
            verdict = "warn"
            conclusion = "strict Backtester 未返回结构化结果；本阶段不能形成通过结论。"
        elif gate and gate.get("verdict") == "fail":
            verdict = "fail"
            conclusion = (
                "strict 回测未通过新 checklist："
                + "; ".join(gate.get("failures") or ["unknown failure"])
                + "。"
            )
        elif bool(survivorship.get("material")):
            verdict = "warn"
            conclusion = self._strict_survivorship_warning_conclusion(cagr, max_dd, survivorship)
        else:
            verdict = "pass"
            conclusion = (
                f"strict 回测通过：CAGR={_percent_text(cagr)}，MaxDD={_percent_text(max_dd)}，"
                f"交易数={trades or 'n/a'}，单笔最大 ADV={_percent_text(capacity.get('max_adv_participation'))}；"
                "walk-forward 暂不作为通过标准。"
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

    def _strict_survivorship_warning_conclusion(
        self,
        cagr: Optional[float],
        max_dd: Optional[float],
        survivorship: Dict[str, Any],
    ) -> str:
        prefix = f"strict 回测数据审计警告：CAGR={_percent_text(cagr)}，MaxDD={_percent_text(max_dd)}，"
        if survivorship.get("kind") == "etf_metadata_survivorship_audit":
            missing_meta = survivorship.get("bar_symbols_missing_fund_meta") or 0
            delisted_meta = survivorship.get("fund_meta_delisted_symbols") or 0
            return (
                prefix +
                f"ETF 日线中有 {missing_meta} 个 symbol 缺少基金元数据，"
                f"基金元数据中 delist_date 标记数={delisted_meta}；"
                "已使用人工审计注册 ETF 代表池和调仓日可见数据约束收窄选择偏差，但仍需关注注册 ETF 自身数据覆盖。"
            )
        return (
            prefix +
            f"但 {survivorship.get('missing_symbols_below_top20_excluding_920') or 0} 个非 920 缺失股票"
            "可能进入小市值 Top20 区间；本阶段不能形成无偏通过结论。"
        )

    def _attach_walkforward_audit_conclusion(self, strategy_id: str, wf_result: Any, verdict: str, reason: str) -> None:
        aggregate = _float_or_default(getattr(wf_result, "aggregate_oos_sharpe", 0.0), 0.0)
        worst = _float_or_default(getattr(wf_result, "worst_oos_sharpe", 0.0), 0.0)
        pct_profitable = _float_or_default(getattr(wf_result, "pct_profitable_splits", 0.0), 0.0)
        dsr = _optional_float(getattr(wf_result, "deflated_sharpe_ratio", None))
        evaluated_splits = int(getattr(wf_result, "evaluated_splits", 0) or 0)
        no_trade_splits = int(getattr(wf_result, "no_trade_splits", 0) or 0)
        split_scope = f"有效 split={evaluated_splits}"
        if no_trade_splits:
            split_scope += f"，无交易 split={no_trade_splits} 已排除"
        if verdict == "pass":
            conclusion = (
                f"walk-forward strict audit 通过：aggregate OOS Sharpe={aggregate:.2f}，"
                f"worst={worst:.2f}，盈利 split={pct_profitable:.0%}，{split_scope}。"
            )
        elif verdict == "warn":
            dsr_text = f"{dsr:.2f}" if dsr is not None else "n/a"
            conclusion = (
                f"walk-forward strict audit 仅警告：aggregate OOS Sharpe={aggregate:.2f}，"
                f"worst={worst:.2f}，DSR={dsr_text}，{split_scope}；需要更多验证。"
            )
        else:
            conclusion = (
                f"walk-forward strict audit 未通过：aggregate OOS Sharpe={aggregate:.2f}，"
                f"worst={worst:.2f}，盈利 split={pct_profitable:.0%}，{split_scope}；{reason}"
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
                "evaluated_splits": evaluated_splits,
                "no_trade_splits": no_trade_splits,
                "total_splits": int(getattr(wf_result, "total_splits", 0) or len(list(getattr(wf_result, "splits", []) or []))),
            },
            "滚动 OOS split 重放 strict Backtester，用于最终稳定性审计。",
        )

    def _attach_final_research_conclusion(self, strategy_id: str, status: str, reason: str = "") -> None:
        production_gate = self._production_gate_for_strategy(strategy_id) if status != "rejected" else None
        verdict = "pass" if status in {"candidate", "paper_trading_candidate"} else "warn" if status == "needs_more_validation" else "fail"
        if status == "rejected":
            conclusion = f"最终 No-Go：{reason or '至少一个正式阶段未通过'}。"
        elif production_gate and production_gate.get("verdict") == "fail":
            verdict = "fail"
            conclusion = f"Final No-Go: {production_gate.get('reason', 'Production gate failed')}."
        elif production_gate and production_gate.get("verdict") == "warn":
            verdict = "warn"
            conclusion = f"Final needs more validation: {production_gate.get('reason', 'Production gate warning')}."
        elif status == "needs_more_validation":
            reason_text = str(reason or "strict checklist 或数据审计尚未完全达标").rstrip("。.")
            conclusion = f"最终结论：需要更多验证；{reason_text}。"
        else:
            conclusion = f"最终状态为 {status}；可进入下一层人工复核、容量和 paper trading 审批。"
        scores = {"status": status, "reason": reason}
        if production_gate:
            scores["production_gate"] = production_gate
        self._attach_research_stage_conclusion(
            strategy_id,
            "final_decision",
            "最终 Go / No-Go",
            verdict,
            conclusion,
            scores,
            "汇总 strict checklist；walk-forward 当前仅作为审计展示。",
        )

    def _production_gate_for_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        if self.research_store is None or not hasattr(self.research_store, "list_hypotheses"):
            return None
        try:
            for row in self.research_store.list_hypotheses():
                if row.get("strategy_id") != strategy_id:
                    continue
                metrics = dict(row.get("metrics") or {})
                if not metrics.get("strict_backtest"):
                    return None
                return evaluate_production_readiness(metrics, self.config.production_gate_config)
        except Exception as e:
            logger.warning(f"Failed to evaluate production gate for {strategy_id}: {e}")
        return None

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
        gate_result = self._fast_validation_gate(validation_report)
        gate_metrics = dict(gate_result.get("metrics") or {})
        gate = {
            "status": str(gate_result.get("status") or self._validation_gate_status(validation_report)),
            "rank_ic": _float_or_default(gate_metrics.get("rank_ic"), 0.0),
            "rank_ic_ir": _float_or_default(gate_metrics.get("rank_ic_ir"), 0.0),
            "fdr_adjusted_p": _float_or_default(gate_metrics.get("fdr_adjusted_p"), 1.0),
            "hit_rate": _float_or_default(gate_metrics.get("hit_rate"), 0.0),
            "reason": str(gate_result.get("reason") or self._validation_gate_reason(validation_report)),
            "metrics": gate_metrics,
            "failures": list(gate_result.get("failures") or []),
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

    def _validation_gate_status(self, validation_report: Any) -> str:
        return str(self._fast_validation_gate(validation_report).get("status") or "failed")

    def _validation_gate_reason(self, validation_report: Any) -> str:
        return str(self._fast_validation_gate(validation_report).get("reason") or "Validation gate failed")

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
        if "Missing point-in-time market cap field" in str(reason):
            return
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
        meta = self._strategy_research_meta(strategy_id)
        spec = dict(meta.get("strategy_spec") or {})
        universe = spec.get("universe") or []
        symbols = [str(symbol) for symbol in universe if _is_a_share_symbol(str(symbol))]
        fallback = [str(symbol) for symbol in self.config.default_symbols if _is_a_share_symbol(str(symbol))]
        return symbols or fallback or list(DEFAULT_A_SHARE_SYMBOLS)

    def _strategy_archive_dir(self, strategy_id: str) -> str:
        meta = self._strategy_research_meta(strategy_id)
        return str(meta.get("rejected_strategy_dir") or "")

    def _strategy_research_meta(self, strategy_id: str) -> Dict[str, Any]:
        entry = self.integrator.get_registry_entry(strategy_id) if hasattr(self.integrator, "get_registry_entry") else None
        meta = dict((entry or {}).get("research_meta") or {})
        if meta:
            return self._merge_archived_research_meta(strategy_id, meta)
        if self.research_store is not None and hasattr(self.research_store, "get_candidate"):
            try:
                candidate = self.research_store.get_candidate(strategy_id)
                meta = dict((candidate or {}).get("research_meta") or {})
                if meta:
                    return self._merge_archived_research_meta(strategy_id, meta)
            except Exception:
                pass
        if self.research_store is not None and hasattr(self.research_store, "list_hypotheses"):
            try:
                for row in self.research_store.list_hypotheses():
                    if row.get("strategy_id") != strategy_id:
                        continue
                    evidence = dict(row.get("evidence") or {})
                    spec = dict(evidence.get("strategy_spec") or {})
                    if spec:
                        return self._merge_archived_research_meta(strategy_id, {"strategy_spec": spec})
            except Exception:
                pass
        return self._archived_research_meta(strategy_id)

    def _archived_research_meta(self, strategy_id: str) -> Dict[str, Any]:
        archived = self._resolve_archived_candidate_info(strategy_id)
        return dict((archived or {}).get("research_meta") or {})

    def _resolve_archived_candidate_info(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        if self._archived_candidate_resolver is None:
            return None
        try:
            return self._archived_candidate_resolver(strategy_id)
        except Exception as e:
            logger.warning(f"Archived candidate resolver failed for {strategy_id}: {e}")
            return None

    def _merge_archived_research_meta(self, strategy_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        archived = self._resolve_archived_candidate_info(strategy_id)
        if not archived:
            return dict(meta or {})
        archived_meta = dict(archived.get("research_meta") or {})
        archived_spec = dict(archived_meta.get("strategy_spec") or {})
        existing_meta = dict(meta or {})
        existing_spec = dict(existing_meta.get("strategy_spec") or {})
        merged = {**archived_meta, **existing_meta}
        if self._archived_candidate_uses_pit_universe(archived):
            merged["strategy_spec"] = self._drop_fixed_symbol_parameters({**existing_spec, **archived_spec})
        else:
            merged["strategy_spec"] = {**archived_spec, **existing_spec}
        return merged

    @staticmethod
    def _archived_candidate_uses_pit_universe(info: Dict[str, Any]) -> bool:
        params = dict((info or {}).get("parameters") or {})
        meta = dict((info or {}).get("research_meta") or {})
        spec = dict(meta.get("strategy_spec") or {})
        return bool(params.get("pit_universe_enabled")) or bool(params.get("risk_category_symbols")) or bool(spec.get("risk_category_symbols"))

    @staticmethod
    def _drop_fixed_symbol_parameters(values: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = dict(values or {})
        for key in ("symbols", "risk_symbols", "defensive_symbols"):
            cleaned.pop(key, None)
        return cleaned

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
        if any(_is_a_share_symbol(symbol) for symbol in symbols) and self._walkforward_accepts_parameter("initial_cash"):
            kwargs["initial_cash"] = self._default_initial_cash()
        archive_dir = self._strategy_archive_dir(strategy_id)
        if archive_dir and self._walkforward_accepts_parameter("strategy_archive_dir"):
            kwargs["strategy_archive_dir"] = archive_dir
        if benchmark_data is not None and self._walkforward_accepts_benchmark_data():
            kwargs["benchmark_data"] = benchmark_data
        return self._rigor_hub.run_walkforward(**kwargs)

    def _walkforward_accepts_benchmark_data(self) -> bool:
        return self._walkforward_accepts_parameter("benchmark_data")

    def _default_initial_cash(self) -> float:
        try:
            value = float(getattr(self.config, "default_initial_cash", DEFAULT_RESEARCH_INITIAL_CASH))
        except (TypeError, ValueError):
            return DEFAULT_RESEARCH_INITIAL_CASH
        return value if value > 0 else DEFAULT_RESEARCH_INITIAL_CASH

    def _walkforward_accepts_parameter(self, name: str) -> bool:
        try:
            parameters = inspect.signature(self._rigor_hub.run_walkforward).parameters
        except (TypeError, ValueError):
            return False
        return name in parameters or any(
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
        payload = self._walkforward_result_dict(wf_result, verdict, reason, self._walkforward_thresholds())
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

    def _walkforward_thresholds(self) -> Dict[str, Any]:
        rigor = dict(self.config.rigor_config or {})
        wf = dict(rigor.get("purged_walkforward") or {})
        thresholds = dict(rigor.get("thresholds") or {})
        cost = dict(rigor.get("cost_model") or {})
        return {
            "train_window_days": wf.get("train_window_days", 252),
            "test_window_days": wf.get("test_window_days", 63),
            "step_days": wf.get("step_days", 63),
            "purge_days": wf.get("purge_days", 5),
            "embargo_days": wf.get("embargo_days", 21),
            "min_train_observations": wf.get("min_train_observations", 126),
            "min_worst_oos_sharpe": thresholds.get("min_worst_oos_sharpe", 0.3),
            "min_profitable_splits_pct": thresholds.get("min_profitable_splits_pct", 0.5),
            "min_deflated_sharpe_ratio": thresholds.get("min_deflated_sharpe_ratio", 0.95),
            "max_adv_pct": cost.get("max_adv_pct", 0.05),
        }

    @staticmethod
    def _walkforward_result_dict(
        wf_result: Any,
        verdict: str,
        reason: str,
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        splits = list(getattr(wf_result, "splits", []) or [])
        evaluated_splits = int(getattr(wf_result, "evaluated_splits", 0) or 0)
        no_trade_splits = int(getattr(wf_result, "no_trade_splits", 0) or 0)
        total_splits = int(getattr(wf_result, "total_splits", 0) or len(splits))
        capacity_ok = getattr(wf_result, "capacity_ok", None)
        return {
            "verdict": verdict,
            "reason": reason,
            "is_viable": bool(getattr(wf_result, "is_viable", False)),
            "capacity_ok": bool(capacity_ok) if capacity_ok is not None else None,
            "thresholds": dict(thresholds or {}),
            "aggregate_oos_sharpe": _float_or_default(getattr(wf_result, "aggregate_oos_sharpe", 0.0), 0.0),
            "worst_oos_sharpe": _float_or_default(getattr(wf_result, "worst_oos_sharpe", 0.0), 0.0),
            "pct_profitable_splits": _float_or_default(getattr(wf_result, "pct_profitable_splits", 0.0), 0.0),
            "deflated_sharpe_ratio": _optional_float(getattr(wf_result, "deflated_sharpe_ratio", None)),
            "sharpe_degradation": _float_or_default(getattr(wf_result, "sharpe_degradation", 0.0), 0.0),
            "regime_breakdown": dict(getattr(wf_result, "regime_breakdown", {}) or {}),
            "bull_only_warning": bool(getattr(wf_result, "bull_only_warning", False)),
            "n_splits": evaluated_splits if evaluated_splits or no_trade_splits else len(splits),
            "evaluated_splits": evaluated_splits if evaluated_splits or no_trade_splits else len(splits),
            "no_trade_splits": no_trade_splits,
            "total_splits": total_splits,
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
            "trade_count": split.get("trade_count"),
            "has_trades": split.get("has_trades"),
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
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _required_float(value: Any) -> Optional[float]:
    return _optional_float(value)


def _percent_text(value: Any) -> str:
    number = _optional_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2%}"


def _compact_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _is_a_share_symbol(symbol: str) -> bool:
    return len(symbol) == 6 and symbol.isdigit()


class _NullResearchStore(ResearchStore):
    def upsert_candidate(self, info: Dict[str, Any]) -> None:
        pass

    def get_candidate(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        return []

    def list_candidates(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
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

    def write_initial_screening_table(self, rows: Iterable[Dict[str, Any]]) -> None:
        pass

    def save_run_result(self, result: Any) -> None:
        pass
