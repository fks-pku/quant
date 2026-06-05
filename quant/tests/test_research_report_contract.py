from html.parser import HTMLParser

from quant.infrastructure.research.asset_paths import report_id_for_result
from quant.infrastructure.research.reporting import (
    build_research_full_report_html,
    build_research_stage_report_html,
)

REQUIRED_TOP_LEVEL_SECTIONS = [
    "1. 本阶段结论",
    "2. 快研究证据",
    "3. 报告导航",
]

REQUIRED_TEMPLATE_MARKERS = [
    'class="badge',
    'class="formula"',
    "fast_research_report.html",
    "strict_backtest_report.html",
    "walkforward_audit_report.html",
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


def test_report_id_ignores_none_run_id():
    data = {"run_id": None, "log": []}
    assert report_id_for_result(data, []) == "research_pipeline"


def test_execution_cost_bps_diagnostics_weighted_and_median():
    from quant.api.research_bp import _execution_cost_bps_diagnostics

    result = _execution_cost_bps_diagnostics(
        [
            {"notional": 100, "slippage_bps": 10, "impact_bps": 5},
            {"notional": 300, "slippage_bps": 20, "impact_bps": 1},
        ]
    )

    assert result["observations"] == 2
    assert result["weighted_effective_bps"] == 19.5
    assert result["median_effective_bps"] == 18.0
    assert result["weighted_slippage_bps"] == 17.5
    assert result["median_impact_bps"] == 3.0


def test_full_report_combines_all_stage_reports_with_metric_checklist():
    result = {
        "run_id": "full_contract",
        "backtested": 1,
        "walkforward_passed": 0,
        "saved_at": "2026-05-24T00:00:00+00:00",
    }
    rows = [
        {
            "title": "Full Contract",
            "strategy_id": "full_contract_strategy",
            "status": "rejected",
            "stage": "go_no_go",
            "decision_reason": "strict and capacity failed",
            "metrics": {
                "rank_ic": 0.031,
                "rank_ic_ir": 0.35,
                "rank_ic_tstat": 2.3,
                "hit_rate": 0.56,
                "strict_backtest": {
                    "initial_cash": 500000,
                    "benchmark": {
                        "symbol": "000300",
                        "benchmark_yearly_returns": {"2025": 0.06},
                    },
                    "metrics": {
                        "sharpe": 0.62,
                        "cagr": 0.07,
                        "max_drawdown_pct": -0.24,
                        "calmar_ratio": 0.29,
                        "profit_factor": 1.21,
                        "total_trades": 64,
                    },
                    "equity_curve": {
                        "strategy": [
                            {"date": "2024-12-31", "value": 500000},
                            {"date": "2025-01-31", "value": 510000},
                            {"date": "2025-12-31", "value": 580000},
                        ],
                        "benchmark": [
                            {"date": "2024-12-31", "value": 500000},
                            {"date": "2025-01-31", "value": 495000},
                            {"date": "2025-12-31", "value": 530000},
                        ],
                    },
                    "yearly_returns": {"2025": 0.16},
                    "constraints": {
                        "execution_cost_model": {
                            "enabled": True,
                            "name": "small_cap_realistic",
                            "max_participation_rate": 0.01,
                        },
                        "t_plus_1": True,
                        "cn_lot_size": 100,
                    },
                    "capacity": {"max_adv_participation": 0.04},
                    "execution_cost_bps": {
                        "weighted_effective_bps": 27.43756599176068,
                        "median_effective_bps": 32.195233221989454,
                    },
                },
                "walkforward": {
                    "verdict": "fail",
                    "aggregate_oos_sharpe": 0.91,
                    "worst_oos_sharpe": 0.18,
                    "pct_profitable_splits": 0.60,
                    "capacity_ok": False,
                },
                "research_stage_conclusions": {
                    "fast_research": {"label": "Fast", "verdict": "pass", "conclusion": "fast pass"},
                    "strict_backtest": {"label": "Strict", "verdict": "fail", "conclusion": "strict fail"},
                    "walkforward_strict_audit": {"label": "WF", "verdict": "fail", "conclusion": "wf fail"},
                    "final_decision": {"label": "Final", "verdict": "rejected", "conclusion": "reject"},
                },
            },
            "evidence": {"strategy_spec": {"strategy_id": "full_contract_strategy", "universe": ["000300"]}},
        }
    ]

    html = build_research_full_report_html(result, rows)

    _assert_clean_html(html)
    assert "End-to-End Research Report" in html
    assert _headings(html, "h2") == [
        "1. Final Decision",
        "2. Metric Checklist",
        "3. Strategy Logic And Core Evidence",
        "4. Key Risks",
        "5. Appendix",
        "6. TODO：上线前还需要做什么",
    ]
    assert "Metric Checklist" in html
    assert "Strategy Logic And Core Evidence" in html
    assert "策略逻辑" in html
    assert "止盈止损逻辑" in html
    assert "Key Risks" in html
    assert "Appendix" in html
    assert "上线前还需要做什么" in html
    assert "Parameter Sensitivity" not in html
    assert "参数敏感性" not in html
    assert "A. Fast research input and signal diagnostics" in html
    assert "B. Strict backtest full diagnostics" in html
    assert "C. Walk-forward audit evidence" in html
    assert "Evidence Map" not in html
    assert 'class="audit-details"' in html
    assert "full_research_report.html" in html
    assert "fast_research_report.html" in html
    assert "strict_backtest_report.html" in html
    assert "walkforward_audit_report.html" in html
    assert '<figure class="equity-chart">' in html
    assert '<path class="strategy-line"' in html
    assert '<path class="benchmark-line"' in html
    assert '<figure class="return-calendar-chart">' in html
    assert '<details class="return-cell positive">' in html
    assert "Backtest Configuration" in html
    assert "A 股示例默认 500000 CNY" not in html
    assert "研究默认由 config/research.yaml 的 default_initial_cash 控制" in html
    assert "<td>有效成本 BPS</td><td>weighted=27.4376 bps; median=32.1952 bps</td>" in html
    assert "Data Quality Audit" in html
    assert "Drawdown Episodes" in html
    assert "Cost Decomposition" in html
    assert "<td>max_adv_participation</td><td>4.00%</td><td>&lt;=5.00% ADV</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>total_trades</td><td>64</td><td>&gt;50</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>cagr_drawdown_tier</td><td>CAGR=7.00%; MaxDD=24.00%</td><td>CAGR 5.00%-10.00% requires MaxDD &lt;=15.00%</td><td><span class=\"badge fail\">fail</span></td>" in html


def test_reports_do_not_render_parameter_sensitivity_by_default():
    result = {"run_id": "sensitivity_contract", "backtested": 1, "walkforward_passed": 0}
    rows = [
        {
            "title": "Sensitivity Contract",
            "strategy_id": "sensitivity_contract_strategy",
            "status": "needs_walkforward_validation",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "sharpe": 1.12,
                        "cagr": 0.158,
                        "max_drawdown_pct": -0.214,
                        "total_trades": 81,
                    },
                    "capacity": {"max_adv_participation": 0.032},
                },
                "parameter_sensitivity": {
                    "status": "warn",
                    "method": "Local one-factor and scenario sweep around locked parameters.",
                    "base_params": {"max_positions": 3, "risk_stop_pct": -0.05},
                    "selected_params": {"max_positions": 3, "risk_stop_pct": -0.05},
                    "best_params": {"max_positions": 5, "risk_stop_pct": -0.08},
                    "tested_count": 3,
                    "pass_count": 2,
                    "max_degradation_pct": 37.5,
                    "stability_note": "Best result is near but not centered in the tested plateau.",
                    "rows": [
                        {
                            "name": "base",
                            "parameters": {"max_positions": 3, "risk_stop_pct": -0.05},
                            "cagr": 0.158,
                            "max_drawdown_pct": -0.214,
                            "sharpe": 1.12,
                            "max_adv_participation": 0.032,
                            "verdict": "pass",
                        },
                        {
                            "name": "wider_stop",
                            "parameters": {"max_positions": 3, "risk_stop_pct": -0.08},
                            "cagr": 0.131,
                            "max_drawdown_pct": -0.263,
                            "sharpe": 0.91,
                            "max_adv_participation": 0.035,
                            "verdict": "warn",
                        },
                    ],
                },
            },
            "evidence": {"strategy_spec": {"strategy_id": "sensitivity_contract_strategy", "universe": ["000300"]}},
        }
    ]

    full_html = build_research_full_report_html(result, rows)
    strict_html = build_research_stage_report_html("strict_backtest", result, rows)

    assert "Parameter Sensitivity" not in full_html
    assert "参数敏感性" not in full_html
    assert "参数敏感性" not in strict_html
    assert "稳健性审计，不作为全样本参数寻优通过证据" not in full_html
    assert "tested_count</td><td>3" not in full_html
    assert "wider_stop" not in full_html
    assert "risk_stop_pct" not in full_html


