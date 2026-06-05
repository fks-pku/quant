from quant.scripts import update_cn_live_data


def test_duckdb_ranges_are_loaded_before_write_storage(monkeypatch, tmp_path):
    events = []

    def fake_load_ranges(db_path, table, date_column):
        assert "storage" not in events
        events.append(f"range:{table}")
        return {}

    def fake_load_latest_adj_factors(db_path, table):
        assert "storage" not in events
        events.append(f"adj:{table}")
        return {}

    class FakeStorage:
        def __init__(self, **kwargs):
            events.append("storage")

        def close(self):
            events.append("close")

    class FakeProvider:
        def __init__(self, storage, min_interval):
            events.append("provider")

        def connect(self):
            events.append("connect")

        def disconnect(self):
            events.append("disconnect")

    monkeypatch.setattr(update_cn_live_data, "_load_ranges", fake_load_ranges)
    monkeypatch.setattr(update_cn_live_data, "_load_latest_adj_factors", fake_load_latest_adj_factors)
    monkeypatch.setattr(update_cn_live_data, "DuckDBStorage", FakeStorage)
    monkeypatch.setattr(update_cn_live_data, "TushareProvider", FakeProvider)
    monkeypatch.setattr(update_cn_live_data, "_preflight_tushare", lambda provider, target_end: events.append("preflight"))

    update_cn_live_data.main(
        [
            "--duckdb-dir",
            str(tmp_path),
            "--end",
            "2024-01-02",
            "--skip-stocks",
            "--skip-indices",
            "--skip-etfs",
            "--skip-daily-basic",
            "--skip-financials",
            "--skip-status",
            "--skip-index-weights",
        ]
    )

    assert events[:6] == [
        "range:daily_cn_ochl",
        "range:daily_cn_ochl",
        "range:daily_cn_ochl",
        "range:cn_fund_nav",
        "adj:daily_cn_ochl",
        "storage",
    ]
    assert events[-2:] == ["disconnect", "close"]
