from html.parser import HTMLParser

from quant.infrastructure.research.reporting import build_research_stage_report_html

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
                        "aggregate_oos_sharpe": 0.7,
                        "worst_oos_sharpe": 0.2,
                        "pct_profitable_splits": 0.66,
                        "deflated_sharpe_ratio": None,
                        "n_splits": 1,
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
                        "period": "2012-01-01 to 2025-12-31",
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