def test_full_report_renders_strategy_parameter_list_with_explanations():
    result = {"run_id": "parameter_contract", "backtested": 1, "walkforward_passed": 0}
    rows = [
        {
            "title": "Parameter Contract",
            "strategy_id": "parameter_contract_strategy",
            "status": "needs_walkforward_validation",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "sharpe": 0.88,
                        "cagr": 0.12,
                        "max_drawdown_pct": -0.18,
                        "total_trades": 72,
                    },
                    "capacity": {"max_adv_participation": 0.021},
                }
            },
            "evidence": {
                "strategy_spec": {
                    "strategy_id": "parameter_contract_strategy",
                    "signal_formula_key": "parameter_contract_strategy",
                    "universe": ["000300"],
                    "lookback_days": 20,
                    "horizon_days": 5,
                    "execution_lag_days": 1,
                    "rebalance_frequency": "weekly",
                    "parameters": {"max_positions": 3, "empty_months": [1, 4], "target_exposure": 1.0},
                    "parameter_explanations": {
                        "max_positions": "最多同时持有的股票数量。",
                        "empty_months": "这些月份保持空仓以降低季节性风险。",
                    },
                }
            },
        }
    ]

    html = build_research_full_report_html(result, rows)

    assert "策略参数列表及解释" in html
    assert "<td>max_positions</td><td>3</td><td>最多同时持有的股票数量。</td><td>StrategySpec.parameters</td>" in html
    assert "<td>empty_months</td><td>1, 4</td><td>这些月份保持空仓以降低季节性风险。</td><td>StrategySpec.parameters</td>" in html
    assert "<td>lookback_days</td><td>20</td><td>历史观察窗口" in html


def test_full_report_explains_strategy_specific_risk_exit_package():
    result = {"run_id": "risk_exit_contract", "backtested": 1, "walkforward_passed": 0}
    rows = [
        {
            "title": "Risk Exit Contract",
            "strategy_id": "risk_exit_contract_strategy",
            "status": "needs_walkforward_validation",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "sharpe": 0.94,
                        "cagr": 0.13,
                        "max_drawdown_pct": -0.19,
                        "total_trades": 88,
                    },
                    "capacity": {"max_adv_participation": 0.028},
                }
            },
            "evidence": {
                "strategy_spec": {
                    "strategy_id": "xueqiu_small_cap_financial_filter",
                    "strategy_type": "small_cap_size_rotation",
                    "signal_formula_key": "xueqiu_small_cap_financial_filter",
                    "universe": ["000001", "000002"],
                    "parameters": {
                        "enable_risk_exit": True,
                        "risk_exit": {
                            "enabled": True,
                            "stop_loss_pct": 0.12,
                            "min_stop_loss_pct": 0.08,
                            "max_stop_loss_pct": 0.18,
                            "stop_volatility_multiplier": 3.0,
                            "take_profit_pct": 0.25,
                            "trailing_stop_pct": 0.10,
                            "trailing_volatility_multiplier": 2.5,
                            "max_trailing_stop_pct": 0.22,
                            "hard_take_profit_pct": 0.0,
                            "max_holding_days": 45,
                            "min_time_stop_return": 0.02,
                        },
                    },
                    "strategy_logic": {
                        "exit_rule": "每日先检查 ST、停牌、退市、价格和流动性风险；risk_exit.enabled=true 时叠加止损、移动止盈和时间止损。",
                    },
                }
            },
        }
    ]

    html = build_research_full_report_html(result, rows)

    assert "止盈止损逻辑" in html
    assert "策略特定风险退出包" in html
    assert "baseline_no_risk_exit" not in html
    assert "risk_exit_enabled" not in html
    assert "亏损退出" in html
    assert "12.00%" in html
    assert "8.00%-18.00%" in html
    assert "盈利保护" in html
    assert "25.00%" in html
    assert "10.00%" in html
    assert "时间止损" in html
    assert "45" in html
    assert "2.00%" in html
    assert "状态/流动性退出" in html
    assert "ST、停牌、退市、价格和流动性风险" in html


def test_xueqiu_report_spec_preserves_default_enabled_risk_exit_thresholds():
    from quant.scripts.run_xueqiu_small_cap_financial_filter_strict_backtest import (
        _risk_exit_scenarios,
        _strategy_spec,
    )

    scenario = _risk_exit_scenarios(
        [
            {
                "name": "top3_source_month_plus_shenzhen_stop",
                "max_positions": 3,
                "min_positions": 3,
                "target_exposure": 1.0,
                "empty_months": [1, 4],
                "risk_index_symbol": "399001",
            }
        ]
    )[0]

    spec = _strategy_spec(scenario, 100)
    risk_exit = spec["parameters"]["risk_exit"]

    assert scenario["risk_exit_label"] == "risk_exit_enabled"
    assert spec["parameters"]["excluded_board_prefixes"] == ["300", "301", "688", "689"]
    assert "excluding ChiNext 300/301 and STAR 688/689 stocks" in spec["universe"]
    assert risk_exit["enabled"] is True
    assert risk_exit["stop_loss_pct"] == 0.12
    assert risk_exit["min_stop_loss_pct"] == 0.08
    assert risk_exit["max_stop_loss_pct"] == 0.18
    assert risk_exit["take_profit_pct"] == 0.25
    assert risk_exit["trailing_stop_pct"] == 0.10
    assert risk_exit["max_holding_days"] == 45
    assert risk_exit["min_time_stop_return"] == 0.02


