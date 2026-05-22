from pathlib import Path
import re

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _case_ids(path: Path, pattern: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {match.group(1).lower() for match in re.finditer(pattern, text, re.MULTILINE)}


@pytest.mark.parametrize(
    ("doc_path", "test_path"),
    [
        (
            REPO_ROOT / "quant/features/backtest/docs/backtest-invariants.md",
            REPO_ROOT / "quant/tests/test_backtest_invariants.py",
        ),
        (
            REPO_ROOT / "quant/features/strategies/docs/strategy-invariants.md",
            REPO_ROOT / "quant/tests/test_strategies_invariants.py",
        ),
    ],
    ids=["backtest", "strategies"],
)
def test_invariant_docs_match_case_tests(doc_path: Path, test_path: Path):
    doc_cases = _case_ids(doc_path, r"^## CASE-(\d+[A-Za-z]?)")
    test_cases = _case_ids(test_path, r"^# CASE-(\d+[A-Za-z]?)")

    assert doc_cases == test_cases
