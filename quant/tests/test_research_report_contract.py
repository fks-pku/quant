from html.parser import HTMLParser
from pathlib import Path

from quant.infrastructure.research.reporting import build_full_research_report_html


REPORT_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "infrastructure"
    / "var"
    / "research"
    / "report_templates"
    / "full_research_report_template.html"
)

LEGACY_GOLDEN_REPORT = (
    Path(__file__).resolve().parents[1]
    / "infrastructure"
    / "research"
    / "golden_reports"
    / "full_research_report_golden.html"
)

REQUIRED_TOP_LEVEL_SECTIONS = [
    "1. 结论汇总",
    "2. idea 来源与初筛",
    "3. 信号定义",
    "4. 数据来源及 benchmark 定义",
    "5. 信号验证",
    "6. 策略回测报告",
    "7. purged walk-forward",
    "8. 最终推荐与下一步计划",
]

REQUIRED_DETAIL_SECTIONS = [
    "一句话结论",
    "来源质量与准入评分",
    "初筛风险旗标",
    "信号公式",
    "交易解释",
    "数据质量检查",
    "组合诊断",
    "回测配置",
    "核心绩效",
    "成交与成本诊断",
    "方法设置",
    "结果摘要",
    "Split 明细",
    "推荐理由",
    "下一步计划",
    "产物链接",
]

REQUIRED_TEMPLATE_MARKERS = [
    'class="template-note"',
    'class="grid"',
    'class="metric"',
    'class="formula"',
    'class="decision"',
    'class="decision-mark"',
]


class _HeadingParser(HTMLParser):
    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag
        self.capture = False
        self.current = []
        self.headings = []

    def handle_starttag(self, tag: str, attrs):
        if tag == self.tag:
            self.capture = True
            self.current = []

    def handle_endtag(self, tag: str):
        if tag == self.tag and self.capture:
            text = "".join(self.current).strip()
            if text:
                self.headings.append(" ".join(text.split()))
            self.capture = False

    def handle_data(self, data: str):
        if self.capture:
            self.current.append(data)


def _headings(html: str, tag: str) -> list[str]:
    parser = _HeadingParser(tag)
    parser.feed(html)
    return parser.headings


def _assert_clean_html(html: str):
    assert "???" not in html
    assert "\ufffd" not in html


def test_canonical_full_research_report_template_defines_contract():
    html = REPORT_TEMPLATE.read_text(encoding="utf-8")

    _assert_clean_html(html)
    assert _headings(html, "h2") == REQUIRED_TOP_LEVEL_SECTIONS
    template_h3 = _headings(html, "h3")
    for heading in REQUIRED_DETAIL_SECTIONS:
        assert heading in template_h3
    for marker in REQUIRED_TEMPLATE_MARKERS:
        assert marker in html
    assert "full_research_report.html" in html
    assert "000300" in html
    assert "Calmar Ratio" in html
    assert "Top 1% long-only" in html
    assert "final_suspended_holding_nav" in html


def test_legacy_golden_full_research_report_has_no_encoding_regression():
    html = LEGACY_GOLDEN_REPORT.read_text(encoding="utf-8")

    _assert_clean_html(html)