def test_full_report_marks_missing_fast_research_metrics_as_failures():
    result = {
        "run_id": "strict_only_contract",
        "backtested": 1,
        "walkforward_passed": 0,
        "saved_at": "2026-05-24T00:00:00+00:00",
    }
    rows = [
        {
            "title": "Strict Only Contract",
            "strategy_id": "strict_only_strategy",
            "status": "rejected",
            "stage": "go_no_go",
            "decision_reason": "strict failed",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "sharpe": -1.04,
                        "cagr": -0.3064,
                        "max_drawdown_pct": -0.9819,
                        "calmar_ratio": -0.312,
                        "profit_factor": 0.61,
                        "total_trades": 1235,
                    }
                },
                "walkforward": {
                    "aggregate_oos_sharpe": -1.47,
                    "worst_oos_sharpe": -6.16,
                    "pct_profitable_splits": 0.31,
                    "capacity_ok": False,
                    "splits": [
                        {
                            "split": 1,
                            "train_start": "2024-01-01",
                            "train_end": "2024-06-30",
                            "test_start": "2024-07-01",
                            "test_end": "2024-07-31",
                            "oos_sharpe": -0.9,
                            "verdict": "fail",
                        }
                    ],
                },
            },
            "evidence": {"strategy_spec": {"strategy_id": "strict_only_strategy", "universe": ["000300"]}},
        }
    ]

    html = build_research_full_report_html(result, rows)

    _assert_clean_html(html)
    assert "Rank IC=n/a" not in html
    assert "not_recorded" not in html
    assert "<td>max_adv_participation</td><td>missing</td><td>&lt;=5.00% ADV</td><td><span class=\"badge fail\">fail</span></td>" in html
    assert "<td>cagr_drawdown_tier</td><td>CAGR=-30.64%; MaxDD=98.19%</td><td>CAGR &gt;=5.00%</td><td><span class=\"badge fail\">fail</span></td>" in html
    assert "fast research admission</td><td>missing" in html
    assert "HFQ signal validation</td><td>missing" in html
    assert "vectorized portfolio diagnostics</td><td>missing" in html
    assert "PnL attribution bridge</td><td>missing" in html
    assert (
        "<td>1</td><td>2024-01-01 - 2024-06-30</td><td>2024-07-01 - 2024-07-31</td>"
        "<td>frozen parameters</td><td>-0.9000</td><td>fail</td>"
    ) in html


def test_full_report_marks_etf_timing_fast_research_scope_as_not_applicable():
    result = {
        "run_id": "etf_timing_contract",
        "backtested": 1,
        "walkforward_passed": 0,
        "saved_at": "2026-05-24T00:00:00+00:00",
    }
    rows = [
        {
            "title": "ETF Timing Contract",
            "strategy_id": "ashare_gold_equity_barbell_timing",
            "status": "rejected",
            "stage": "go_no_go",
            "decision_reason": "walk-forward failed",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "sharpe": 1.18,
                        "cagr": 0.1424,
                        "max_drawdown_pct": -0.1575,
                        "calmar_ratio": 0.904,
                        "profit_factor": 7.14,
                        "total_trades": 204,
                    },
                    "capacity": {"max_adv_participation": 0.02},
                    "data_quality": {
                        "survivorship_audit": {
                            "kind": "etf_metadata_survivorship_audit",
                            "material": True,
                            "reason": "ETF metadata fixture gap",
                            "etf_bar_symbols": 221,
                            "fund_meta_etf_symbols": 1497,
                            "bar_symbols_missing_fund_meta": 13,
                            "fund_meta_delisted_symbols": 0,
                            "universe_registry_version": "audited_stable_etf_registry_v1",
                            "registered_universe_symbol_count": 6,
                            "registered_universe_symbols_with_bars": 6,
                            "registered_universe_missing_bar_count": 0,
                            "bar_symbols_missing_fund_meta_sample": [{"symbol": "160706"}],
                        }
                    },
                },
                "walkforward": {
                    "aggregate_oos_sharpe": 0.665,
                    "worst_oos_sharpe": -3.31,
                    "pct_profitable_splits": 0.615,
                    "capacity_ok": False,
                },
            },
            "evidence": {
                "source": "local_strategy",
                "strategy_spec": {
                    "strategy_id": "ashare_gold_equity_barbell_timing",
                    "strategy_type": "etf_momentum_rotation",
                    "signal_formula_key": "etf_barbell_timing",
                    "universe": ["510050", "510300", "159915", "159949", "510880", "518880"],
                    "pit_universe_enabled": True,
                    "risk_category_symbols": {"csi300": ["510300"], "sse50": ["510050"]},
                    "defensive_category_symbols": {"gold": ["518880"]},
                    "universe_selection_policy": "audited_stable_etf_registry",
                    "universe_start": "2016-01-01",
                    "universe_end": "2025-12-31",
                    "universe_min_history_days_as_of": 0,
                    "universe_max_symbols_per_category": 0,
                    "registered_universe_counts": {
                        "registered_symbol_count": 6,
                        "active_symbol_count": 6,
                        "missing_data_count": 0,
                    },
                },
            },
        }
    ]

    html = build_research_full_report_html(result, rows)

    _assert_clean_html(html)
    assert "not_recorded" not in html
    assert "fast_signal_validation_scope" not in html
    assert "ETF timing/rotation" in html
    assert "需要重跑 fast/full research" not in html
    assert "<td>rank_ic</td>" not in html
    assert "HFQ signal validation</td><td>n/a" in html
    assert "vectorized portfolio diagnostics</td><td>n/a" in html
    assert "PnL attribution bridge</td><td>n/a" in html
    assert "已审计稳定 ETF 注册池" in html
    assert "新增类别必须人工审计注册" in html
    assert "注册池 active=6/registered=6/missing_data=0" in html
    assert "每类只保留起点主代表" not in html
    assert "etf_metadata_survivorship_audit" in html
    assert "bar_symbols_missing_fund_meta</td><td>13" in html
    assert "registered_universe_symbol_count</td><td>6" in html
    assert "registered_universe_missing_bar_count</td><td>0" in html
    assert 'class="logic-plain"' in html
    assert "白话版：" in html
    assert "这套策略先把仓位思路分成两部分" in html
    assert "顺风期组合同时拿权益 ETF 和黄金 ETF" in html
    assert "<td>max_adv_participation</td><td>2.00%</td><td>&lt;=5.00% ADV</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>cagr_drawdown_tier</td><td>CAGR=14.24%; MaxDD=15.75%</td><td>CAGR 10.00%-15.00% requires MaxDD &lt;=25.00%</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>worst_oos_sharpe</td><td>-3.3100</td><td>&gt;=0.3000</td><td><span class=\"badge fail\">fail</span></td>" in html


