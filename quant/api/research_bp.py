import uuid
import threading
from datetime import datetime
from pathlib import Path
from flask import Blueprint, jsonify, request

from quant.features.research.models import ResearchConfig, ResearchResult
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.pool import CandidatePool
from quant.features.research.scheduler import ResearchScheduler


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


def _serialize_walkforward_trade(trade, data_df):
    symbol = getattr(trade, "symbol", "")
    side = getattr(trade, "side", "")
    quantity = _safe_float(getattr(trade, "quantity", 0.0))
    price = _trade_price(trade)
    trade_value = abs(quantity) * price
    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "pnl": _safe_float(getattr(trade, "pnl", 0.0)),
        "trade_value": trade_value,
        "avg_daily_volume": _average_daily_volume(data_df, symbol, _trade_date(trade)),
    }


def _trade_price(trade) -> float:
    side = str(getattr(trade, "side", "")).upper()
    names = ["price", "fill_price"]
    names.extend(["exit_price", "entry_price"] if side == "SELL" else ["entry_price", "exit_price"])
    for name in names:
        value = _safe_float(getattr(trade, name, 0.0))
        if value > 0:
            return value
    return 0.0


def _trade_date(trade):
    for name in ("fill_date", "signal_date", "exit_time", "entry_time", "timestamp"):
        value = getattr(trade, name, None)
        if value is not None:
            return value
    return None


def _average_daily_volume(data_df, symbol, trade_date=None) -> float:
    if data_df is None or not hasattr(data_df, "empty") or data_df.empty or "volume" not in data_df.columns:
        return 0.0
    try:
        import pandas as pd

        data = data_df
        if symbol and "symbol" in data.columns:
            data = data[data["symbol"].astype(str) == str(symbol)]
        if data.empty:
            return 0.0

        date_col = "timestamp" if "timestamp" in data.columns else "date" if "date" in data.columns else None
        if date_col is not None:
            dated = data.copy()
            dated[date_col] = pd.to_datetime(dated[date_col], errors="coerce")
            dated = dated.dropna(subset=[date_col]).sort_values(date_col)
            if dated.empty:
                return _median_volume(data)
            if trade_date is not None:
                trade_ts = pd.Timestamp(trade_date).normalize()
                dated_days = dated[date_col].dt.normalize()
                exact = dated[dated_days == trade_ts]
                if not exact.empty:
                    return float(exact["volume"].mean())
                prior = dated[dated[date_col] <= pd.Timestamp(trade_date)].tail(63)
                if not prior.empty:
                    return float(prior["volume"].median())
            return float(dated.tail(63)["volume"].median())
        return _median_volume(data)
    except Exception:
        return 0.0


def _median_volume(data) -> float:
    try:
        return float(data["volume"].median())
    except Exception:
        return 0.0


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _make_walkforward_runner():
    from quant.features.backtest.engine import Backtester
    from quant.features.strategies.registry import StrategyRegistry
    from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
    from quant.features.backtest.walkforward import DataFrameProvider
    from quant.features.trading.portfolio import Portfolio
    from quant.features.trading.risk import RiskEngine
    from quant.features.trading.sub_portfolio import SubPortfolio
    import pandas as pd

    def _run_walkforward_backtest(sid, request):
        registry = StrategyRegistry()
        strategy_class = registry.get(sid)
        if strategy_class is None:
            return {"metrics": {"sharpe": 0.0, "max_dd": 0.0, "cagr": 0.0, "win_rate": 0.0}, "returns": pd.Series(dtype=float)}

        symbols = request.get("symbols") or []
        start = datetime.strptime(str(request["start"]), "%Y-%m-%d")
        end = datetime.strptime(str(request["end"]), "%Y-%m-%d")
        initial_cash = float(request.get("initial_cash", 100000))

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
            return {"metrics": {"sharpe": 0.0, "max_dd": 0.0, "cagr": 0.0, "win_rate": 0.0}, "returns": pd.Series(dtype=float)}

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
        bt_result = backtester.run(start=start, end=end, strategies=[strategy], initial_cash=initial_cash, data_provider=data_provider, symbols=symbols)
        returns = bt_result.equity_curve.pct_change().dropna() if hasattr(bt_result, "equity_curve") else pd.Series(dtype=float)
        days = max(1, (end - start).days)
        trades = [_serialize_walkforward_trade(trade, data_df) for trade in getattr(bt_result, "trades", [])]
        return {
            "metrics": {
                "sharpe": float(getattr(bt_result, "sharpe_ratio", 0.0)),
                "max_dd": float(getattr(bt_result, "max_drawdown_pct", 0.0)),
                "cagr": float(getattr(bt_result, "total_return", 0.0) * 100 / max(1, days / 365.25)),
                "win_rate": float(getattr(bt_result, "win_rate", 0.0) * 100),
            },
            "returns": returns,
            "trades": trades,
        }

    return _run_walkforward_backtest


