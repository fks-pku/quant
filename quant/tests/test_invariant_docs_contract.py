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


def test_trading_invariants_describe_strategy_dashboard_lifecycle():
    text = (REPO_ROOT / "quant/features/trading/docs/trading-invariants.md").read_text(
        encoding="utf-8"
    )
    expected_fragments = [
        "### Strategy Dashboard Lifecycle",
        "configure/start -> restore positions -> D close data/OSS -> "
        "D pending-only signal generation -> D+1 order execution -> "
        "fill/position/NAV sync -> next close snapshot",
        "Live and paper share the signal-generation contract, diverge only at execution adapters",
        "pause/stop/liquidating states must block D+1 execution for that mode",
        "record_pending_only",
        "idempotent by DB economic signature",
        "must reuse the existing pending order id",
        "must not require D+1 bars",
        "must not call broker `submit_order`",
        "business stage, not wall-clock run date",
        "execution_date market-open timestamp",
        "manual replay run after midnight",
        "QMT real-open duplicate-submit guards",
        "A prior execution row whose signal_date equals the current D-close signal_date must not block",
        "`strategy_signals` is strategy intent only",
        "`strategy_orders` is submit attempts only",
        "`strategy_fills` is fill facts only",
        "must not require order_id, broker_order_id, fill_quantity, fill_price, commission, or fill_time to be written back into `strategy_signals`",
        "must not open a new QMT/xtquant connection on every dashboard refresh",
        "below the configured free-space guard",
        "skip non-trading days before connecting to QMT",
        "submit-only DB path",
        "`--submit-pending-only`",
        "must not call `--simulate-daily`",
        "must not invoke strategy hooks",
        "Unassigned or `default` live broker fills must remain default/manual account facts",
        "must not be dispatched to managed strategy `on_fill` hooks",
        "legacy JSONL is migration input only",
        "actual simulated/broker fill price",
        "marketability is checked against open/close but fill_price is the submitted limit price",
        "quant.runtime.execution_simulator",
    ]

    missing = [fragment for fragment in expected_fragments if fragment not in text]

    assert missing == []


def test_trading_invariants_describe_live_backtest_equivalence_boundary():
    text = (REPO_ROOT / "quant/features/trading/docs/trading-invariants.md").read_text(
        encoding="utf-8"
    )
    expected_fragments = [
        "### Live/Backtest Equivalence",
        "Live and backtest must share strategy signal generation",
        "Allowed live-only differences are DB-backed state bridging and QMT real order submission/fill reports",
        "QMT fill price, quantity, commission, rejection, partial fill, and broker order IDs are external facts",
        "No other strategy lifecycle, risk gate, signal filtering, cost-budget, cash attribution, or dashboard status semantics may diverge",
    ]

    missing = [fragment for fragment in expected_fragments if fragment not in text]

    assert missing == []


def test_trading_invariants_describe_dashboard_read_model_boundary():
    text = (REPO_ROOT / "quant/features/trading/docs/trading-invariants.md").read_text(
        encoding="utf-8"
    )
    expected_fragments = [
        "### Strategy Dashboard Read Model",
        "separate fact collection from read-model projection",
        "quant.features.trading.dashboard_projection",
        "projected submit dates, signal close prices, and order-date open prices",
        "visible-signal, pending-action",
        "order-display, fill-display, commission, slippage",
        "display_status=no_fill",
        "Projection functions are pure",
        "pending-action, visible-signal, order-display, fill-display, commission, and slippage DTOs",
        "without reading files, connecting brokers, mutating DB state, or importing infrastructure adapters",
        "current execution state separate from EOD equity curve",
        "T13-32",
    ]

    missing = [fragment for fragment in expected_fragments if fragment not in text]

    assert missing == []