def test_full_report_preserves_strict_evidence_when_walkforward_stage_rerenders():
    result = {
        "run_id": "walkforward_contract",
        "backtested": 0,
        "walkforward_passed": 0,
        "saved_at": "2026-05-24T00:00:00+00:00",
    }
    rows = [
        {
            "title": "Walkforward Contract",
            "strategy_id": "walkforward_contract_strategy",
            "status": "rejected",
            "stage": "go_no_go",
            "decision_reason": "walk-forward failed",
            "metrics": {
                "strict_backtest": {
                    "metrics": {
                        "sharpe": 1.18,
                        "cagr": 0.142,
                        "max_drawdown_pct": -0.158,
                        "calmar_ratio": 0.90,
                        "profit_factor": 7.15,
                        "total_trades": 204,
                    },
                    "capacity": {"max_adv_participation": 0.02},
                },
                "walkforward": {
                    "verdict": "fail",
                    "aggregate_oos_sharpe": 0.6654,
                    "worst_oos_sharpe": -3.3176,
                    "pct_profitable_splits": 0.6154,
                    "capacity_ok": False,
                },
            },
            "evidence": {"strategy_spec": {"strategy_id": "walkforward_contract_strategy", "universe": ["510300"]}},
        }
    ]

    html = build_research_full_report_html(result, rows)

    assert "<td>max_adv_participation</td><td>2.00%</td><td>&lt;=5.00% ADV</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>total_trades</td><td>204</td><td>&gt;50</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>cagr_drawdown_tier</td><td>CAGR=14.20%; MaxDD=15.80%</td><td>CAGR 10.00%-15.00% requires MaxDD &lt;=25.00%</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "strict_sharpe</td><td>not_recorded" not in html
    assert "Sharpe=1.18" in html


def test_walkforward_report_shows_effective_split_denominator_and_no_trade_exclusions():
    result = {
        "run_id": "walkforward_no_trade_contract",
        "walkforward_passed": 0,
        "saved_at": "2026-05-24T00:00:00+00:00",
    }
    rows = [
        {
            "title": "Walkforward No Trade Contract",
            "strategy_id": "walkforward_no_trade_strategy",
            "status": "rejected",
            "stage": "go_no_go",
            "decision_reason": "walk-forward failed",
            "metrics": {
                "walkforward": {
                    "verdict": "fail",
                    "aggregate_oos_sharpe": 0.7,
                    "worst_oos_sharpe": 0.4,
                    "pct_profitable_splits": 1.0,
                    "capacity_ok": True,
                    "total_splits": 3,
                    "evaluated_splits": 2,
                    "no_trade_splits": 1,
                    "n_splits": 2,
                    "splits": [
                        {
                            "split": 1,
                            "train_start": "2024-01-01",
                            "train_end": "2024-06-30",
                            "test_start": "2024-07-01",
                            "test_end": "2024-07-31",
                            "oos_sharpe": -9.0,
                            "trade_count": 0,
                            "has_trades": False,
                        },
                        {
                            "split": 2,
                            "train_start": "2024-02-01",
                            "train_end": "2024-07-31",
                            "test_start": "2024-08-01",
                            "test_end": "2024-08-31",
                            "oos_sharpe": 0.7,
                            "trade_count": 3,
                            "has_trades": True,
                        },
                    ],
                },
            },
            "evidence": {"strategy_spec": {"strategy_id": "walkforward_no_trade_strategy", "universe": ["510300"]}},
        }
    ]

    html = build_research_stage_report_html("walkforward_strict_audit", result, rows)

    assert "<td>total_splits</td><td>3</td><td>&gt;0</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>evaluated_splits</td><td>2</td><td>&gt;0</td><td><span class=\"badge pass\">pass</span></td>" in html
    assert "<td>no_trade_splits</td><td>1</td><td>excluded from OOS stats</td><td><span class=\"badge warn\">excluded</span></td>" in html
    assert "aggregate/worst/profitable/DSR 只统计这些区间" in html
    assert "<td>1</td><td>2024-01-01 - 2024-06-30</td><td>2024-07-01 - 2024-07-31</td><td>frozen parameters</td><td>n/a (no trades)</td><td>0</td><td>excluded_no_trade</td>" in html
    assert "-9.0000</td><td>0</td><td>excluded_no_trade" not in html


def test_small_cap_strict_grid_best_respects_drawdown_constraint_before_return():
    from quant.scripts.run_ashare_small_cap_pure_baseline_strict_backtest import _select_best

    rows = [
        {"scenario": "high_return_high_drawdown", "cagr": 0.14, "max_drawdown_pct": -0.54, "sharpe": 0.70},
        {"scenario": "return_target_but_drawdown_breach", "cagr": 0.103, "max_drawdown_pct": -0.305, "sharpe": 0.73},
        {"scenario": "drawdown_controlled_goal_candidate", "cagr": 0.1001, "max_drawdown_pct": -0.249, "sharpe": 0.72, "total_trades": 100},
        {"scenario": "drawdown_controlled_low_sharpe", "cagr": 0.06, "max_drawdown_pct": -0.25, "sharpe": 0.50},
    ]

    assert _select_best(rows)["scenario"] == "drawdown_controlled_goal_candidate"


def test_small_cap_strict_grid_uses_current_cagr_drawdown_tiers():
    from quant.scripts.run_ashare_small_cap_pure_baseline_strict_backtest import _meets_goal, _select_best

    rows = [
        {"scenario": "old_30pct_drawdown_candidate", "cagr": 0.11, "max_drawdown_pct": -0.28, "sharpe": 0.90, "total_trades": 100},
        {"scenario": "tier_pass_candidate", "cagr": 0.08, "max_drawdown_pct": -0.14, "sharpe": 0.70, "total_trades": 100},
    ]

    assert _meets_goal(rows[0]) is False
    assert _meets_goal(rows[1]) is True
    assert _select_best(rows)["scenario"] == "tier_pass_candidate"


def test_small_cap_strict_grid_keeps_scenario_symbol_scope_isolated():
    from quant.scripts.run_ashare_small_cap_pure_baseline_strict_backtest import _scenario_symbols

    stock_symbols = ["600001", "600002"]
    pure_scenario = {"market_timing_symbol": "", "broad_index_symbols": []}
    blend_scenario = {"market_timing_symbol": "", "broad_index_symbols": ["510300", "159915"]}

    assert _scenario_symbols(stock_symbols, pure_scenario) == ["600001", "600002"]
    assert _scenario_symbols(stock_symbols, blend_scenario) == ["600001", "600002", "510300", "159915"]


def test_gold_equity_barbell_report_uses_specific_strategy_logic():
    from quant.scripts.run_ashare_gold_equity_barbell_timing_strict_backtest import _hypothesis_row

    best = {
        "scenario": "monthly_126d_200ma_half_equity_half_gold",
        "symbols": ["000300", "510300", "518880"],
        "parameters": {
            "timing_symbol": "000300",
            "momentum_lookback": 126,
            "momentum_skip": 1,
            "trend_window": 200,
            "volatility_window": 20,
            "liquidity_window": 20,
            "min_avg_turnover": 20_000_000.0,
            "target_exposure": 0.98,
            "risk_leg_weight": 0.5,
            "holding_days": 20,
            "require_pit_size": True,
        },
        "risk_category_symbols": {"csi300": ["510300"]},
        "defensive_category_symbols": {"gold": ["518880"]},
        "timing_symbol": "000300",
        "registered_universe_counts": {"registered_symbol_count": 2, "active_symbol_count": 2, "missing_data_count": 0},
        "universe_registry_version": "audited_stable_etf_registry_v1",
    }
    strict_report = {
        "period": "2016-01-01 to 2025-12-31",
        "metrics": {"cagr": 0.12, "max_drawdown_pct": -0.15, "sharpe": 0.98},
        "constraints": {"t_plus_1": True, "cn_lot_size": 100},
        "diagnostics": {},
    }

    row = _hypothesis_row(best, strict_report, [best])
    html = build_research_full_report_html({"run_id": "gold_logic"}, [row])

    assert "StrategySpec declared signal" not in html
    assert "risk-on" in html
    assert "gold ETF" in html
    assert "risk_leg_weight" in html


