from quant.infrastructure.events import EventType


def test_research_event_types_exist():
    assert EventType.RESEARCH_SEARCH_DONE.value == "research_search_done"
    assert EventType.RESEARCH_IDEA_SCORED.value == "research_idea_scored"
    assert EventType.RESEARCH_CODE_READY.value == "research_code_ready"
    assert EventType.RESEARCH_REPORT_DONE.value == "research_report_done"
    assert EventType.RESEARCH_ERROR.value == "research_error"


def test_all_event_types_count():
    assert len(EventType) >= 18
