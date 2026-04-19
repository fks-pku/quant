import asyncio
import uuid
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from quant.core.events import EventBus, EventType
from quant.research.agents.searcher import SearcherAgent
from quant.research.agents.analyst import AnalystAgent
from quant.research.agents.coder import CoderAgent
from quant.research.agents.evaluator import EvaluatorAgent
from quant.research.models import ResearchTask


class ResearchCoordinator:
    def __init__(
        self,
        event_bus: EventBus,
        llm_adapter,
        data_provider=None,
        backtest_config: dict = None,
        config: dict = None,
    ):
        self.event_bus = event_bus
        self.llm = llm_adapter
        self.data_provider = data_provider
        self.backtest_config = backtest_config or {}
        self.config = config or {}

        self._searcher = SearcherAgent(
            llm_adapter=llm_adapter,
            sources=[],
            max_concurrent=self.config.get("search", {}).get("max_concurrent_sources", 3),
        )
        self._analyst = AnalystAgent(llm_adapter)
        self._coder = CoderAgent(llm_adapter)
        self._evaluator = EvaluatorAgent(llm_adapter, data_provider, backtest_config)

        self._active_tasks: dict = {}

    async def _run_pipeline(self, task: ResearchTask):
        try:
            task.status = "SEARCHING"
            ideas = await self._searcher.search(topic=task.topic, max_ideas=task.max_ideas * 3)
            task.ideas = ideas
        except Exception as e:
            task.error_log.append(f"Search error: {e}")
            task.status = "ERROR"
            return

        try:
            task.status = "ANALYZING"
            scored = await self._analyst.analyze(
                task.ideas,
                min_score=self.config.get("analysis", {}).get("min_feasibility_score", 60),
            )
            task.scored_ideas = scored[:task.max_ideas]
        except Exception as e:
            task.error_log.append(f"Analysis error: {e}")
            task.status = "ERROR"
            return

        try:
            task.status = "CODING"
            for idea in task.scored_ideas:
                try:
                    candidate = await self._coder.implement(idea)
                    task.candidates.append(candidate)
                except Exception as e:
                    task.error_log.append(f"Coding error for {idea.raw_idea.title}: {e}")
        except Exception as e:
            task.error_log.append(f"Coding phase error: {e}")
            task.status = "ERROR"
            return

        try:
            task.status = "EVALUATING"
            symbols = self.config.get("evaluation", {}).get(
                "symbols", ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
            )
            for candidate in task.candidates:
                try:
                    report = await self._evaluator.evaluate(candidate, symbols)
                    task.reports.append(report)
                except Exception as e:
                    task.error_log.append(f"Evaluation error for {candidate.strategy_name}: {e}")
        except Exception as e:
            task.error_log.append(f"Evaluation phase error: {e}")
            task.status = "ERROR"
            return

        task.status = "COMPLETED"
        task.completed_at = datetime.now()
        self._save_reports(task)

    def _save_reports(self, task: ResearchTask):
        output_dir = Path("quant/research/reports") / f"{datetime.now().strftime('%Y-%m-%d')}_task_{task.id[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "task_id": task.id,
            "topic": task.topic,
            "status": task.status,
            "ideas_found": len(task.ideas),
            "ideas_scored": len(task.scored_ideas),
            "candidates": len(task.candidates),
            "reports": [
                {
                    "strategy": r.candidate.strategy_name,
                    "recommendation": r.recommendation,
                    "sharpe": r.backtest_metrics.get("sharpe_ratio") if r.backtest_metrics else None,
                }
                for r in task.reports
            ],
            "errors": task.error_log,
            "created_at": str(task.created_at),
            "completed_at": str(task.completed_at) if task.completed_at else None,
        }

        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    def start_research(self, topic: Optional[str] = None, max_ideas: int = 5) -> ResearchTask:
        task = ResearchTask(
            id=str(uuid.uuid4()),
            status="CREATED",
            topic=topic,
            max_ideas=max_ideas,
            created_at=datetime.now(),
        )
        self._active_tasks[task.id] = task
        asyncio.create_task(self._run_pipeline(task))
        return task

    def get_task(self, task_id: str) -> Optional[ResearchTask]:
        return self._active_tasks.get(task_id)

    def list_tasks(self) -> List[ResearchTask]:
        return list(self._active_tasks.values())