def test_strict_report_includes_strategy_execution_logic_and_signal_explanation():
    result = {
        "run_id": "qixing_execution_logic",
        "backtested": 1,
        "log": [],
    }
    rows = [
        {
            "title": "JoinQuant Qixing daily ETF/LOF momentum",
            "source": "joinquant",
            "source_url": "https://www.joinquant.com/community/post/detailMobile?postId=67252",
            "status": "rejected",
            "stage": "backtest",
            "decision_reason": "fixture",
            "thesis": "ETF/LOF daily momentum rotation.",
            "evidence": {
                "strategy_spec": {
                    "strategy_id": "joinquant_qixing_daily_etf_rotation",
                    "strategy_type": "etf_momentum_rotation",
                    "signal_formula_key": "joinquant_qixing_daily_etf_rotation",
                    "prediction_direction": "higher_is_better",
                    "universe": ["510300", "159915", "511880"],
                    "lookback_days": 24,
                    "horizon_days": 1,
                    "execution_lag_days": 1,
                    "rebalance_frequency": "daily",
                    "required_fields": ["adj_close", "volume", "turnover"],
                    "fallback_symbol": "511880",
                    "strategy_logic": {
                        "core_idea": "在 ETF/LOF 池中选择趋势更强且拟合质量更好的品种。",
                        "universe": "A 股 ETF/LOF 日线池。",
                        "entry_filters": ["成交额过滤", "正动量过滤"],
                        "ranking_rule": "按 24 日加权对数回归年化收益乘以 R² 排序。",
                        "portfolio_construction": "只持有最高分标的，候选不足时切换到 511880。",
                        "rebalance_rule": "每日收盘后计算信号，下一交易日执行。",
                        "exit_rule": "候选为空或触发风控时退出到防守资产。",
                        "risk_budget": "以候选过滤、防守资产和 T+1 执行约束控制风险。",
                    },
                }
            },
            "metrics": {
                "strict_backtest": {
                    "period": "2016-01-01 to 2025-12-31",
                    "metrics": {
                        "sharpe": 0.04,
                        "sortino": 0.06,
                        "cagr": -0.03,
                        "total_return": -0.39,
                        "max_drawdown_pct": -0.77,
                        "calmar_ratio": -0.04,
                        "win_rate": 0.38,
                        "profit_factor": 0.95,
                        "total_trades": 100,
                    },
                    "benchmark": {"symbol": "000300"},
                    "diagnostics": {"total_commission": 1000, "cost_drag_pct": 0.1},
                    "constraints": {
                        "t_plus_1": True,
                        "cn_lot_size": 100,
                        "slippage_bps": 5,
                        "commission": {"CN": {"type": "cn_realistic", "fund_percent": 0.0001}},
                    },
                }
            },
        }
    ]

    html = build_research_stage_report_html("strict_backtest", result, rows)

    assert "策略逻辑说明" not in html
    assert "信号详细说明" in html
    assert "核心假设" in html
    assert "在 ETF/LOF 池中选择趋势更强且拟合质量更好的品种" in html
    assert "策略执行逻辑" in html
    assert "每日运行步骤" in html
    assert "执行约束摘要" in html
    assert "on_after_trading" in html
    assert "T+1" in html
    assert "24 日加权对数回归年化收益乘以 R²" in html
    assert "511880" in html


