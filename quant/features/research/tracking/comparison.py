from typing import Any, Dict, List


class StrategyComparator:
    def __init__(self, experiment_store: Any):
        self._store = experiment_store

    def compare(self, strategy_ids: List[str], metric_name: str = "sharpe") -> List[Dict[str, Any]]:
        results = []
        for sid in strategy_ids:
            runs = self._store.list_runs(strategy_id=sid, limit=10)
            for run in runs:
                metrics = self._store.list_metrics(run["run_id"])
                for m in metrics:
                    if m.get("metric_name") == metric_name:
                        results.append({
                            "strategy_id": sid,
                            "run_id": run["run_id"],
                            "metric_value": m.get("metric_value"),
                            "window_type": m.get("window_type"),
                            "window_label": m.get("window_label"),
                        })
        return results
