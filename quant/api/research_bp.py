import uuid
import threading
from datetime import datetime
from pathlib import Path
from flask import Blueprint, jsonify, request

from quant.features.research.models import ResearchConfig, ResearchResult
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.pool import CandidatePool
from quant.features.research.scheduler import ResearchScheduler
from quant.features.research.ensemble import ResearchEnsembleBuilder
from quant.features.research.tracking.comparison import StrategyComparator


def _make_backtest_fn():
    """Create a backtest function for the research pipeline.

    Defined at the API layer (not in features/) so cross-feature imports are
    architectural composition-root wiring, not feature-to-feature coupling.
    """
    from quant.features.backtest.engine import Backtester
    from quant.features.strategies.registry import StrategyRegistry
    from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    from quant.features.research.models import ResearchLogEntry
    import pandas as pd

    def _run_backtest(sid, result, config, integrator, pool):
        registry = StrategyRegistry()
        strategy_class = registry.get(sid)
        if strategy_class is None:
            result.errors.append(f"Strategy {sid} not in registry for backtest")
            return

        symbols = config.default_symbols
        start = datetime.strptime(config.default_backtest_start, "%Y-%m-%d")
        end = datetime.strptime(config.default_backtest_end, "%Y-%m-%d")

        db_provider = DuckDBProvider()
        db_provider.connect()
        all_data = []
        for sym in symbols:
            bars = db_provider.get_bars(sym, start, end, "1d")
            if not bars.empty:
                all_data.append(bars)
        db_provider.disconnect()

        if not all_data:
            result.errors.append(f"No data for {sid}")
            return

        data_df = pd.concat(all_data, ignore_index=True)
        data_provider = DataFrameProvider(data_df)
        strategy = strategy_class(symbols=symbols)

        bt_config = {
            "backtest": {"slippage_bps": 5},
            "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
            "data": {"default_timeframe": "1d"},
            "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
        }

        backtester = Backtester(bt_config, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio)
        bt_result = backtester.run(start=start, end=end, strategies=[strategy], initial_cash=100000, data_provider=data_provider, symbols=symbols)

        info = integrator.get_registry_entry(sid)
        if info is not None:
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
                pool.reject(sid, reason=f"Backtest Sharpe {bt_result.sharpe_ratio:.2f} below threshold")
                result.rejected += 1
                result.log.append(ResearchLogEntry(
                    phase="backtest", title=info.get("name", sid),
                    source="", source_url="", verdict="fail",
                    reason=f"Sharpe {bt_result.sharpe_ratio:.2f} < {config.backtest_sharpe_threshold}",
                    scores={
                        "sharpe": round(bt_result.sharpe_ratio, 2),
                        "max_dd": round(bt_result.max_drawdown_pct, 2),
                        "win_rate": round(bt_result.win_rate * 100, 2),
                    },
                ))
            else:
                result.backtested += 1
                result.log.append(ResearchLogEntry(
                    phase="backtest", title=info.get("name", sid),
                    source="", source_url="", verdict="pass",
                    reason=f"Sharpe {bt_result.sharpe_ratio:.2f}",
                    scores={
                        "sharpe": round(bt_result.sharpe_ratio, 2),
                        "max_dd": round(bt_result.max_drawdown_pct, 2),
                        "win_rate": round(bt_result.win_rate * 100, 2),
                    },
                ))

    return _run_backtest


def _make_rigor_backtest_runner():
    from quant.features.backtest.engine import Backtester
    from quant.features.strategies.registry import StrategyRegistry
    from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.research.rigor.backtest_hub import serialize_backtest_trades
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    import pandas as pd

    def _run(strategy_id, request):
        registry = StrategyRegistry()
        strategy_class = registry.get(strategy_id)
        if strategy_class is None:
            return {"metrics": {}, "equity_curve": [], "trades": [], "errors": [f"Strategy {strategy_id} not in registry"]}

        symbols = list(request.get("symbols") or [])
        start_text = request.get("start")
        end_text = request.get("end")
        start = datetime.strptime(start_text, "%Y-%m-%d")
        end = datetime.strptime(end_text, "%Y-%m-%d")

        db_provider = DuckDBProvider()
        db_provider.connect()
        all_data = []
        try:
            for sym in symbols:
                bars = db_provider.get_bars(sym, start, end, "1d")
                if not bars.empty:
                    all_data.append(bars)
        finally:
            db_provider.disconnect()

        if not all_data:
            return {"metrics": {}, "equity_curve": [], "trades": [], "errors": [f"No data for {strategy_id}"]}

        data_df = pd.concat(all_data, ignore_index=True)
        strategy = strategy_class(symbols=symbols)
        bt_config = {
            "backtest": {"slippage_bps": request.get("cost_config", {}).get("slippage_bps", 5)},
            "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
            "data": {"default_timeframe": "1d"},
            "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
        }
        backtester = Backtester(bt_config, portfolio_class=Portfolio, risk_engine_class=RiskEngine, sub_portfolio_class=SubPortfolio)
        bt_result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=float(request.get("initial_cash", 100000)),
            data_provider=DataFrameProvider(data_df),
            symbols=symbols,
        )
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