def test_generated_stage_reports_match_contract():
    fixture = (
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
                "aggregate_oos_sharpe": -9.9,
                "worst_oos_sharpe": -9.9,
                "pct_profitable_splits": 0.0,
            },
            "log": [
                {
                    "phase": "rigor",
                    "verdict": "warn",
                    "title": "contract_check",
                    "reason": "log values should not drive report metrics",
                    "scores": {
                        "aggregate_oos_sharpe": -9.9,
                        "worst_oos_sharpe": -9.9,
                        "pct_profitable_splits": 0.0,
                        "deflated_sharpe_ratio": 0.0,
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
                    "rank_ic_tstat": 1.3,
                    "rank_ic_p_value": 0.19,
                    "fama_macbeth_tstat": 0.8,
                    "fdr_adjusted_p": 0.41,
                    "ic_decay": [(1, 0.008), (5, 0.004)],
                    "hit_rate": 0.54,
                    "long_short_spread": 0.001,
                    "n_observations": 280,
                    "data_start": "2017-01-03",
                    "data_end": "2025-12-31",
                    "research_stage_conclusions": {
                        "fast_research": {
                            "label": "快研究",
                            "verdict": "fail",
                            "conclusion": "fast fixture 结论",
                            "method": "fixture fast",
                        },
                        "strict_backtest": {
                            "label": "严格回测",
                            "verdict": "warn",
                            "conclusion": "strict fixture 结论",
                            "method": "fixture strict",
                        },
                        "walkforward_strict_audit": {
                            "label": "Walk-forward strict audit",
                            "verdict": "warn",
                            "conclusion": "wf fixture 结论",
                            "method": "fixture wf",
                        },
                        "final_decision": {
                            "label": "最终 Go / No-Go",
                            "verdict": "fail",
                            "conclusion": "final fixture 结论",
                            "method": "fixture final",
                        },
                    },
                        "portfolio_diagnostics": {
                            "kind": "top_bucket_long_only",
                            "top_bucket_selection": "top_n",
                            "top_bucket_target_count": 20,
                            "top_bucket_annualized_return": 0.28,
                            "top_bucket_after_cost_sharpe": 0.62,
                        "top_bucket_turnover": 0.34,
                        "top_bucket_hit_rate": 0.54,
                        "top_bucket_after_cost_mean_return": 0.003,
                        "top_bucket_after_cost_annualized_return": 0.19,
                        "top_bucket_after_cost_max_drawdown": -0.21,
                        "top_bucket_after_cost_calmar_ratio": 1.11,
                        "top1_pct_annualized_return": 0.41,
                        "top1_pct_after_cost_sharpe": 0.48,
                        "top1_pct_turnover": 0.71,
                        "top1_pct_hit_rate": 0.52,
                        "top1_pct_after_cost_mean_return": 0.004,
                        "top1_pct_after_cost_annualized_return": 0.25,
                        "top1_pct_after_cost_max_drawdown": -0.32,
                        "top1_pct_after_cost_calmar_ratio": 1.28,
                        "benchmark_symbol": "000300",
                        "benchmark_excess_after_cost_annualized_return": 0.14,
                        "benchmark_excess_after_cost_sharpe": 0.31,
                        "benchmark_excess_after_cost_mean_return": 0.003,
                        "benchmark_excess_after_cost_annualized_return": 0.14,
                        "benchmark_excess_after_cost_max_drawdown": -0.18,
                        "benchmark_excess_after_cost_calmar_ratio": 0.78,
                        "pnl_attribution_bridge": [
                            {
                                "key": "ideal_top20_close_to_close",
                                "label": "理想 top20 close-to-close",
                                "annualized_return": 0.31,
                                "delta_annualized_return": 0.0,
                                "sharpe": 0.88,
                                "delta_sharpe": 0.0,
                                "max_drawdown": -0.22,
                                "turnover": 0.45,
                                "selected_count_mean": 20.0,
                                "selected_count_min": 20,
                                "selected_count_max": 20,
                                "note": "每日信号 top20 等权，下一交易日 close-to-close，不加执行约束。",
                            },
                            {
                                "key": "turnover_cost",
                                "label": "加入估算换手成本",
                                "annualized_return": 0.19,
                                "delta_annualized_return": -0.12,
                                "sharpe": 0.62,
                                "delta_sharpe": -0.26,
                                "max_drawdown": -0.28,
                                "turnover": 0.34,
                                "selected_count_mean": 20.0,
                                "selected_count_min": 19,
                                "selected_count_max": 20,
                                "note": "轻量归因桥，不替代 strict Backtester。",
                            },
                        ],
                    },
                    "walkforward": {
                        "verdict": "warn",
                        "reason": "structured walk-forward diagnostic",
                        "is_viable": False,
                        "capacity_ok": False,
                        "aggregate_oos_sharpe": 0.7,
                        "worst_oos_sharpe": 0.2,
                        "pct_profitable_splits": 0.66,
                        "deflated_sharpe_ratio": None,
                        "n_splits": 1,
                        "thresholds": {
                            "train_window_days": 252,
                            "test_window_days": 63,
                            "step_days": 63,
                            "purge_days": 5,
                            "embargo_days": 21,
                            "min_train_observations": 126,
                            "min_worst_oos_sharpe": 0.3,
                            "min_profitable_splits_pct": 0.5,
                            "min_deflated_sharpe_ratio": 0.95,
                            "max_adv_pct": 0.05,
                        },
                        "splits": [
                            {
                                "split": 1,
                                "train_start": "2020-01-01",
                                "train_end": "2020-06-30",
                                "test_start": "2020-07-01",
                                "test_end": "2020-07-31",
                                "oos_sharpe": 0.7,
                                "max_drawdown": -0.03,
                                "turnover": 0.12,
                                "verdict": "warn",
                            }
                        ],
                    },
                    "strict_backtest": {
                        "period": "2016-01-01 to 2025-12-31",
                        "metrics": {
                            "sharpe": 0.57,
                            "sortino": 0.84,
                            "cagr": 0.11,
                            "total_return": 0.84,
                            "max_drawdown_pct": -0.43,
                            "calmar_ratio": 0.26,
                            "win_rate": 0.49,
                            "profit_factor": 1.11,
                            "payoff_ratio": 1.22,
                            "expectancy": 123.45,
                            "gain_to_pain_ratio": 0.24,
                            "ulcer_index": 0.18,
                            "tail_ratio": 1.35,
                            "recovery_factor": 1.95,
                            "avg_trade_duration_days": 8.4,
                            "total_trades": 100,
                            "round_trip_trades": 50,
                            "winning_trades": 24,
                            "losing_trades": 26,
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
                            "benchmark_cagr": 0.05,
                            "benchmark_total_return": 0.28,
                            "benchmark_sharpe": 0.4,
                            "benchmark_sortino": 0.6,
                            "benchmark_max_drawdown_pct": -0.2,
                            "benchmark_calmar_ratio": 0.25,
                            "benchmark_return": 0.05,
                            "alpha": 0.07,
                            "beta": 1.06,
                            "information_ratio": 0.5,
                            "tracking_error": 0.14,
                            "up_capture": 1.2,
                            "down_capture": 0.8,
                            "benchmark_yearly_returns": {"2024": 0.04, "2025": 0.06},
                        },
                        "diagnostics": {
                            "total_commission": 1000,
                            "total_gross_pnl": 12000,
                            "cost_drag_pct": 8.333333,
                            "volume_limited_trades": 3,
                            "limit_rejected_orders": 1,
                            "t1_rejected_sells": 0,
                            "submission_rejected": 5,
                            "risk_skipped_orders": 6,
                            "discarded_orders": 4,
                            "expired_orders": 1,
                            "rejection_counts": {"insufficient_cash": 2},
                            "final_suspended_holding_nav": 12345.67,
                            "final_suspended_holding_nav_pct_of_final_nav": 0.0213,
                            "frozen_zero_final_nav": 567654.33,
                            "frozen_zero_cagr": 0.12,
                            "final_suspended_symbols": ["600519"],
                        },
                        "turnover": {
                            "gross_traded_value": 900000,
                            "one_way_traded_value": 440000,
                            "annual_gross_turnover": 1.8,
                            "annual_one_way_turnover": 0.88,
                            "avg_daily_traded_value": 12000,
                            "max_daily_traded_value": 85000,
                        },
                        "exposure": {
                            "avg_position_count": 18.6,
                            "min_position_count": 12,
                            "max_position_count": 20,
                            "avg_gross_exposure_pct": 0.94,
                            "avg_cash_pct": 0.06,
                            "max_position_weight": 0.075,
                            "p95_max_position_weight": 0.068,
                        },
                        "capacity": {
                            "executed_orders": 100,
                            "avg_adv_participation": 0.002,
                            "p95_adv_participation": 0.008,
                            "max_adv_participation": 0.017,
                            "p95_volume_participation": 0.009,
                            "max_volume_participation": 0.021,
                            "p95_trade_notional": 45000,
                            "max_trade_notional": 120000,
                            "estimated_capacity_at_1pct_adv_p95": 625000,
                            "estimated_capacity_at_1pct_adv_max": 294117.65,
                            "max_impact_bps": 0.0,
                        },
                        "guard_diagnostics": {
                            "enabled": True,
                            "parameters": {
                                "min_trade_price": 2.0,
                                "min_avg_turnover": 20000.0,
                            },
                            "entry_rejections": {"low_price": 42, "is_st": 8},
                            "exit_triggers": {"suspended": 3},
                        },
                        "drawdown_episodes": [
                            {
                                "start": "2024-02-01",
                                "trough": "2024-04-10",
                                "recovery": "2024-08-01",
                                "drawdown_pct": -0.18,
                                "duration_days": 182,
                            }
                        ],
                        "trade_distribution": {
                            "sell_trades": 50,
                            "avg_pnl": 123.45,
                            "median_pnl": 45.67,
                            "p05_pnl": -880.0,
                            "p95_pnl": 1600.0,
                            "max_win": 4200.0,
                            "max_loss": -2100.0,
                            "avg_return": 0.012,
                            "median_return": 0.004,
                            "avg_duration_days": 8.4,
                            "p95_duration_days": 21.0,
                        },
                        "rolling_stability": {
                            "rolling_1y_sharpe": {
                                "latest": 0.42,
                                "median": 0.55,
                                "min": -0.2,
                                "max": 1.4,
                                "observations": 260,
                            },
                            "rolling_3y_sharpe": {
                                "latest": 0.48,
                                "median": 0.51,
                                "min": 0.1,
                                "max": 0.9,
                                "observations": 20,
                            },
                            "rolling_1y_information_ratio": {
                                "latest": 0.3,
                                "median": 0.4,
                                "min": -0.1,
                                "max": 1.0,
                                "observations": 260,
                            },
                        },
                        "regime_breakdown": {
                            "positive_years": 2,
                            "total_years": 2,
                            "outperform_years": 1,
                            "avg_return_when_benchmark_up": 0.12,
                            "avg_excess_when_benchmark_up": 0.06,
                            "avg_return_when_benchmark_down": -0.04,
                            "avg_excess_when_benchmark_down": 0.02,
                            "best_year": {"year": "2025", "return": 0.16},
                            "worst_year": {"year": "2024", "return": 0.0},
                        },
                        "cost_decomposition": {
                            "gross_pnl_before_explicit_cost": 12000,
                            "net_pnl_after_cost": 80000,
                            "explicit_commission_tax": 1000,
                            "explicit_cost_pct_initial_cash": 0.002,
                            "explicit_cost_pct_gross_pnl": 0.08333333,
                            "slippage_impact_note": "滑点/冲击体现在成交价中；total_commission 只包含显式佣金税费。",
                        },
                        "data_quality": {
                            "survivorship_audit": {
                                "material": True,
                                "reason": "fixture survivorship risk",
                                "daily_basic_symbols": 5717,
                                "ohlc_symbols": 5189,
                                "daily_basic_not_ohlc_symbols": 540,
                                "missing_low_price_symbols_excluding_920": 251,
                                "missing_symbols_below_top20_excluding_920": 197,
                                "dates_with_missing_below_top20_excluding_920": 3389,
                                "sample_missing_symbols": [
                                    {"symbol": "000005", "last_date": "2024-03-05"},
                                    {"symbol": "000018", "last_date": "2020-01-06"},
                                ],
                            }
                        },
                        "equity_curve": {
                            "strategy": [
                                {"date": "2024-12-31", "value": 500000},
                                {"date": "2025-01-03", "value": 510000},
                                {"date": "2025-12-31", "value": 580000},
                            ],
                            "benchmark": [
                                {"date": "2024-12-31", "value": 500000},
                                {"date": "2025-01-03", "value": 498000},
                                {"date": "2025-12-31", "value": 530000},
                            ],
                        },
                        "constraints": {
                            "strategy_max_position_pct": 1.0,
                            "strategy_max_positions": 20,
                            "slippage_bps": 5,
                            "execution_cost_model": {
                                "enabled": True,
                                "name": "small_cap_realistic",
                                "tick_size": 0.01,
                                "half_spread_ticks": 0.5,
                                "max_participation_rate": 0.01,
                                "impact_coefficient": 0.5,
                                "volatility_fallback": 0.03,
                            },
                            "commission": {"CN": "cn_realistic"},
                            "t_plus_1": True,
                            "cn_lot_size": 100,
                            "delisting_risk_guard": {
                                "enabled": True,
                                "min_trade_price": 2.0,
                                "min_avg_turnover": 20000.0,
                                "liquidity_lookback": 20,
                                "max_recent_suspended_days": 0,
                            },
                        },
                        "yearly_returns": {"2024": 0.0, "2025-12-31": 0.16},
                    },
                },
            }
        ],
    )
    fast_html = build_research_stage_report_html("fast_research", *fixture)
    strict_html = build_research_stage_report_html("strict_backtest", *fixture)
    wf_html = build_research_stage_report_html("walkforward_strict_audit", *fixture)

    _assert_clean_html(fast_html)
    _assert_clean_html(strict_html)
    _assert_clean_html(wf_html)
    assert _headings(fast_html, "h2") == REQUIRED_TOP_LEVEL_SECTIONS
    assert _headings(strict_html, "h2") == ["1. 本阶段结论", "2. 严格回测证据", "3. 报告导航"]
    assert _headings(wf_html, "h2") == ["1. 本阶段结论", "2. Walk-forward Audit 证据", "3. 报告导航"]
    for marker in REQUIRED_TEMPLATE_MARKERS:
        assert marker in fast_html
    assert "full_research_report.html" not in fast_html
    assert "full_research_report.html" not in strict_html
    assert "full_research_report.html" not in wf_html
    assert "fast_research_report.html" in strict_html
    assert "strict_backtest_report.html" in fast_html
    assert "walkforward_audit_report.html" in fast_html
    assert "6. 策略回测报告" not in fast_html
    assert "<td>快研究（当前）</td>" in fast_html
    assert "Walk-forward strict audit" in wf_html
    assert "fast fixture 结论" in fast_html
    assert "strict fixture 结论" in strict_html
    assert "wf fixture 结论" in wf_html
    assert "final fixture 结论" not in fast_html
    assert "Calmar Ratio" in strict_html
    assert "final_suspended_holding_nav" in strict_html
    assert "数据完整性审计" in strict_html
    assert "<td>daily_basic_not_ohlc_symbols</td><td>540</td>" in strict_html
    assert "<td>missing_symbols_below_top20_excluding_920</td><td>197</td>" in strict_html
    assert "000005, 000018" in strict_html
    assert "<td>默认目标总仓位</td><td>100.00%</td>" in strict_html
    assert '<figure class="equity-chart">' in strict_html
    assert '<path class="strategy-line"' in strict_html
    assert '<path class="benchmark-line"' in strict_html
    assert "策略期末 580,000.00（指数 116）" in strict_html
    assert "Benchmark 期末 530,000.00（指数 106）" in strict_html
    assert "首日=100 的归一化指数" in strict_html
    assert '<figure class="return-calendar-chart">' in strict_html
    assert '<details class="return-cell positive"><summary class="return-year-summary"><b class="return-year-label">2025</b><strong class="return-year-value">16.00%</strong>' in strict_html
    assert '<div class="return-month-grid">' in strict_html
    assert '<div class="return-month positive"><div class="return-month-head"><span>01月</span><em>策略</em></div><strong>2.00%</strong>' in strict_html
    assert '<div class="return-month positive"><div class="return-month-head"><span>12月</span><em>策略</em></div><strong>13.73%</strong>' in strict_html
    assert "000300 6.00% · 超额 10.00%" in strict_html
    assert "<dt>000300</dt><dd>-0.40%</dd>" in strict_html
    assert "<dt>超额</dt><dd>2.40%</dd>" in strict_html
    assert "small_cap_realistic" in strict_html
    assert "max_participation_rate=1.00%" in strict_html
    assert "换手与持仓暴露" in strict_html
    assert "容量与流动性压力" in strict_html
    assert "退市护栏命中归因" in strict_html
    assert "回撤过程" in strict_html
    assert "交易分布" in strict_html
    assert "滚动稳定性与市场阶段" in strict_html
    assert "成本口径拆分" in strict_html
    assert "<td>Payoff Ratio</td><td>1.2200</td>" in strict_html
    assert "<td>Up / Down Capture</td><td>1.2000 / 0.8000</td>" in strict_html
    assert "<td>annual_gross_turnover</td><td>180.00%</td>" in strict_html
    assert "<td>avg_position_count</td><td>18.6000</td>" in strict_html
    assert "<td>p95_adv_participation</td><td>0.80%</td>" in strict_html
    assert "<td>estimated_capacity_at_1pct_adv_p95</td><td>625,000.00</td>" in strict_html
    assert "<td>entry_rejections_top</td><td>low_price=42; is_st=8</td>" in strict_html
    assert "<td>exit_triggers_top</td><td>suspended=3</td>" in strict_html
    assert "<td>2024-02-01</td><td>2024-04-10</td><td>2024-08-01</td><td>-18.00%</td><td>182</td>" in strict_html
    assert "<td>p95_duration_days</td><td>21.0000</td>" in strict_html
    assert "<td>rolling_1y_sharpe</td><td>latest=0.4200; median=0.5500; min=-0.2000; max=1.4000</td>" in strict_html
    assert "<td>benchmark_down_excess</td><td>2.00%</td>" in strict_html
    assert "<td>gross_pnl_before_explicit_cost</td><td>12,000.00</td>" in strict_html
    assert "<td>rejection_total</td><td>17</td>" in strict_html
    assert "<td>insufficient_cash_rejected_orders</td><td>2</td><td>现金不足拒单</td>" in strict_html
    assert "12,345.67" in strict_html
    assert "<td>退市风险护栏</td><td>启用；最低价格 2.0000；20 日均成交额 &gt;= 20000.0000</td>" in strict_html
    assert "<td>frozen_zero_final_nav</td><td>567,654.33</td>" in strict_html
    assert "<td>Rank IC t-stat</td><td>1.3000</td>" in fast_html
    assert "<td>p-value</td><td>0.1900</td>" in fast_html
    assert "通常有意义/较好水平" in fast_html
    assert "&gt;=0.02 有研究意义；&gt;=0.04 较好；&gt;=0.06 很强" in fast_html
    assert "&gt;50% 方向有效；&gt;=55% 较好；&gt;=60% 很强" in fast_html
    assert "正向 OOS split &gt;50% 有意义；&gt;=60%-70% 较好" in fast_html
    assert "1d=0.0080; 5d=0.0040" in fast_html
    assert "<td>Top 20 long-only</td><td>28.00%</td><td>0.6200</td>" in fast_html
    assert "<td>34.00%</td><td>19.00%</td><td>A 股可交易方向诊断</td>" in fast_html
    assert "PnL 归因桥" in fast_html
    assert "<td>理想 top20 close-to-close</td><td>31.00%</td><td>0.00%</td><td>0.8800</td>" in fast_html
    assert "<td>加入估算换手成本</td><td>19.00%</td><td>-12.00%</td><td>0.6200</td>" in fast_html
    assert "<td>aggregate_oos_sharpe</td><td>0.7000</td>" in wf_html
    assert "通过阈值" in wf_html
    assert "<td>train_window</td><td>252 trading days</td>" in wf_html
    assert "<td>test_window</td><td>63 trading days</td>" in wf_html
    assert "<td>worst_oos_sharpe</td><td>0.2000</td><td>&gt;=0.3000</td><td><span class=\"badge fail\">fail</span></td>" in wf_html
    assert "<td>pct_profitable_splits</td><td>66.00%</td><td>&gt;=50.00%</td><td><span class=\"badge pass\">pass</span></td>" in wf_html
    assert "<td>deflated_sharpe_ratio</td><td>n/a</td><td>&gt;=0.9500；缺失时不触发 DSR 警告</td><td><span class=\"badge warn\">missing</span></td>" in wf_html
    assert "<td>capacity_viability</td><td>未通过</td><td>所有交易可估算成交量且单笔参与率 &lt;=5.00% ADV</td><td><span class=\"badge fail\">fail</span></td>" in wf_html
    assert "<td>0.7000</td><td>-3.00%</td><td>12.00%</td><td>warn</td>" in wf_html
    assert "-9.9000" not in fast_html
    assert "-9.9000" not in wf_html
    for placeholder in ("[strategy_id]", "[Rank IC]", "[000300 coverage]", "[YYYY-MM-DD]"):
        assert placeholder not in fast_html
        assert placeholder not in strict_html
        assert placeholder not in wf_html


