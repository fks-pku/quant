import pandas as pd

from quant.scripts.ingest_tushare_fund_universe import fetch_fund_metadata


def test_fetch_fund_metadata_routes_statuses_to_matching_tushare_interfaces():
    class FakeProvider:
        def __init__(self):
            self.calls = []

        def fetch_fund_basic(self, status):
            self.calls.append(("fund_basic", status))
            return pd.DataFrame(
                [
                    {
                        "ts_code": f"5100{len(self.calls):02d}.SH",
                        "name": "Fund",
                        "fund_type": "股票型",
                        "status": status,
                        "market": "E",
                    }
                ]
            )

        def fetch_etf_basic(self, status):
            self.calls.append(("etf_basic", status))
            return pd.DataFrame(
                [
                    {
                        "ts_code": f"1599{len(self.calls):02d}.SZ",
                        "extname": "ETF",
                        "list_status": status,
                        "status": status,
                        "etf_type": "纯境内",
                    }
                ]
            )

    provider = FakeProvider()

    frame = fetch_fund_metadata(provider, ["L", "D", "I", "P"])

    assert provider.calls == [
        ("fund_basic", "L"),
        ("etf_basic", "L"),
        ("fund_basic", "D"),
        ("etf_basic", "D"),
        ("fund_basic", "I"),
        ("etf_basic", "P"),
    ]
    assert {"L", "D", "I", "P"}.issubset(set(frame["status"]))
