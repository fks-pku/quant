import uuid
import threading
from flask import Blueprint, jsonify, request

from quant.features.research.models import ResearchConfig
from quant.features.research.research_engine import ResearchEngine
from quant.features.research.pool import CandidatePool
from quant.features.research.scheduler import ResearchScheduler

research_bp = Blueprint("research", __name__)

_research_jobs: dict = {}
_research_lock = threading.Lock()
_research_scheduler: ResearchScheduler = None


def _get_scheduler() -> ResearchScheduler:
    global _research_scheduler
    if _research_scheduler is None:
        cfg = _load_research_config()
        engine = ResearchEngine(config=cfg)
        _research_scheduler = ResearchScheduler(engine, cfg)
        if cfg.auto_run:
            _research_scheduler.start()
    return _research_scheduler


def _load_research_config() -> ResearchConfig:
    from quant.shared.utils.config_loader import ConfigLoader
    try:
        data = ConfigLoader.load("research")
        return ResearchConfig(**data.get("research", {}))
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

    engine = ResearchEngine(config=cfg)

    def _run():
        try:
            result = engine.run_full_pipeline(sources=sources)
            with _research_lock:
                _research_jobs[job_id] = {"status": "completed", "result": result}
        except Exception as e:
            with _research_lock:
                _research_jobs[job_id] = {"status": "error", "error": str(e)}

    with _research_lock:
        _research_jobs[job_id] = {"status": "running"}
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
    if job["status"] == "completed":
        result = job["result"]
        response["result"] = {
            "discovered": result.discovered,
            "evaluated": result.evaluated,
            "integrated": result.integrated,
            "backtested": result.backtested,
            "rejected": result.rejected,
            "errors": result.errors,
        }
    elif job["status"] == "error":
        response["error"] = job.get("error", "Unknown error")
    return jsonify(response)


@research_bp.route("/api/research/candidates")
def list_candidates():
    pool = CandidatePool()
    return jsonify({"candidates": pool.list_candidates()})


@research_bp.route("/api/research/promote/<strategy_id>", methods=["POST"])
def promote_candidate(strategy_id):
    pool = CandidatePool()
    success = pool.promote(strategy_id)
    if success:
        return jsonify({"success": True, "strategy_id": strategy_id, "status": "paused"})
    return jsonify({"success": False, "error": "Promotion failed"}), 400


@research_bp.route("/api/research/reject/<strategy_id>", methods=["POST"])
def reject_candidate(strategy_id):
    data = request.get_json() or {}
    reason = data.get("reason", "")
    pool = CandidatePool()
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
