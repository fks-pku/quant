from datetime import datetime

from quant.research.models import (
    RawIdea,
    ResearchReport,
    ResearchTask,
    ScoredIdea,
    StrategyCandidate,
)


def _make_raw_idea(**overrides):
    defaults = dict(
        id="idea-1",
        source="arxiv",
        source_url="https://arxiv.org/abs/2401.00001",
        title="Momentum in Emerging Markets",
        description="A study of momentum strategies in emerging markets.",
    )
    defaults.update(overrides)
    return RawIdea(**defaults)


def _make_scored_idea(**overrides):
    raw = overrides.pop("raw_idea", _make_raw_idea())
    defaults = dict(
        id="scored-1",
        raw_idea=raw,
        feasibility_score=0.8,
        academic_rigor=0.7,
        backtestability=0.9,
        compatibility=0.6,
        novelty=0.5,
        implementation_plan="Implement momentum factor with 12-month lookback.",
    )
    defaults.update(overrides)
    return ScoredIdea(**defaults)


def _make_candidate(**overrides):
    scored = overrides.pop("scored_idea", _make_scored_idea())
    defaults = dict(
        id="cand-1",
        scored_idea=scored,
        strategy_name="EmergingMomentum",
        code_path="quant/strategies/emerging_momentum/strategy.py",
        config_path="quant/strategies/emerging_momentum/config.yaml",
    )
    defaults.update(overrides)
    return StrategyCandidate(**defaults)


def _make_report(**overrides):
    candidate = overrides.pop("candidate", _make_candidate())
    defaults = dict(
        id="report-1",
        candidate=candidate,
        backtest_metrics={"sharpe": 1.2, "max_drawdown": -0.15},
    )
    defaults.update(overrides)
    return ResearchReport(**defaults)


class TestRawIdea:
    def test_creation_with_all_fields(self):
        now = datetime.now()
        idea = RawIdea(
            id="idea-1",
            source="ssrn",
            source_url="https://ssrn.com/123",
            title="Test",
            description="Desc",
            published_date="2024-01-01",
            metadata={"author": "Smith"},
            discovered_at=now,
        )
        assert idea.id == "idea-1"
        assert idea.source == "ssrn"
        assert idea.source_url == "https://ssrn.com/123"
        assert idea.title == "Test"
        assert idea.description == "Desc"
        assert idea.published_date == "2024-01-01"
        assert idea.metadata == {"author": "Smith"}
        assert idea.discovered_at == now

    def test_defaults(self):
        idea = _make_raw_idea()
        assert idea.published_date is None
        assert idea.metadata == {}
        assert isinstance(idea.discovered_at, datetime)


class TestScoredIdea:
    def test_creation(self):
        scored = _make_scored_idea()
        assert scored.id == "scored-1"
        assert scored.feasibility_score == 0.8
        assert scored.academic_rigor == 0.7
        assert scored.backtestability == 0.9
        assert scored.compatibility == 0.6
        assert scored.novelty == 0.5
        assert isinstance(scored.raw_idea, RawIdea)
        assert isinstance(scored.implementation_plan, str)

    def test_defaults(self):
        scored = _make_scored_idea()
        assert scored.suggested_factors == []
        assert scored.suggested_params == {}
        assert scored.risk_assessment == ""
        assert isinstance(scored.scored_at, datetime)


class TestStrategyCandidate:
    def test_creation(self):
        cand = _make_candidate()
        assert cand.id == "cand-1"
        assert cand.strategy_name == "EmergingMomentum"
        assert cand.code_path == "quant/strategies/emerging_momentum/strategy.py"
        assert cand.config_path == "quant/strategies/emerging_momentum/config.yaml"
        assert isinstance(cand.scored_idea, ScoredIdea)

    def test_registered_default(self):
        cand = _make_candidate()
        assert cand.registered is False

    def test_registered_explicit(self):
        cand = _make_candidate(registered=True)
        assert cand.registered is True


class TestResearchReport:
    def test_creation(self):
        report = _make_report()
        assert report.id == "report-1"
        assert report.backtest_metrics == {"sharpe": 1.2, "max_drawdown": -0.15}
        assert isinstance(report.candidate, StrategyCandidate)

    def test_default_recommendation(self):
        report = _make_report()
        assert report.recommendation == "watchlist"

    def test_custom_recommendation(self):
        report = _make_report(recommendation="deploy")
        assert report.recommendation == "deploy"

    def test_defaults(self):
        report = _make_report()
        assert report.walkforward_result is None
        assert report.llm_analysis == ""
        assert report.recommendation_reasoning == ""
        assert report.comparison == {}
        assert isinstance(report.generated_at, datetime)


class TestResearchTask:
    def test_creation(self):
        task = ResearchTask(id="task-1", status="pending")
        assert task.id == "task-1"
        assert task.status == "pending"

    def test_empty_lists_default(self):
        task = ResearchTask(id="task-1", status="pending")
        assert task.ideas == []
        assert task.scored_ideas == []
        assert task.candidates == []
        assert task.reports == []
        assert task.error_log == []

    def test_defaults(self):
        task = ResearchTask(id="task-1", status="pending")
        assert task.topic is None
        assert task.max_ideas == 5
        assert task.completed_at is None
        assert isinstance(task.created_at, datetime)

    def test_status_transitions(self):
        task = ResearchTask(id="task-1", status="pending")
        assert task.status == "pending"

        task.status = "searching"
        assert task.status == "searching"

        task.ideas.append(_make_raw_idea())
        task.status = "scoring"
        assert task.status == "scoring"
        assert len(task.ideas) == 1

        task.scored_ideas.append(_make_scored_idea())
        task.status = "coding"
        assert task.status == "coding"

        task.candidates.append(_make_candidate())
        task.status = "evaluating"
        assert task.status == "evaluating"

        task.reports.append(_make_report())
        task.status = "completed"
        task.completed_at = datetime.now()
        assert task.status == "completed"
        assert task.completed_at is not None

    def test_error_logging(self):
        task = ResearchTask(id="task-1", status="pending")
        task.status = "failed"
        task.error_log.append("API timeout")
        task.error_log.append("Rate limit exceeded")
        assert len(task.error_log) == 2
        assert task.error_log[0] == "API timeout"
