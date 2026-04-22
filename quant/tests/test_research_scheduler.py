import pytest
from unittest.mock import patch, MagicMock
from quant.features.research.scheduler import ResearchScheduler
from quant.features.research.models import ResearchConfig


def test_scheduler_starts_and_stops():
    engine = MagicMock()
    cfg = ResearchConfig(auto_run=False, interval_days=1)
    sched = ResearchScheduler(engine, cfg)
    assert sched.is_running is False
    sched.start()
    assert sched.is_running is True
    sched.stop()
    assert sched.is_running is False
