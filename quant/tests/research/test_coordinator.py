import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from quant.research.coordinator import ResearchCoordinator
from quant.core.events import EventBus


def test_coordinator_creates_task():
    event_bus = EventBus()
    mock_llm = MagicMock()
    mock_llm.analyze.return_value = {}

    coordinator = ResearchCoordinator(
        event_bus=event_bus,
        llm_adapter=mock_llm,
        config={"search": {"max_concurrent_sources": 1}, "analysis": {"min_feasibility_score": 60}, "evaluation": {"symbols": ["AAPL"]}},
    )

    with patch("quant.research.coordinator.asyncio.create_task"):
        task = coordinator.start_research(topic="momentum", max_ideas=1)
    assert task.status == "CREATED"
    assert task.topic == "momentum"
    assert task.max_ideas == 1
    assert task.id in coordinator._active_tasks


def test_coordinator_get_task():
    event_bus = EventBus()
    mock_llm = MagicMock()
    coordinator = ResearchCoordinator(event_bus=event_bus, llm_adapter=mock_llm)

    with patch("quant.research.coordinator.asyncio.create_task"):
        task = coordinator.start_research(topic="test")
    retrieved = coordinator.get_task(task.id)
    assert retrieved is task


def test_coordinator_list_tasks():
    event_bus = EventBus()
    mock_llm = MagicMock()
    coordinator = ResearchCoordinator(event_bus=event_bus, llm_adapter=mock_llm)

    with patch("quant.research.coordinator.asyncio.create_task"):
        coordinator.start_research(topic="test1")
        coordinator.start_research(topic="test2")
    tasks = coordinator.list_tasks()
    assert len(tasks) == 2