research_bp = Blueprint("research", __name__)

_research_jobs: dict = {}
_research_lock = threading.Lock()
_research_scheduler: ResearchScheduler = None


def _make_research_store(cfg: ResearchConfig):
    from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore
    from quant.infrastructure.research.repository import FileResearchStore

    root = cfg.research_dir or str(Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research")
    if cfg.research_store_backend == "duckdb":
        return DuckDBResearchStore(root)
    return FileResearchStore(root)


def _make_experiment_store(cfg: ResearchConfig):
    if not cfg.tracking_enabled:
        return None
    from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore

    root = cfg.research_dir or str(Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research")
    return DuckDBExperimentStore(root, db_path=cfg.tracking_db_path or None)


def _make_artifact_store(cfg: ResearchConfig):
    if not cfg.tracking_enabled:
        return None
    from quant.infrastructure.research.file_artifact_store import FileArtifactStore

    root = cfg.research_dir or str(Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research")
    return FileArtifactStore(root)


def _make_rigor_hub(cfg: ResearchConfig, experiment_store=None):
    if not cfg.rigor_enabled or not cfg.auto_backtest:
        return None
    from quant.features.research.rigor import RigorHub

    return RigorHub(_make_rigor_backtest_runner(), config=cfg.rigor_config, experiment_store=experiment_store)


def _make_research_scout(cfg: ResearchConfig):
    from quant.features.research.scout import StrategyScout
    from quant.infrastructure.research.sources import ArxivSource, BlogSource, NBERSource, SSRNSource

    factories = {
        "arxiv": ArxivSource,
        "ssrn": SSRNSource,
        "nber": NBERSource,
        "blog": BlogSource,
    }
    source_names = cfg.sources or ["arxiv", "ssrn"]
    return StrategyScout(sources={name: factories[name]() for name in source_names if name in factories})


def _close_research_store(store) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        close()


def _create_llm_adapter(cfg: ResearchConfig):
    if cfg.llm_provider == "openai":
        from quant.features.cio.llm_adapters.openai_adapter import OpenAIAdapter
        return OpenAIAdapter(model=cfg.llm_model, api_key=cfg.llm_api_key or "", temperature=cfg.llm_temperature)
    elif cfg.llm_provider == "claude":
        from quant.features.cio.llm_adapters.claude_adapter import ClaudeAdapter
        return ClaudeAdapter(model=cfg.llm_model, api_key=cfg.llm_api_key or "", temperature=cfg.llm_temperature)
    elif cfg.llm_provider == "ollama":
        from quant.features.cio.llm_adapters.ollama_adapter import OllamaAdapter
        return OllamaAdapter(model=cfg.llm_model)
    elif cfg.llm_provider == "minimax":
        from quant.features.cio.llm_adapters.minimax_adapter import MiniMaxAdapter
        return MiniMaxAdapter(
            model=cfg.llm_model,
            api_key=cfg.llm_api_key or "",
            temperature=cfg.llm_temperature,
            base_url=cfg.llm_base_url or "https://api.minimax.chat/v1",
            group_id=cfg.llm_group_id or "",
        )
    elif cfg.llm_provider == "deepseek":
        from quant.features.cio.llm_adapters.deepseek_adapter import DeepSeekAdapter
        return DeepSeekAdapter(
            model=cfg.llm_model or "deepseek-chat",
            api_key=cfg.llm_api_key or "",
            temperature=cfg.llm_temperature,
            base_url=cfg.llm_base_url or "https://api.deepseek.com/v1",
        )
    elif cfg.llm_provider == "glm":
        from quant.features.cio.llm_adapters.glm_adapter import GLMAdapter
        return GLMAdapter(
            model=cfg.llm_model or "glm-5.1",
            api_key=cfg.llm_api_key or "",
            temperature=cfg.llm_temperature,
            base_url=cfg.llm_base_url or "https://api.z.ai/api/coding/paas/v4/",
        )
    return None


def _get_scheduler() -> ResearchScheduler:
    global _research_scheduler
    if _research_scheduler is None:
        cfg = _load_research_config()
        research_store = _make_research_store(cfg)
        experiment_store = _make_experiment_store(cfg)
        artifact_store = _make_artifact_store(cfg)
        llm_adapter = _create_llm_adapter(cfg)
        from quant.features.research.evaluator import StrategyEvaluator
        evaluator = StrategyEvaluator(llm_adapter=llm_adapter)
        engine = ResearchEngine(
            config=cfg,
            scout=_make_research_scout(cfg),
            evaluator=evaluator,
            backtest_fn=_make_backtest_fn(),
            research_store=research_store,
            experiment_store=experiment_store,
            artifact_store=artifact_store,
            rigor_hub=_make_rigor_hub(cfg, experiment_store=experiment_store),
        )
        _research_scheduler = ResearchScheduler(engine, cfg)
        if cfg.auto_run:
            _research_scheduler.start()
    return _research_scheduler


def _load_research_config() -> ResearchConfig:
    from quant.shared.utils.config_loader import ConfigLoader
    try:
        data = ConfigLoader.load("research")
        research_cfg = data.get("research", {})
        llm_cfg = data.get("llm", {})
        research_cfg.setdefault("llm_provider", llm_cfg.get("provider", "minimax"))
        research_cfg.setdefault("llm_model", llm_cfg.get("model", "MiniMax-M2.7"))
        research_cfg.setdefault("llm_api_key", llm_cfg.get("api_key"))
        research_cfg.setdefault("llm_temperature", llm_cfg.get("temperature", 0.3))
        research_cfg.setdefault("llm_base_url", llm_cfg.get("base_url"))
        research_cfg.setdefault("llm_group_id", llm_cfg.get("group_id"))
        return ResearchConfig(**research_cfg)
    except Exception:
        return ResearchConfig()


@research_bp.route("/api/research/run", methods=["POST"])
def run_research():
    data = request.get_json() or {}
    sources = data.get("sources")
    max_results = data.get("max_results", 10)
    job_id = str(uuid.uuid4())[:8]

    cfg = _load_research_config()
    if sources:
        cfg.sources = sources
    cfg.max_results_per_source = max_results

    llm_adapter = _create_llm_adapter(cfg)
    from quant.features.research.evaluator import StrategyEvaluator
    evaluator = StrategyEvaluator(llm_adapter=llm_adapter)
    research_store = _make_research_store(cfg)
    experiment_store = _make_experiment_store(cfg)
    artifact_store = _make_artifact_store(cfg)
    engine = ResearchEngine(
        config=cfg,
        scout=_make_research_scout(cfg),
        evaluator=evaluator,
        backtest_fn=_make_backtest_fn(),
        research_store=research_store,
        experiment_store=experiment_store,
        artifact_store=artifact_store,
        rigor_hub=_make_rigor_hub(cfg, experiment_store=experiment_store),
    )

    def _run():
        try:
            engine.run_full_pipeline(sources=sources, result=result_obj)
            with _research_lock:
                _research_jobs[job_id]["status"] = "completed"
        except Exception as e:
            with _research_lock:
                _research_jobs[job_id] = {"status": "error", "error": str(e)}
        finally:
            _close_research_store(research_store)
            _close_research_store(experiment_store)
            _close_research_store(artifact_store)

    result_obj = ResearchResult()
    with _research_lock:
        _research_jobs[job_id] = {"status": "running", "result": result_obj}
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return jsonify({"research_id": job_id, "status": "running"})


@research_bp.route("/api/research/status/<research_id>")
def get_research_status(research_id):
    with _research_lock:
        job = _research_jobs.get(research_id)
    if job is None:
        return jsonify({"error": "Research job not found"}), 404
    response = {"research_id": research_id, "status": job["status"]}
    result = job.get("result")
    if result is not None:
        response["result"] = result.to_dict()
    elif job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")
    return jsonify(response)


@research_bp.route("/api/research/candidates")
def list_candidates():
    store = _make_research_store(_load_research_config())
    try:
        pool = CandidatePool(research_store=store)
        return jsonify({"candidates": pool.list_candidates()})
    finally:
        _close_research_store(store)


@research_bp.route("/api/research/promote/<strategy_id>", methods=["POST"])
def promote_candidate(strategy_id):
    store = _make_research_store(_load_research_config())
    try:
        pool = CandidatePool(research_store=store)
        success = pool.promote(strategy_id)
        if success:
            return jsonify({"success": True, "strategy_id": strategy_id, "status": "paused"})
        return jsonify({"success": False, "error": "Promotion failed"}), 400
    finally:
        _close_research_store(store)


@research_bp.route("/api/research/reject/<strategy_id>", methods=["POST"])
def reject_candidate(strategy_id):
    data = request.get_json() or {}
    reason = data.get("reason", "")
    store = _make_research_store(_load_research_config())
    try:
        pool = CandidatePool(research_store=store)
        success = pool.reject(strategy_id, reason=reason)
        if success:
            return jsonify({"success": True, "strategy_id": strategy_id, "status": "rejected"})
        return jsonify({"success": False, "error": "Rejection failed"}), 400
    finally:
        _close_research_store(store)


@research_bp.route("/api/research/schedule", methods=["GET"])
def get_schedule():
    cfg = _load_research_config()
    return jsonify({
        "auto_run": cfg.auto_run,
        "interval_days": cfg.interval_days,
        "sources": cfg.sources,
        "max_results_per_source": cfg.max_results_per_source,
        "evaluation_threshold": cfg.evaluation_threshold,
        "backtest_sharpe_threshold": cfg.backtest_sharpe_threshold,
        "auto_backtest": cfg.auto_backtest,
        "llm_provider": cfg.llm_provider,
        "llm_model": cfg.llm_model,
        "llm_api_key_set": cfg.llm_api_key is not None,
    })


@research_bp.route("/api/research/schedule", methods=["POST"])
def update_schedule():
    data = request.get_json() or {}
    scheduler = _get_scheduler()
    if data.get("auto_run") and not scheduler.is_running:
        scheduler.start()
    elif not data.get("auto_run") and scheduler.is_running:
        scheduler.stop()
    return jsonify({"success": True, "schedule": data})


@research_bp.route("/api/research/run-scheduled", methods=["POST"])
def trigger_scheduled():
    scheduler = _get_scheduler()
    scheduler.trigger_now()
    return jsonify({"success": True, "message": "Scheduled research triggered"})


@research_bp.route("/api/research/experiments/<strategy_id>", methods=["GET"])
def list_research_experiments(strategy_id):
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Research tracking is disabled"}), 404
    try:
        limit = int(request.args.get("limit", 50))
        runs = StrategyComparator(store).list_runs(strategy_id=strategy_id, limit=limit)
        return jsonify({"strategy_id": strategy_id, "runs": runs})
    finally:
        _close_research_store(store)


@research_bp.route("/api/research/experiments/<strategy_id>/<run_id>", methods=["GET"])
def get_research_experiment(strategy_id, run_id):
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Research tracking is disabled"}), 404
    try:
        run = store.get_run(run_id)
        if run is None or run.get("strategy_id") != strategy_id:
            return jsonify({"error": "Experiment run not found"}), 404
        return jsonify({
            "strategy_id": strategy_id,
            "run": run,
            "metrics": store.list_metrics(run_id),
            "artifacts": store.get_artifacts(run_id),
        })
    finally:
        _close_research_store(store)


@research_bp.route("/api/research/compare", methods=["GET"])
def compare_research_experiments():
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Research tracking is disabled"}), 404
    try:
        ids = [item.strip() for item in request.args.get("ids", "").split(",") if item.strip()]
        metric = request.args.get("metric", "sharpe")
        comparator = StrategyComparator(store)
        rows = []
        for strategy_id in ids:
            best = comparator.best_metric(metric, strategy_id=strategy_id)
            rows.append({"strategy_id": strategy_id, "metric": metric, "best": best})
        return jsonify({"ids": ids, "metric": metric, "results": rows})
    finally:
        _close_research_store(store)


@research_bp.route("/api/research/ensemble", methods=["GET"])
def get_research_ensemble():
    cfg = _load_research_config()
    experiment_store = _make_experiment_store(cfg)
    artifact_store = _make_artifact_store(cfg)
    if experiment_store is None or artifact_store is None:
        _close_research_store(experiment_store)
        _close_research_store(artifact_store)
        return jsonify({"error": "Research tracking is disabled"}), 404
    try:
        ids = [item.strip() for item in request.args.get("ids", "").split(",") if item.strip()]
        result = ResearchEnsembleBuilder(experiment_store, artifact_store, cfg.ensemble_config).build(ids or None)
        return jsonify(result)
    finally:
        _close_research_store(experiment_store)
        _close_research_store(artifact_store)


@research_bp.route("/api/research/ensemble/rebuild", methods=["POST"])
def rebuild_research_ensemble():
    cfg = _load_research_config()
    data = request.get_json() or {}
    experiment_store = _make_experiment_store(cfg)
    artifact_store = _make_artifact_store(cfg)
    if experiment_store is None or artifact_store is None:
        _close_research_store(experiment_store)
        _close_research_store(artifact_store)
        return jsonify({"error": "Research tracking is disabled"}), 404
    run_id = ""
    try:
        ids = data.get("strategy_ids") or data.get("ids")
        if isinstance(ids, str):
            ids = [item.strip() for item in ids.split(",") if item.strip()]
        if data.get("record_run", True):
            run_id = experiment_store.start_run("research_ensemble", {"strategy_ids": ids or []})
        result = ResearchEnsembleBuilder(experiment_store, artifact_store, cfg.ensemble_config).build(ids or None, run_id=run_id)
        if run_id:
            experiment_store.complete_run(run_id, "completed")
        return jsonify(result)
    except Exception as exc:
        if run_id:
            experiment_store.complete_run(run_id, "failed", error=str(exc))
        return jsonify({"error": str(exc)}), 500
    finally:
        _close_research_store(experiment_store)
        _close_research_store(artifact_store)
