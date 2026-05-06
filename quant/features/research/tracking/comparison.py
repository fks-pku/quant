from typing import Any, Dict, List, Optional

from quant.domain.ports import ExperimentStore


class StrategyComparator:
    def __init__(self, experiment_store: ExperimentStore):
        self.experiment_store = experiment_store

    def list_runs(self, strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return self.experiment_store.list_runs(strategy_id=strategy_id, limit=limit)

    def metrics_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        return self.experiment_store.list_metrics(run_id)

    def best_metric(self, metric_name: str, strategy_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        best: Optional[Dict[str, Any]] = None
        for run in self.experiment_store.list_runs(strategy_id=strategy_id, limit=1000):
            for metric in self.experiment_store.list_metrics(run["run_id"]):
                if metric.get("metric_name") != metric_name:
                    continue
                if best is None or float(metric.get("metric_value", 0.0)) > float(best.get("metric_value", 0.0)):
                    best = metric
        return best