research_bp = Blueprint("research", __name__)

_research_jobs: dict = {}
_research_lock = threading.Lock()
_research_scheduler: ResearchScheduler = None


def _make_research_store(cfg: ResearchConfig):
    root = cfg.research_dir or str(Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research")
    if getattr(cfg, "tracking_enabled", False) and cfg.tracking_db_path:
        from quant.infrastructure.research.duckdb_research_store import DuckDBResearchStore

        return DuckDBResearchStore(db_path=cfg.tracking_db_path, artifact_root=root)
    from quant.infrastructure.research.repository import FileResearchStore

    return FileResearchStore(root)


def _make_experiment_stores(cfg: ResearchConfig):
    if not getattr(cfg, "tracking_enabled", False):
        return None, None
    from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore
    from quant.infrastructure.research.file_artifact_store import FileArtifactStore
    db_path = cfg.tracking_db_path or str(
        Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research" / "experiments.duckdb"
    )
    artifact_root = cfg.research_dir or str(
        Path(__file__).resolve().parent.parent / "infrastructure" / "var" / "research" / "artifacts"
    )
    return DuckDBExperimentStore(db_path), FileArtifactStore(artifact_root)


def _make_experiment_store(cfg: ResearchConfig):
    if not getattr(cfg, "tracking_enabled", False) or not cfg.tracking_db_path:
        return None
    from quant.infrastructure.research.duckdb_experiment_store import DuckDBExperimentStore
    return DuckDBExperimentStore(cfg.tracking_db_path)


def _make_rigor_hub(cfg: ResearchConfig, experiment_store=None):
    from quant.features.research.rigor.backtest_hub import RigorHub
    return RigorHub(
        backtest_runner=_make_walkforward_runner(),
        config=cfg.rigor_config,
        experiment_store=experiment_store,
    )


def _make_strategy_scout(cfg: ResearchConfig):
    from quant.features.research.discovery.source_hub import SourceHub
    from quant.features.research.scout import StrategyScout
    from quant.infrastructure.research.sources import ArxivSource, BlogSource, NBERSource, SSRNSource

    source_hub = SourceHub({
        "arxiv": ArxivSource(),
        "ssrn": SSRNSource(),
        "nber": NBERSource(),
        "blog": BlogSource(),
    })
    return StrategyScout.from_source_hub(source_hub, sources=getattr(cfg, "sources", None))


def _make_pit_data(cfg: ResearchConfig):
    if not getattr(cfg, "pit_enabled", False):
        return None
    from quant.infrastructure.research.pit_duckdb import PITDuckDBData

    return PITDuckDBData()


def _make_research_market_data(cfg: ResearchConfig, as_of_date: str = None):
    from quant.infrastructure.research.market_data import DuckDBResearchMarketData

    return DuckDBResearchMarketData(
        pit_data=_make_pit_data(cfg),
        pit_as_of_date=as_of_date or getattr(cfg, "default_backtest_end", None),
    )


def _make_benchmark_data_loader(cfg: ResearchConfig):
    from quant.features.research.rigor.regime_detector import benchmark_symbol_for_universe

    market_data = _make_research_market_data(cfg)

    def _load(symbols, start, end):
        benchmark_symbol = benchmark_symbol_for_universe(symbols)
        data = market_data.get_daily_bars([benchmark_symbol], start, end)
        if data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return None
        return data

    return _load


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
        llm_adapter = _create_llm_adapter(cfg)
        from quant.features.research.evaluator import StrategyEvaluator
        evaluator = StrategyEvaluator(llm_adapter=llm_adapter)
        experiment_store, artifact_store = _make_experiment_stores(cfg)
        engine = ResearchEngine(
            config=cfg,
            scout=_make_strategy_scout(cfg),
            evaluator=evaluator,
            backtest_fn=_make_backtest_fn(),
            research_store=research_store,
            rigor_hub=_make_rigor_hub(cfg, experiment_store=experiment_store) if cfg.rigor_enabled else None,
            benchmark_data_loader=_make_benchmark_data_loader(cfg) if cfg.rigor_enabled else None,
        )
        if experiment_store:
            engine._experiment_store = experiment_store
            engine._artifact_store = artifact_store
            if getattr(cfg, "ensemble_enabled", False):
                from quant.features.research.ensemble.ensemble import StrategyEnsemble
                engine._ensemble = StrategyEnsemble(experiment_store, cfg.ensemble_config)
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
    experiment_store, artifact_store = _make_experiment_stores(cfg)
    ensemble = None
    if getattr(cfg, "ensemble_enabled", False) and experiment_store is not None:
        from quant.features.research.ensemble.ensemble import StrategyEnsemble
        ensemble = StrategyEnsemble(experiment_store, cfg.ensemble_config)
    engine = ResearchEngine(
        config=cfg,
        scout=_make_strategy_scout(cfg),
        evaluator=evaluator,
        backtest_fn=_make_backtest_fn(),
        research_store=_make_research_store(cfg),
        experiment_store=experiment_store,
        artifact_store=artifact_store,
        rigor_hub=_make_rigor_hub(cfg, experiment_store=experiment_store) if cfg.rigor_enabled else None,
        ensemble=ensemble,
        benchmark_data_loader=_make_benchmark_data_loader(cfg) if cfg.rigor_enabled else None,
    )

    def _run():
        try:
            engine.run_full_pipeline(sources=sources, result=result_obj)
            with _research_lock:
                _research_jobs[job_id]["status"] = "completed"
        except Exception as e:
            with _research_lock:
                _research_jobs[job_id] = {"status": "error", "error": str(e)}

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
    pool = CandidatePool(research_store=_make_research_store(_load_research_config()))
    return jsonify({"candidates": pool.list_candidates()})


@research_bp.route("/api/research/promote/<strategy_id>", methods=["POST"])
def promote_candidate(strategy_id):
    pool = CandidatePool(research_store=_make_research_store(_load_research_config()))
    success = pool.promote(strategy_id)
    if success:
        return jsonify({"success": True, "strategy_id": strategy_id, "status": "paused"})
    return jsonify({"success": False, "error": "Promotion failed"}), 400


@research_bp.route("/api/research/reject/<strategy_id>", methods=["POST"])
def reject_candidate(strategy_id):
    data = request.get_json() or {}
    reason = data.get("reason", "")
    pool = CandidatePool(research_store=_make_research_store(_load_research_config()))
    success = pool.reject(strategy_id, reason=reason)
    if success:
        return jsonify({"success": True, "strategy_id": strategy_id, "status": "rejected"})
    return jsonify({"success": False, "error": "Rejection failed"}), 400


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


@research_bp.route("/api/research/experiments/<strategy_id>")
def list_experiments(strategy_id):
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    runs = store.list_runs(strategy_id=strategy_id)
    return jsonify({"runs": runs})


@research_bp.route("/api/research/experiments/<strategy_id>/<run_id>")
def get_experiment(strategy_id, run_id):
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    run = store.get_run(run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    metrics = store.list_metrics(run_id)
    run["metrics"] = metrics
    return jsonify(run)


@research_bp.route("/api/research/compare")
def compare_strategies():
    ids_str = request.args.get("ids", "")
    metric = request.args.get("metric", "sharpe")
    ids = [i.strip() for i in ids_str.split(",") if i.strip()]
    if not ids:
        return jsonify({"error": "Provide ids parameter"}), 400
    cfg = _load_research_config()
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    from quant.features.research.tracking.comparison import StrategyComparator
    comparator = StrategyComparator(store)
    results = comparator.compare(ids, metric_name=metric)
    return jsonify({"comparisons": results})


@research_bp.route("/api/research/ensemble")
def get_ensemble():
    cfg = _load_research_config()
    pool = CandidatePool(research_store=_make_research_store(cfg))
    candidates = pool.list_candidates()
    if len(candidates) < 2:
        return jsonify({"ensemble": None, "message": "Need at least 2 candidates"})
    store = _make_experiment_store(cfg)
    if store is None:
        return jsonify({"error": "Experiment tracking not configured"}), 400
    from quant.features.research.ensemble.ensemble import StrategyEnsemble
    ensemble = StrategyEnsemble(store, cfg.ensemble_config)
    ids = [c["id"] for c in candidates]
    result = ensemble.build(ids)
    return jsonify({"ensemble": {
        "strategy_ids": result.strategy_ids,
        "weights": result.weights,
        "portfolio_sharpe": result.portfolio_sharpe,
        "diversification_ratio": result.diversification_ratio,
        "mean_correlation": result.mean_correlation,
        "effective_n": result.effective_n,
    }})


@research_bp.route("/api/research/ensemble/rebuild", methods=["POST"])
def rebuild_ensemble():
    return get_ensemble()