def test_generated_full_research_report_matches_golden_contract():
    html = build_full_research_report_html(
        {
            "run_id": "contract_check",
            "discovered": 1,
            "evaluated": 1,
            "specified": 1,
            "validated": 1,
            "validated_passed": 0,
            "integrated": 1,
            "backtested": 1,
            "walkforward_passed": 0,
            "rejected": 1,
            "walkforward_detail": {
                "n_splits": 3,
                "top_excess_vs_benchmark_avg_sharpe": 0.7,
                "top_excess_vs_benchmark_pct_positive": 0.66,
            },
            "log": [
                {
                    "phase": "rigor",
                    "verdict": "warn",
                    "title": "contract_check",
                    "reason": "diagnostic only",
                    "scores": {
                        "aggregate_oos_sharpe": 0.7,
                        "worst_oos_sharpe": None,
                        "pct_profitable_splits": 0.66,
                        "deflated_sharpe_ratio": None,
                    },
                }
            ],
        },
        [
            {
                "title": "Contract Check",
                "source": "fixture",
                "source_url": "https://example.test",
                "status": "rejected",
                "stage": "go_no_go",
                "decision_reason": "",
                "thesis": "Fixture signal definition.",
                "evidence": {
                    "published_date": "2026-01-01",
                    "discovery_quality": {"score": 8.5, "matched_terms": ["daily"], "risk_flags": []},
                    "strategy_spec": {
                        "strategy_id": "contract_check",
                        "signal_formula_key": "rolling ridge forecast of 5d HFQ return",
                        "universe": ["CN A-share liquid universe"],
                        "lookback_days": 21,
                        "horizon_days": 5,
                        "execution_lag_days": 1,
                        "required_fields": ["date", "symbol", "adj_close", "volume"],
                    },
                },
                "metrics": {
                    "admission_score": 8.5,
                    "signal_quality_score": 5.0,
                    "research_confidence_score": 6.0,
                    "required_data_fields": ["date", "symbol", "adj_close", "volume"],
                    "validation_tests": ["rank_ic", "fdr_control", "purged_walk_forward"],
                    "rank_ic": 0.008,
                    "rank_ic_ir": 0.05,
                    "fama_macbeth_tstat": 0.8,
                    "fdr_adjusted_p": 0.41,
                    "hit_rate": 0.54,
                    "long_short_spread": 0.001,
                    "n_observations": 280,
                    "data_start": "2017-01-03",
                    "data_end": "2025-12-31",
                    "portfolio_diagnostics": {
                        "kind": "top_bucket_long_only",
                        "top_bucket_annualized_return": 0.28,
                        "top_bucket_hit_rate": 0.54,
                        "top_bucket_after_cost_mean_return": 0.003,
                        "top_bucket_after_cost_max_drawdown": -0.21,
                        "top_bucket_after_cost_calmar_ratio": 1.11,
                        "top1_pct_annualized_return": 0.41,
                        "top1_pct_hit_rate": 0.52,
                        "top1_pct_after_cost_mean_return": 0.004,
                        "top1_pct_after_cost_max_drawdown": -0.32,
                        "top1_pct_after_cost_calmar_ratio": 1.28,
                        "benchmark_symbol": "000300",
                        "benchmark_excess_after_cost_annualized_return": 0.14,
                        "benchmark_excess_after_cost_mean_return": 0.003,
                        "benchmark_excess_after_cost_max_drawdown": -0.18,
                        "benchmark_excess_after_cost_calmar_ratio": 0.78,
                    },
                    "strict_backtest": {
                        "period": "2020-01-01 to 2025-12-31",
                        "metrics": {
                            "sharpe": 0.57,
                            "sortino": 0.84,
                            "cagr": 0.11,
                            "total_return": 0.84,
                            "max_drawdown_pct": -0.43,
                            "calmar_ratio": 0.26,
                            "win_rate": 0.49,
                            "profit_factor": 1.11,
                            "total_trades": 100,
                            "round_trip_trades": 50,
                            "t_stat": 1.2,
                            "p_value": 0.11,
                        },
                        "benchmark": {
                            "symbol": "000300",
                            "coverage_start": "2017-01-03",
                            "coverage_end": "2025-12-31",
                            "rows": 2186,
                            "price_column": "adj_close",
                            "fallback_used": False,
                            "benchmark_return": 0.05,
                            "alpha": 0.07,
                            "beta": 1.06,
                            "information_ratio": 0.5,
                            "tracking_error": 0.14,
                        },
                        "diagnostics": {
                            "total_commission": 1000,
                            "volume_limited_trades": 3,
                            "limit_rejected_orders": 1,
                            "t1_rejected_sells": 0,
                            "final_suspended_holding_nav": 12345.67,
                            "final_suspended_symbols": ["600519"],
                        },
                        "yearly_returns": {"2025-12-31": 0.16},
                    },
                },
            }
        ],
    )

    _assert_clean_html(html)
    assert _headings(html, "h2") == _headings(REPORT_TEMPLATE.read_text(encoding="utf-8"), "h2")
    generated_h3 = _headings(html, "h3")
    for heading in REQUIRED_DETAIL_SECTIONS:
        assert heading in generated_h3
    for marker in REQUIRED_TEMPLATE_MARKERS:
        assert marker in html
    assert "Calmar Ratio" in html
    assert "Top 1% long-only" in html
    assert "final_suspended_holding_nav" in html
    assert "12,345.67" in html
    assert "Rejected strategy archive" in html
    for placeholder in ("[strategy_id]", "[Rank IC]", "[000300 coverage]", "[YYYY-MM-DD]"):
        assert placeholder not in html