def test_signal_oos_validation_uses_structured_walkforward_verdict():
    html = build_research_stage_report_html(
        "fast_research",
        {"run_id": "oos_contract", "rejected": 1},
        [
            {
                "title": "OOS Contract",
                "source": "fixture",
                "source_url": "https://example.test",
                "status": "rejected",
                "metrics": {
                    "rank_ic": -0.05,
                    "rank_ic_ir": -0.3,
                    "fdr_adjusted_p": 0.01,
                    "hit_rate": 0.36,
                    "walkforward": {
                        "verdict": "fail",
                        "aggregate_oos_sharpe": 0.1,
                        "worst_oos_sharpe": -2.0,
                        "pct_profitable_splits": 0.1,
                    },
                },
            }
        ],
    )

    assert "<td>OOS validation</td><td>fail</td>" in html
    assert "正向 OOS split &gt;50% 有意义" in html


def test_fast_report_explains_joinquant_low_price_signal_in_chinese():
    html = build_research_stage_report_html(
        "fast_research",
        {"run_id": "joinquant_low_price_contract", "validated": 1, "validated_passed": 1},
        [
            {
                "title": "JoinQuant Small Cap Low Price",
                "source": "joinquant_community",
                "source_url": "https://www.joinquant.com/community/post/detailMobile?postId=59884",
                "status": "candidate",
                "thesis": "English source text should not be the only hypothesis shown.",
                "evidence": {
                    "published_date": "2026-05-17",
                    "authors": "Codex Quant Research",
                    "strategy_spec": {
                        "strategy_id": "joinquant_small_cap_low_price",
                        "signal_formula_key": "joinquant_small_cap_low_price_factor",
                        "universe": ["000001", "000002"],
                        "lookback_days": 1,
                        "horizon_days": 5,
                        "execution_lag_days": 1,
                        "required_fields": ["close", "market_cap", "turnover"],
                    },
                },
                "metrics": {
                    "rank_ic": 0.04,
                    "rank_ic_ir": 0.23,
                    "fdr_adjusted_p": 0.001,
                    "hit_rate": 0.62,
                },
            }
        ],
    )

    assert "这不是基本面预测模型，而是一个 A 股日频截面风格信号" in html
    assert "2-20 元且具备基本流动性" in html
    assert "低价股票中优先选择市值最小的股票" in html
    assert "strict strategy: signal_i,t = 1 / market_cap_i,t" in html
    assert "持仓触发风险后每日尝试退出" in html
    assert "t+1 由 Backtester 下 MARKET 单" in html
    assert "A 股 T+1、100 股一手、涨跌停、停牌、成交量限制、现金不足、佣金和 5bps 滑点" in html
