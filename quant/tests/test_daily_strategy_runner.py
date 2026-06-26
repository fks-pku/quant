from datetime import date

from quant.runtime.daily_strategy_runner import run_daily_snapshots


class FieldRequiredStrategy:
    name = "FieldRequired"

    def __init__(self, symbols, required_fields):
        self._symbols = list(symbols)
        self.required_fields = list(required_fields)
        self.feed_count = 0
        self.after_calls = 0

    @property
    def symbols(self):
        return self._symbols

    def on_data_batch(self, context, data):
        self.feed_count += len(list(data))

    def on_after_trading(self, context, trading_date):
        self.after_calls += 1


def test_daily_snapshot_runner_rejects_missing_required_fields():
    strategy = FieldRequiredStrategy(["600519"], ["pe_ttm"])
    bars = [{"symbol": "600519", "timestamp": date(2026, 6, 22), "close": 10.0}]

    results = run_daily_snapshots([strategy], date(2026, 6, 22), bars, strict=True)

    assert results[0][1].ran is False
    assert results[0][1].missing_fields == ("pe_ttm",)
    assert strategy.feed_count == 0
    assert strategy.after_calls == 0


def test_daily_snapshot_runner_accepts_declared_required_fields():
    strategy = FieldRequiredStrategy(["600519"], ["pe_ttm"])
    bars = [{"symbol": "600519", "timestamp": date(2026, 6, 22), "close": 10.0, "pe_ttm": 9.5}]

    results = run_daily_snapshots([strategy], date(2026, 6, 22), bars, strict=True)

    assert results[0][1].ran is True
    assert results[0][1].missing_fields == ()
    assert strategy.feed_count == 1
    assert strategy.after_calls == 1
