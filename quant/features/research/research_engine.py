import inspect
import logging
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from quant.domain.ports.research_store import ResearchStore
from quant.features.research.models import ResearchConfig, ResearchResult, ResearchLogEntry, RawStrategy
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
        logger.info(f"Discovered {result.discovered} strategies")

        result.log.append(ResearchLogEntry(
            phase="scout", title=f"Scanned {result.discovered} strategies",
            source="", source_url="", verdict="info",
            reason=f"Sources: {sources or self.config.sources}",
            scores=self._discovery_summary(raw_strategies),
        ))

        integrated_ids = []
        evaluation_rows = []
        for raw in raw_strategies:
            try:
                strategy_hash = StrategyScout.hash_strategy(raw)
                validation_report = None
                strategy_spec = None
                if self.research_store.has_seen(strategy_hash):
                    result.log.append(ResearchLogEntry(
                        phase="scout", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="skip",
                        reason="Previously discovered",
                    ))
                    self._record_hypothesis(raw, status="skipped", stage="scout", reason="Previously discovered")
                    continue
                self.research_store.mark_seen(strategy_hash, raw)

                report = self.evaluator.evaluate(raw)
                result.evaluated += 1

                evaluation_score = self._evaluation_score(report)
                passes_filter = evaluation_score >= self.config.evaluation_threshold
                if report.data_requirement == "high-frequency":
                    passes_filter = passes_filter and report.daily_adaptable

                if not passes_filter:
                    reason_parts = [
                        f"admission={evaluation_score:.1f} < {self.config.evaluation_threshold}",
                        f"suitability={report.suitability_score:.1f}",
                    ]
                    if report.data_requirement == "high-frequency" and not report.daily_adaptable:
                        reason_parts.append("high-frequency, not daily-adaptable")
                    if getattr(report, "rejection_reason", ""):
                        reason_parts.append(report.rejection_reason)
                    result.log.append(ResearchLogEntry(
                        phase="evaluate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="fail",
                        reason="; ".join(reason_parts),
                        scores={
                            "suitability": report.suitability_score,
                            "admission": evaluation_score,
                            "signal_quality": getattr(report, "signal_quality_score", 0.0),
                            "complexity": report.complexity_score,
                            "edge": report.estimated_edge,
                        },
                    ))
                    evaluation_rows.append((raw, report, "fail", "; ".join(reason_parts)))
                    self._record_hypothesis(
                        raw,
                        status="rejected",
                        stage="evaluate",
                        reason="; ".join(reason_parts),
                        report=report,
                    )
                    logger.info(f"'{raw.title}' filtered out (suitability={report.suitability_score})")
                    result.rejected += 1
                    continue

                if self.config.validation_enabled and self._spec_builder is not None:
                    spec = self._spec_builder.build(raw, report)
                    strategy_spec = spec
                    result.specified += 1
                    if spec.status != "ready":
                        result.needs_manual_spec += 1
                        result.log.append(ResearchLogEntry(
                            phase="validation", title=raw.title, source=raw.source,
                            source_url=raw.source_url, verdict="skip",
                            reason=f"Spec status: {spec.status}",
                        ))
                        self._record_hypothesis(
                            raw,
                            status="needs_manual_spec",
                            stage="validation",
                            reason=f"Spec status: {spec.status}",
                            report=report,
                        )
                    elif self._validator is not None:
                        vreport = self._validator.validate(spec)
                        result.validated += 1
                        if vreport.status == "error" or not vreport.fdr_significant or vreport.rank_ic < 0.02:
                            result.log.append(ResearchLogEntry(
                                phase="validation", title=raw.title, source=raw.source,
                                source_url=raw.source_url, verdict="fail",
                                reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                                scores={"rank_ic": vreport.rank_ic, "hit_rate": vreport.hit_rate},
                            ))
                            result.rejected += 1
                            evaluation_rows.append((raw, report, "fail", f"Validation failed: IC={vreport.rank_ic:.4f}"))
                            self._record_hypothesis(
                                raw,
                                status="rejected",
                                stage="validation",
                                reason=f"Validation failed: IC={vreport.rank_ic:.4f}",
                                report=report,
                                validation_report=vreport,
                            )
                            continue
                        else:
                            result.validated_passed += 1
                            result.log.append(ResearchLogEntry(
                                phase="validation", title=raw.title, source=raw.source,
                                source_url=raw.source_url, verdict="pass",
                                reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                                scores={"rank_ic": vreport.rank_ic, "hit_rate": vreport.hit_rate},
                            ))
                            vreport = self._append_ic_decay_warning(result, raw, vreport)
                            validation_report = vreport
                            self._record_hypothesis(
                                raw,
                                status="validated",
                                stage="validation",
                                reason=f"IC={vreport.rank_ic:.4f}, FDR={vreport.fdr_adjusted_p:.4f}",
                                report=report,
                                validation_report=vreport,
                            )

                strategy_id = self.integrator.integrate(raw, report, spec=strategy_spec)
                if strategy_id:
                    result.integrated += 1
                    integrated_ids.append(strategy_id)
                    result.log.append(ResearchLogEntry(
                        phase="integrate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="pass",
                        reason=f"Integrated as {strategy_id}",
                        scores={
                            "suitability": report.suitability_score,
                            "admission": self._evaluation_score(report),
                            "signal_quality": getattr(report, "signal_quality_score", 0.0),
                            "complexity": report.complexity_score,
                            "edge": report.estimated_edge,
                            "type": report.strategy_type,
                        },
                    ))
                    evaluation_rows.append((raw, report, "pass", f"Integrated as {strategy_id}"))
                    self._record_hypothesis(
                        raw,
                        status="candidate",
                        stage="integrate",
                        reason=f"Integrated as {strategy_id}",
                        strategy_id=strategy_id,
                        report=report,
                    )
                    self._write_promotion_dossier(strategy_id, raw, report, validation_report, result.run_id, strategy_spec)
                else:
                    result.errors.append(f"Integration failed for '{raw.title}'")
                    result.log.append(ResearchLogEntry(
                        phase="integrate", title=raw.title, source=raw.source,
                        source_url=raw.source_url, verdict="error",
                        reason="Integration failed",
                    ))
                    evaluation_rows.append((raw, report, "error", "Integration failed"))
                    self._record_hypothesis(
                        raw,
                        status="error",
                        stage="integrate",
                        reason="Integration failed",
                        report=report,
                    )
            except Exception as e:
                logger.error(f"Pipeline error for '{raw.title}': {e}")
                result.errors.append(str(e))
                result.log.append(ResearchLogEntry(
                    phase="evaluate", title=raw.title, source=raw.source,
                    source_url=raw.source_url, verdict="error",
                    reason=str(e),
                ))
                self._record_hypothesis(raw, status="error", stage="evaluate", reason=str(e))

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
        self._write_candidate_scorecard(result.run_id)
        logger.info(f"Pipeline complete: {result}")
        return result

    def _record_hypothesis(
        self,
        raw: RawStrategy,
        status: str,
        stage: str,
        reason: str,
        strategy_id: str = "",
        report: Any = None,
        validation_report: Any = None,
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
                "fdr_adjusted_p",
                "fdr_significant",
                "hit_rate",
                "long_short_spread",
                "n_observations",
                "ff_alpha_monthly",
                "ff_alpha_tstat",
                "ff_r2",
            ):
                metrics[field] = getattr(validation_report, field, 0.0)
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

    def _run_backtests(self, strategy_ids: List[str], result: ResearchResult, benchmark_data: Any = None) -> None:
        if self._backtest_fn is None:
            logger.warning("No backtest function injected — skipping backtests")
            return
        for sid in strategy_ids:
            try:
                symbols = self._strategy_symbols(sid)
                if self._rigor_hub is not None and self.config.rigor_enabled:
                    split_benchmark_data = benchmark_data
                    if split_benchmark_data is None:
                        split_benchmark_data = self._load_benchmark_data(
                            symbols,
                            self.config.default_backtest_start,
                            self.config.default_backtest_end,
                        )
                    wf_result = self._run_walkforward(
                        sid,
                        symbols,
                        self.config.default_backtest_start,
                        self.config.default_backtest_end,
                        split_benchmark_data,
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

    def _strategy_symbols(self, strategy_id: str) -> List[str]:
        entry = self.integrator.get_registry_entry(strategy_id) if hasattr(self.integrator, "get_registry_entry") else None
        meta = dict((entry or {}).get("research_meta") or {})
        spec = dict(meta.get("strategy_spec") or {})
        universe = spec.get("universe") or []
        symbols = [str(symbol) for symbol in universe if str(symbol)]
        return symbols or list(self.config.default_symbols)

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
            phase="validation",
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
