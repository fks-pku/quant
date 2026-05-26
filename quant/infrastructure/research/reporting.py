from __future__ import annotations

import math
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, Iterable, List

from quant.infrastructure.research.asset_paths import FULL_REPORT_HTML, STAGE_REPORT_HTML

_REQUIRED_CHART_STYLE = """
.equity-chart {
  margin: 12px 0 18px;
  padding: 16px;
  border: 1px solid var(--line);
  background: #fff;
}
.equity-chart svg {
  display: block;
  width: 100%;
  height: auto;
  min-height: 280px;
}
.equity-chart-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 18px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--muted);
}
.equity-chart-meta b { color: var(--ink); }
.equity-chart i {
  display: inline-block;
  width: 18px;
  height: 3px;
  margin-right: 6px;
  vertical-align: middle;
}
.legend-strategy { background: #dc2626; }
.legend-benchmark { background: #16a34a; }
.strategy-line {
  fill: none;
  stroke: #dc2626;
  stroke-width: 2.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.benchmark-line {
  fill: none;
  stroke: #16a34a;
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.chart-grid { stroke: #e8ddd0; stroke-width: 1; }
.chart-axis { stroke: #9ca3af; stroke-width: 1.2; }
.chart-label {
  fill: #66727e;
  font-size: 12px;
  font-family: "Cascadia Mono", Consolas, monospace;
}
.return-calendar-chart {
  margin: 12px 0 18px;
  padding: 16px;
  border: 1px solid var(--line);
  background: #fff;
}
.return-calendar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(142px, 1fr));
  gap: 10px;
  align-items: start;
}
.return-cell {
  min-height: 112px;
  padding: 0;
  border: 1px solid var(--line);
  background: #fff;
  overflow: hidden;
}
details.return-cell {
  cursor: pointer;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}
details.return-cell:hover {
  border-color: rgba(15, 23, 42, 0.26);
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.08);
}
details.return-cell[open] {
  grid-column: 1 / -1;
  cursor: default;
}
.return-year-summary {
  display: grid;
  grid-template-columns: minmax(48px, auto) 1fr 24px;
  grid-template-areas:
    "year toggle toggle"
    "value value value"
    "caption meta meta";
  gap: 4px 10px;
  min-height: 112px;
  padding: 12px;
  align-items: center;
  list-style: none;
  cursor: pointer;
}
.return-year-summary::-webkit-details-marker {
  display: none;
}
details.return-cell[open] .return-year-summary {
  grid-template-columns: auto auto 1fr auto 24px;
  grid-template-areas: "year value caption meta toggle";
  min-height: 0;
  border-bottom: 1px solid rgba(102, 114, 126, 0.22);
}
.return-year-summary::after {
  content: "+";
  grid-area: toggle;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1px solid rgba(102, 114, 126, 0.32);
  color: var(--muted);
  font: 700 15px/1 "Cascadia Mono", Consolas, monospace;
  background: rgba(255, 255, 255, 0.65);
}
details.return-cell[open] > .return-year-summary::after {
  content: "-";
}
.return-year-label,
.return-year-summary > b {
  grid-area: year;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
}
.return-year-value,
.return-year-summary > strong {
  grid-area: value;
  display: block;
  font-size: 22px;
  line-height: 1.15;
  white-space: nowrap;
}
.return-year-caption,
.return-year-summary > span {
  grid-area: caption;
  color: var(--muted);
  font-size: 12px;
}
.return-year-meta,
.return-year-summary > small {
  grid-area: meta;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.3;
}
.return-cell.positive { background: #fff1f2; border-color: #fecdd3; }
.return-cell.negative { background: #f0fdf4; border-color: #bbf7d0; }
.return-cell.neutral { background: #f8fafc; }
.return-month-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  padding: 12px;
  background: rgba(248, 250, 252, 0.7);
}
@media (min-width: 1180px) {
  details.return-cell[open] .return-month-grid {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}
@media (max-width: 620px) {
  details.return-cell[open] .return-year-summary {
    grid-template-columns: minmax(48px, auto) 1fr 24px;
    grid-template-areas:
      "year toggle toggle"
      "value value value"
      "caption meta meta";
  }
}
.return-month {
  min-height: 112px;
  padding: 10px;
  border: 1px solid rgba(102, 114, 126, 0.24);
  border-left: 4px solid rgba(102, 114, 126, 0.34);
  background: rgba(255, 255, 255, 0.72);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.return-month-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.return-month-head span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}
.return-month > span {
  display: block;
  color: var(--muted);
  font-size: 11px;
  font-weight: 800;
}
.return-month-head em {
  flex: 0 0 auto;
  padding: 2px 6px;
  color: var(--muted);
  font-size: 10px;
  font-style: normal;
  line-height: 1.2;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(102, 114, 126, 0.2);
}
.return-month strong {
  font-size: 18px;
  line-height: 1.1;
  color: var(--ink);
  white-space: nowrap;
}
.return-month > small {
  display: block;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.return-month dl {
  display: grid;
  gap: 4px;
  margin: 0;
}
.return-month dl div {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.return-month dt,
.return-month dd {
  margin: 0;
  font-size: 11px;
  line-height: 1.25;
  white-space: nowrap;
}
.return-month dt {
  color: var(--muted);
}
.return-month dd {
  overflow: hidden;
  color: #334155;
  font-weight: 700;
  text-align: right;
  text-overflow: ellipsis;
}
.return-month.positive { background: #fff7f7; border-color: #fecdd3; border-left-color: #e11d48; }
.return-month.negative { background: #f4fdf7; border-color: #bbf7d0; border-left-color: #16a34a; }
.return-month.neutral { background: #f8fafc; }
""".strip()


_STAGE_REPORT_META = {
    "fast_research": {
        "label": "快研究",
        "eyebrow": "Fast Research",
        "description": "来源/admission、StrategySpec、HFQ 信号验证、向量化组合诊断。",
    },
    "strict_backtest": {
        "label": "严格回测",
        "eyebrow": "Strict Backtest",
        "description": "项目 Backtester，含 T+1、停牌、涨跌停、手数、佣金、滑点、现金和成交约束。",
    },
    "walkforward_strict_audit": {
        "label": "Walk-forward strict audit",
        "eyebrow": "Walk-forward Strict Audit",
        "description": "滚动 OOS split 重放 strict Backtester，用于最终稳定性审计。",
    },
}

_WALKFORWARD_DEFAULT_THRESHOLDS = {
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
}


def build_research_stage_report_html(
    stage_key: str,
    result: Any,
    hypotheses: Iterable[Dict[str, Any]],
    generated_at: str | None = None,
) -> str:
    if stage_key not in _STAGE_REPORT_META:
        raise ValueError(f"Unknown research report stage: {stage_key}")
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    rows = list(hypotheses or [])
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    meta = _STAGE_REPORT_META[stage_key]
    title = f"{_report_subject(rows)} {meta['label']}报告"
    body = [
        '<section class="hero">',
        f"<p class=\"eyebrow\">{escape(meta['eyebrow'])}</p>",
        f"<h1>{escape(title)}</h1>",
        f"<p>生成时间 {escape(generated)}。本报告只回答 <b>{escape(meta['label'])}</b> 的执行口径、阶段证据和当前结论；其他阶段请打开对应独立报告。</p>",
        "</section>",
        '<section class="panel">',
        "<h2>1. 本阶段结论</h2>",
        _single_stage_conclusion_table(data, rows, stage_key),
        "</section>",
        *_stage_specific_sections(stage_key, data, rows),
        '<section class="panel">',
        "<h2>3. 报告导航</h2>",
        _stage_report_link_table(data, rows, current_stage=stage_key),
        "</section>",
    ]
    return _html_document(title, "\n".join(body))


def build_research_full_report_html(
    result: Any,
    hypotheses: Iterable[Dict[str, Any]],
    generated_at: str | None = None,
) -> str:
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    rows = list(hypotheses or [])
    generated = generated_at or data.get("saved_at") or datetime.now(timezone.utc).isoformat()
    title = f"{_report_subject(rows)} End-to-End Research Report"
    body = [
        '<section class="hero">',
        "<p class=\"eyebrow\">Executive Summary + Audit Appendix</p>",
        f"<h1>{escape(title)}</h1>",
        f"<p>Generated at {escape(str(generated))}. The main report focuses on the current Go / No-Go checklist, strict Backtester evidence, capacity, and key residual risks. Detailed audit tables are folded into appendices.</p>",
        _report_metric_grid(rows, str(generated)),
        "</section>",
        '<section class="panel">',
        "<h2>1. Final Decision</h2>",
        _conclusion_paragraph(data, rows),
        _executive_snapshot_table(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>2. Metric Checklist</h2>",
        _end_to_end_checklist_table(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>3. Strategy Logic And Core Evidence</h2>",
        _strategy_logic_plain_paragraph(data, rows),
        _strategy_logic_summary_table(data, rows),
        "<h3>Equity Curve</h3>",
        _equity_curve_chart(data, rows),
        "<h3>Core Performance</h3>",
        _core_performance_contract_table(data, rows),
        "<h3>Cost And Capacity</h3>",
        _cost_capacity_summary_table(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>4. Key Risks</h2>",
        _key_risk_table(data, rows),
        "</section>",
        '<section class="panel appendix-panel">',
        "<h2>5. Audit Appendix</h2>",
        _details_block(
            "A. Fast research input and signal diagnostics",
            "\n".join(
                [
                    "<h3>Idea Source</h3>",
                    _idea_source_overview_table(rows),
                    "<h3>Admission And Signal Contract</h3>",
                    _source_quality_score_table(data, rows),
                    _strategy_spec_contract_table(rows),
                    _formula_block(rows),
                    "<h3>Signal Validation</h3>",
                    _signal_validation_contract_table(data, rows),
                    "<h3>Portfolio Diagnostics</h3>",
                    _portfolio_diagnostics_contract_table(data, rows),
                    "<h3>PnL Attribution Bridge</h3>",
                    _pnl_attribution_bridge_contract_table(data, rows),
                ]
            ),
        ),
        _details_block(
            "B. Strict backtest full diagnostics",
            "\n".join(
                [
                    "<h3>Strategy Execution Logic</h3>",
                    _strategy_execution_logic_contract(data, rows),
                    "<h3>Yearly / Monthly Return Calendar</h3>",
                    _yearly_return_calendar(data, rows),
                    "<h3>Backtest Configuration</h3>",
                    _backtest_config_contract_table(data, rows),
                    "<h3>Data Quality Audit</h3>",
                    _data_quality_contract_table(data, rows),
                    "<h3>Trade And Cost Diagnostics</h3>",
                    _trade_cost_contract_table(data, rows),
                    "<h3>Turnover And Exposure</h3>",
                    _turnover_exposure_contract_table(data, rows),
                    "<h3>Capacity And Liquidity</h3>",
                    _capacity_contract_table(data, rows),
                    "<h3>Guard Attribution</h3>",
                    _guard_attribution_contract_table(data, rows),
                    "<h3>Drawdown Episodes</h3>",
                    _drawdown_episode_contract_table(data, rows),
                    "<h3>Trade Distribution</h3>",
                    _trade_distribution_contract_table(data, rows),
                    "<h3>Rolling Stability And Regime</h3>",
                    _rolling_regime_contract_table(data, rows),
                    "<h3>Cost Decomposition</h3>",
                    _cost_decomposition_contract_table(data, rows),
                ]
            ),
        ),
        _details_block(
            "C. Walk-forward audit evidence",
            "\n".join(
                [
                    "<h3>Methodology</h3>",
                    _walkforward_methodology_contract_table(rows),
                    "<h3>Summary</h3>",
                    _walkforward_summary_contract_table(data, rows),
                    "<h3>Split Details</h3>",
                    _walkforward_split_contract_table(data, rows),
                ]
            ),
        ),
        _details_block(
            "D. Report navigation and artifact links",
            "\n".join(
                [
                    _full_report_link_table(data, rows),
                    _artifact_links(rows),
                ]
            ),
        ),
        "</section>",
    ]
    return _html_document(title, "\n".join(body))


def _report_subject(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    title = str(row.get("title") or "").strip()
    strategy_id = _row_strategy_id(row)
    return title or strategy_id or "策略"


def _primary_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    return sorted(rows, key=_ledger_sort_key)[0]


def _row_strategy_id(row: Dict[str, Any]) -> str:
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    return str(row.get("strategy_id") or spec.get("strategy_id") or "not_integrated")


def _strategy_spec(row: Dict[str, Any]) -> Dict[str, Any]:
    return (row.get("evidence") or {}).get("strategy_spec") or {}


def _fast_validation_scope(row: Dict[str, Any]) -> str:
    spec = _strategy_spec(row)
    text = " ".join(
        str(part or "")
        for part in (
            row.get("strategy_id"),
            row.get("title"),
            row.get("thesis"),
            spec.get("strategy_id"),
            spec.get("strategy_type"),
            spec.get("signal_formula_key"),
        )
    ).lower()
    if any(token in text for token in ("etf", "rotation", "timing", "barbell", "asset_allocation", "asset allocation", "rsrs")):
        return "etf_timing_rotation"
    return "cross_sectional_alpha"


def _uses_cross_sectional_fast_validation(row: Dict[str, Any]) -> bool:
    return _fast_validation_scope(row) == "cross_sectional_alpha"


def _fast_validation_scope_text(row: Dict[str, Any]) -> str:
    if _uses_cross_sectional_fast_validation(row):
        return "cross-sectional alpha: Rank IC / ICIR / hit-rate evidence is required."
    return "ETF timing/rotation: cross-sectional Rank IC is not applicable; strict Backtester checklist is the required production evidence."


def _local_strategy_rerun(row: Dict[str, Any]) -> bool:
    evidence = row.get("evidence") or {}
    metadata = evidence.get("metadata") or {}
    source = str(row.get("source") or evidence.get("source") or metadata.get("source") or "").lower()
    return source == "local_strategy" or bool(evidence.get("local_strategy"))


def _report_status(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "no_hypothesis"
    return str(_primary_row(rows).get("status") or "needs_more_validation")


def _report_date(generated: str) -> str:
    try:
        return datetime.fromisoformat(generated.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return generated[:10] if generated else datetime.now(timezone.utc).date().isoformat()


def _report_benchmark(row: Dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    strict = metrics.get("strict_backtest") or {}
    benchmark = strict.get("benchmark") or {}
    diag = metrics.get("portfolio_diagnostics") or {}
    return str(benchmark.get("symbol") or diag.get("benchmark_symbol") or "000300")


def _badge(label: str, klass: str) -> str:
    return f'<span class="badge {escape(klass)}">{escape(label)}</span>'


def _report_metric_grid(rows: List[Dict[str, Any]], generated: str) -> str:
    row = _primary_row(rows)
    items = [
        ("最终状态", _report_status(rows)),
        ("研究对象", _row_strategy_id(row)),
        ("Benchmark", _report_benchmark(row)),
        ("报告日期", _report_date(generated)),
    ]
    cells = "".join(
        f'<div class="metric"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
        for label, value in items
    )
    return f'<div class="grid">{cells}</div>'


def _executive_snapshot_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    metrics = strict.get("metrics") or {}
    benchmark = strict.get("benchmark") or {}
    capacity = strict.get("capacity") or {}
    wf_scores, wf_reason, _ = _walkforward_scores(row)
    drawdown = _safe_float(metrics.get("max_drawdown_pct"))
    values = [
        (
            "当前结论",
            _report_status(rows),
            "正式 Go / No-Go 只看当前 checklist；walk-forward 保留为审计证据。",
        ),
        (
            "收益 / 回撤",
            f"CAGR={_pct(metrics.get('cagr'))}; MaxDD={_pct(abs(drawdown)) if drawdown is not None else 'n/a'}",
            "按 CAGR 所在区间匹配最大回撤门槛。",
        ),
        (
            "Benchmark",
            f"000300 CAGR={_pct(benchmark.get('benchmark_cagr'))}; MaxDD={_pct(benchmark.get('benchmark_max_drawdown_pct'))}",
            "用于判断收益是否只是市场 beta。",
        ),
        (
            "容量",
            f"max ADV={_pct(capacity.get('max_adv_participation'))}; 5% ADV 资金容量={_capacity_at_adv_limit(capacity, strict, 0.05)}",
            "以当前回测交易形态线性放大估算，实盘前仍需盘口复核。",
        ),
        (
            "Walk-forward 审计",
            _walkforward_summary_sentence(wf_scores, wf_reason) if wf_scores else "not_run",
            "当前不纳入 checklist；若后续恢复为门槛，应重新评估 candidate 状态。",
        ),
    ]
    body = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for label, value, note in values
    )
    return _table(["项目", "当前值", "阅读结论"], body)


def _strategy_logic_summary_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    logic = spec.get("strategy_logic") if isinstance(spec.get("strategy_logic"), dict) else {}
    values = [
        ("核心假设", logic.get("core_idea") or _strict_signal_plaintext(row)),
        ("交易范围", logic.get("universe") or _universe_summary(row)),
        ("信号与排序", logic.get("ranking_rule") or _signal_construction_steps(row)),
        ("调仓与执行", logic.get("rebalance_rule") or _logic_rebalance_rule(spec)),
        ("组合构建", logic.get("portfolio_construction") or _logic_portfolio_construction(spec)),
        ("风控/防御", logic.get("exit_rule") or _logic_exit_rule(spec)),
    ]
    body = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(_cell(value))}</td></tr>"
        for label, value in values
    )
    return _table(["策略逻辑", "摘要"], body)


def _strategy_logic_plain_paragraph(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    text = _strategy_logic_plain_text(_primary_row(rows))
    if not text:
        return ""
    return f'<p class="logic-plain"><b>白话版：</b>{escape(text)}</p>'


def _strategy_logic_plain_text(row: Dict[str, Any]) -> str:
    if not row:
        return ""
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    logic = spec.get("strategy_logic") if isinstance(spec.get("strategy_logic"), dict) else {}
    strategy_id = str(spec.get("strategy_id") or row.get("strategy_id") or "").lower()
    formula = str(spec.get("signal_formula_key") or "").lower()
    if "ashare_gold_equity_barbell_timing" in strategy_id or "ashare_gold_equity_barbell_timing" in formula:
        return (
            "这套策略先把仓位思路分成两部分：一部分承担 A 股大盘权益风险，另一部分用黄金 ETF 做防守。"
            "每天收盘后，它先看沪深300是否处在顺风期：价格在长期均线之上、动量也为正，就允许持有权益腿；"
            "否则就把主要仓位切到黄金。权益腿不是挑个股，而是在回测起点前已经存在、且有足够历史数据的宽基、创业板、红利等 ETF 类别里，"
            "先用当时能看到的基金规模选出各类别代表，再用动量除以波动率挑出最强的一只。"
            "顺风期组合同时拿权益 ETF 和黄金 ETF，逆风期主要拿黄金；所有信号都在收盘后生成，下一交易日执行，并受手数、现金、5% ADV、费用和冲击成本约束。"
        )
    if logic:
        core = _cell(logic.get("core_idea") or _strict_signal_plaintext(row))
        universe = _cell(logic.get("universe") or _universe_summary(row))
        ranking = _cell(logic.get("ranking_rule") or _signal_construction_steps(row))
        portfolio = _cell(logic.get("portfolio_construction") or _logic_portfolio_construction(spec))
        rebalance = _cell(logic.get("rebalance_rule") or _logic_rebalance_rule(spec))
        exit_rule = _cell(logic.get("exit_rule") or _logic_exit_rule(spec))
        return (
            f"这套策略的核心想法是：{core} 它先限定交易范围为 {universe}，"
            f"再按 {ranking} 决定买什么。组合层面，{portfolio} "
            f"调仓方式是：{rebalance} 如果条件不满足或触发风险，处理方式是：{exit_rule}"
        )
    return _strict_signal_plaintext(row)


def _cost_capacity_summary_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    diagnostics = strict.get("diagnostics") or {}
    capacity = strict.get("capacity") or {}
    values = [
        ("显式佣金税费", _money(diagnostics.get("total_commission")), "只含佣金/税费；滑点和冲击体现在成交价。"),
        ("成本拖累", _pct(_cost_drag_value(diagnostics)), "显式成本相对交易毛 PnL 的拖累。"),
        ("ADV 参与率", f"p95={_pct(capacity.get('p95_adv_participation'))}; max={_pct(capacity.get('max_adv_participation'))}", "当前 checklist 使用单笔 max ADV <= 5%。"),
        ("估算资金容量", _capacity_at_adv_limit(capacity, strict, 0.05), "按最大 ADV 参与率反推，超过后单笔订单会触及 5% ADV。"),
        ("冲击成本", f"max impact={_fmt(capacity.get('max_impact_bps'))} bps", "来自执行冲击模型。"),
    ]
    body = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for label, value, note in values
    )
    return _table(["项目", "当前值", "说明"], body)


def _key_risk_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    metrics = strict.get("metrics") or {}
    diagnostics = strict.get("diagnostics") or {}
    spec = ((row.get("evidence") or {}).get("strategy_spec") or {})
    wf_scores, wf_reason, _ = _walkforward_scores(row)
    values = [
        (
            "Walk-forward 稳定性",
            _walkforward_summary_sentence(wf_scores, wf_reason) if wf_scores else "not_run",
            "当前已降为审计项；若以后恢复上线门槛，这是最大弱点。",
        ),
        (
            "策略规则可解释性",
            _strict_signal_plaintext(row),
            _strategy_logic_risk_note(spec),
        ),
        (
            "Universe 选择偏差",
            _universe_bias_observation(row),
            _universe_bias_recommendation(spec),
        ),
        (
            "执行摩擦",
            f"insufficient_cash_rejections={_insufficient_cash_rejected_orders(diagnostics)}; total_trades={metrics.get('total_trades') or 'n/a'}",
            "拒单和现金约束会影响复现实盘仓位，建议保留在附录审计。",
        ),
    ]
    body = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for label, value, note in values
    )
    return _table(["风险点", "观察", "处理建议"], body)


def _details_block(title: str, body: str) -> str:
    return (
        '<details class="audit-details">'
        f"<summary>{escape(title)}</summary>"
        f'<div class="audit-body">{body}</div>'
        "</details>"
    )


def _capacity_at_adv_limit(capacity: Dict[str, Any], strict: Dict[str, Any], limit: float) -> str:
    max_adv = _safe_float(capacity.get("max_adv_participation")) if isinstance(capacity, dict) else None
    initial_cash = _safe_float(strict.get("initial_cash"))
    if max_adv is None or max_adv <= 0 or initial_cash is None:
        return "n/a"
    return _money(initial_cash * limit / max_adv)


def _compact_universe_text(value: Any, limit: int = 8) -> str:
    if isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value]
        sample = ", ".join(items[:limit])
        suffix = f"; sample={sample}" if sample else ""
        return f"{len(items)} symbols{suffix}"
    return str(value or "StrategySpec 未记录")


def _universe_bias_observation(row: Dict[str, Any]) -> str:
    spec = ((row.get("evidence") or {}).get("strategy_spec") or {})
    if spec.get("pit_universe_enabled") or spec.get("risk_category_symbols"):
        policy = str(spec.get("universe_selection_policy") or "")
        as_of = str(spec.get("universe_as_of") or "未记录")
        start = str(spec.get("universe_start") or "")
        end = str(spec.get("universe_end") or "")
        min_history = spec.get("universe_min_history_days_as_of")
        category_cap = spec.get("universe_max_symbols_per_category")
        universe = spec.get("universe")
        counts = spec.get("registered_universe_counts") or {}
        quality_note = ""
        if counts:
            quality_note = (
                f"; 注册池 active={int(counts.get('active_symbol_count') or 0)}"
                f"/registered={int(counts.get('registered_symbol_count') or 0)}"
                f"/missing_data={int(counts.get('missing_data_count') or 0)}"
            )
        if policy == "audited_stable_etf_registry":
            return (
                f"{_compact_universe_text(universe)}; 已审计稳定 ETF 注册池; "
                f"新增 ETF 类别必须人工审计注册; 每类最多={category_cap or '不限'}{quality_note}"
            )
        if policy == "dynamic_pit_category_wide":
            window = f"{start or '未记录'}~{end or '未记录'}"
            return (
                f"{_compact_universe_text(universe)}; 动态PIT类别宽池; "
                f"回测窗口={window}; 每个调仓点按当时可见 bar/PIT规模/流动性/lookback 过滤; 每类最多={category_cap or '不限'}{quality_note}"
            )
        return (
            f"{_compact_universe_text(universe)}; PIT类别池已锁定 as-of={as_of}; "
            f"起点前最少历史={min_history or 0}日; 每类最多={category_cap or '不限'}{quality_note}"
        )
    return _compact_universe_text(spec.get("universe"))


def _universe_bias_recommendation(spec: Dict[str, Any]) -> str:
    if spec.get("pit_universe_enabled") or spec.get("risk_category_symbols"):
        counts = spec.get("registered_universe_counts") or {}
        missing = int(counts.get("missing_data_count") or 0) if counts else 0
        quality_tail = "；注册 ETF 缺少数据时必须保留为 warning/fail。" if missing else ""
        if str(spec.get("universe_selection_policy") or "") == "audited_stable_etf_registry":
            return "候选池采用人工审计注册的稳定 ETF 代表池；不会从当前全市场 ETF 分类自动扩展历史候选，新增类别必须先注册并审计。" + quality_tail
        if str(spec.get("universe_selection_policy") or "") == "dynamic_pit_category_wide":
            return "候选池采用每个调仓点当时可见的宽 ETF universe；未来新发 ETF 只在上市并满足当日数据/规模/流动性/lookback 后进入，持仓由信号从宽池中选择。剩余风险取决于底层元数据是否覆盖清盘 ETF。" + quality_tail
        if int(spec.get("universe_max_symbols_per_category") or 0) == 1:
            return "已避免未来新发 ETF 进入历史候选池，并且每类只保留起点主代表以减少幸存池择优；剩余风险取决于底层元数据是否覆盖清盘 ETF。" + quality_tail
        return "已避免未来新发 ETF 进入历史候选池；剩余风险是数据源缺少已清盘/退市 ETF 时仍会有 survivorship bias。" + quality_tail
    return "需要确认 ETF/LOF 池是否按历史可得规则冻结，而不是用当前存续名单回溯。"


def _strategy_logic_risk_note(spec: Dict[str, Any]) -> str:
    logic = spec.get("strategy_logic") if isinstance(spec.get("strategy_logic"), dict) else {}
    if logic:
        return "StrategySpec.strategy_logic 已记录资产桶、切换条件、组合构建和防守腿；报告可直接审计规则。"
    return "建议把 gold/equity barbell 的资产桶、切换条件、现金/防御腿写入 StrategySpec.strategy_logic，避免报告只显示泛化描述。"


def _conclusion_paragraph(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次运行没有形成可审计的 hypothesis 记录，因此不能给出策略推荐。</p>"
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    wf_scores, wf_reason, wf_verdict = _walkforward_scores(row)
    status = str(row.get("status") or "needs_more_validation")
    strategy_id = _row_strategy_id(row)
    rank_ic = _fmt(metrics.get("rank_ic"))
    fdr = _fmt(metrics.get("fdr_adjusted_p"))
    sharpe = _fmt(strict_metrics.get("sharpe"))
    cagr = _pct(strict_metrics.get("cagr"))
    aggregate = _fmt(wf_scores.get("aggregate_oos_sharpe"))
    worst = _fmt(wf_scores.get("worst_oos_sharpe"))
    if status == "rejected":
        if not _has_signal_validation_metrics(metrics):
            if _uses_cross_sectional_fast_validation(row):
                text = (
                    f"{strategy_id} 当前 full report 缺少快研究 / HFQ 信号验证指标；"
                    f"本轮可审计证据来自 strict Backtester 与 purged walk-forward："
                    f"严格回测 Sharpe={sharpe}、CAGR={cagr}，样本外 aggregate OOS Sharpe={aggregate}、worst={worst}。"
                    "最终结论是不进入策略池或 paper trading；需要重跑 fast/full research 才能补齐 Rank IC、FDR、admission 和 PnL bridge。"
                )
            else:
                text = (
                    f"{strategy_id} 是 ETF timing/rotation 策略，横截面 Rank IC、Top bucket 和 PnL bridge 不适用；"
                    f"本轮正式 checklist 证据来自 strict Backtester：严格回测 Sharpe={sharpe}、CAGR={cagr}。"
                    f"purged walk-forward 仍作为审计展示，不纳入当前 Go / No-Go checklist：aggregate OOS Sharpe={aggregate}、worst={worst}。"
                    "最终结论以 strict checklist、交易笔数和单笔 ADV 约束为准。"
                )
        elif _signal_badge_class(metrics) == "fail":
            text = (
                f"{strategy_id} 未通过真实 A 股 HFQ 信号验证，Rank IC={rank_ic}、FDR={fdr}。"
                f"流水线仍完成策略集成、严格 Backtester 与 purged walk-forward 审计，"
                f"严格回测 Sharpe={sharpe}、CAGR={cagr}，样本外 aggregate OOS Sharpe={aggregate}。"
                "最终结论是不进入策略池或 paper trading，仅作为 rejected_strategy 归档。"
            )
        else:
            text = (
                f"{strategy_id} 的信号验证具备统计证据，Rank IC={rank_ic}、FDR={fdr}；"
                f"但严格回测仅 Sharpe={sharpe}、CAGR={cagr}，purged walk-forward 未通过，"
                f"aggregate OOS Sharpe={aggregate}、worst OOS Sharpe={worst}。"
                "最终结论是不进入策略池或 paper trading，仅作为 rejected_strategy 归档。"
            )
    elif status in {"candidate", "paper_trading_candidate"}:
        text = (
            f"{strategy_id} 当前状态为 {status}，已通过当前 strict checklist。"
            "walk-forward 仍作为审计风险展示，不参与当前 Go / No-Go；进入下一阶段前仍需人工复核容量、组合相关性、风控预算和实盘执行细节。"
        )
    else:
        text = (
            f"{strategy_id} 当前状态为 {status}，尚不足以进入 paper trading。"
            f"需要补齐最弱环节：{wf_reason or wf_verdict or '样本外稳定性、成本或容量验证'}。"
        )
    return f"<p>{escape(text)}</p>"


def _judgement_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    wf_scores, wf_reason, _ = _walkforward_scores(row)
    signal_class = _signal_badge_class(metrics) if _uses_cross_sectional_fast_validation(row) or _has_signal_validation_metrics(metrics) else "n/a"
    signal_badge_class = _stage_badge_class(signal_class) if signal_class == "n/a" else signal_class
    strict_class = _strict_badge_class(strict_metrics)
    walk_class = _walkforward_badge_class(wf_scores)
    deploy_class = _status_badge_class(_report_status(rows))
    body = [
        "<tr><td>信号验证</td>"
        + f"<td>{_badge(signal_class, signal_badge_class)}</td>"
        + f"<td>{escape(_signal_validation_summary(metrics, row))}</td></tr>",
        "<tr><td>严格回测</td>"
        + f"<td>{_badge(strict_class, strict_class)}</td>"
        + f"<td>{escape(_strict_backtest_summary(strict_metrics))}</td></tr>",
        "<tr><td>Walk-forward</td>"
        + f"<td>{_badge(walk_class, walk_class)}</td>"
        + f"<td>{escape(_walkforward_summary_sentence(wf_scores, wf_reason) + '；当前不纳入 Go / No-Go checklist')}</td></tr>",
        "<tr><td>部署建议</td>"
        + f"<td>{_badge(_report_status(rows), deploy_class)}</td>"
        + f"<td>{escape(_deployment_summary(row))}</td></tr>",
    ]
    return _table(["判断项", "结果", "解释"], body)


def _stage_conclusion_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>本次没有可展示的研究阶段结论。</p>"
    stages = _stage_conclusions_for_report(data, row)
    body = []
    for key in ("fast_research", "strict_backtest", "walkforward_strict_audit", "final_decision"):
        stage = stages.get(key) or {}
        verdict = str(stage.get("verdict") or "not_run")
        label = str(stage.get("label") or key)
        method = str(stage.get("method") or "")
        conclusion = str(stage.get("conclusion") or "")
        body.append(
            "<tr>"
            + f"<td>{escape(label)}</td>"
            + f"<td>{_badge(verdict, _stage_badge_class(verdict))}</td>"
            + f"<td>{escape(conclusion)}</td>"
            + f"<td>{escape(method)}</td>"
            + "</tr>"
        )
    return _table(["功能", "结论", "当前研究判断", "执行口径"], body)


def _single_stage_conclusion_table(data: Dict[str, Any], rows: List[Dict[str, Any]], stage_key: str) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>本次没有可展示的研究阶段结论。</p>"
    stage = _stage_conclusions_for_report(data, row).get(stage_key) or {}
    verdict = str(stage.get("verdict") or "not_run")
    method = str(stage.get("method") or _STAGE_REPORT_META[stage_key]["description"])
    conclusion = str(stage.get("conclusion") or "本阶段尚未形成结构化结论。")
    body = [
        f"<tr><td>研究对象</td><td>{escape(_row_strategy_id(row))}</td></tr>",
        f"<tr><td>阶段</td><td>{escape(str(stage.get('label') or _STAGE_REPORT_META[stage_key]['label']))}</td></tr>",
        f"<tr><td>结论</td><td>{_badge(verdict, _stage_badge_class(verdict))}</td></tr>",
        f"<tr><td>当前研究判断</td><td>{escape(conclusion)}</td></tr>",
        f"<tr><td>执行口径</td><td>{escape(method)}</td></tr>",
    ]
    return _table(["字段", "内容"], body)


def _stage_report_link_table(
    data: Dict[str, Any],
    rows: List[Dict[str, Any]],
    current_stage: str | None = None,
) -> str:
    row = _primary_row(rows)
    stages = _stage_conclusions_for_report(data, row) if row else {}
    body = []
    for key, path in STAGE_REPORT_HTML.items():
        meta = _STAGE_REPORT_META[key]
        stage = stages.get(key) or {}
        verdict = str(stage.get("verdict") or "not_run")
        label = str(stage.get("label") or meta["label"])
        current = "（当前）" if key == current_stage else ""
        href = escape(path.as_posix())
        body.append(
            "<tr>"
            + f"<td>{escape(label)}{current}</td>"
            + f"<td>{_badge(verdict, _stage_badge_class(verdict))}</td>"
            + f'<td><a href="{href}">{escape(path.as_posix())}</a></td>'
            + f"<td>{escape(str(stage.get('conclusion') or '尚未运行'))}</td>"
            + "</tr>"
        )
    return _table(["阶段", "结论", "HTML", "摘要"], body)


def _full_report_link_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    stages = _stage_conclusions_for_report(data, row) if row else {}
    final_stage = stages.get("final_decision") or {}
    final_verdict = str(final_stage.get("verdict") or _report_status(rows))
    body = [
        "<tr>"
        + "<td>End-to-end</td>"
        + f"<td>{_badge(final_verdict, _stage_badge_class(final_verdict))}</td>"
        + f'<td><a href="{escape(FULL_REPORT_HTML.as_posix())}">{escape(FULL_REPORT_HTML.as_posix())}</a></td>'
        + f"<td>{escape(str(final_stage.get('conclusion') or _deployment_summary(row)))}</td>"
        + "</tr>"
    ]
    for key, path in STAGE_REPORT_HTML.items():
        meta = _STAGE_REPORT_META[key]
        stage = stages.get(key) or {}
        verdict = str(stage.get("verdict") or "not_run")
        label = str(stage.get("label") or meta["label"])
        body.append(
            "<tr>"
            + f"<td>{escape(label)}</td>"
            + f"<td>{_badge(verdict, _stage_badge_class(verdict))}</td>"
            + f'<td><a href="{escape(path.as_posix())}">{escape(path.as_posix())}</a></td>'
            + f"<td>{escape(str(stage.get('conclusion') or 'not_run'))}</td>"
            + "</tr>"
        )
    return _table(["Report", "Verdict", "HTML", "Summary"], body)


def _full_report_evidence_map(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    stages = _stage_conclusions_for_report(data, row) if row else {}
    items = [
        (
            "Final Decision",
            "1",
            str((stages.get("final_decision") or {}).get("verdict") or _report_status(rows)),
            "Deployment conclusion, stage verdicts, and reviewer judgement.",
        ),
        (
            "Go / No-Go Checklist",
            "2",
            "listed",
            "Observed value, recommended threshold, and pass/fail marker for each production gate metric.",
        ),
        (
            "Fast Research",
            "3",
            str((stages.get("fast_research") or {}).get("verdict") or "not_run"),
            "Idea source, signal definition, HFQ validation, portfolio diagnostics, and PnL bridge.",
        ),
        (
            "Strict Backtest",
            "4",
            str((stages.get("strict_backtest") or {}).get("verdict") or "not_run"),
            "Execution logic, equity curve, return calendar, config, data audit, performance, costs, capacity, drawdown, trades, regimes, and cost decomposition.",
        ),
        (
            "Walk-forward Audit",
            "5",
            str((stages.get("walkforward_strict_audit") or {}).get("verdict") or "not_run"),
            "Purged rolling OOS diagnostics retained for audit; currently excluded from Go / No-Go checklist.",
        ),
        (
            "Artifacts",
            "6",
            "linked",
            "Standalone stage reports and generated strategy/report artifact paths.",
        ),
    ]
    body = "".join(
        "<tr>"
        + f"<td>{escape(area)}</td>"
        + f"<td>{escape(section)}</td>"
        + f"<td>{_evidence_map_badge(verdict)}</td>"
        + f"<td>{escape(coverage)}</td>"
        + "</tr>"
        for area, section, verdict, coverage in items
    )
    return _table(["Area", "Section", "Stage / Availability", "Evidence Coverage"], [body])


def _evidence_map_badge(value: str) -> str:
    if value in {"listed", "linked"}:
        return _badge(value, "pass")
    return _badge(value, _stage_badge_class(value))


def _end_to_end_checklist_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>No hypothesis row was recorded, so the end-to-end checklist cannot be evaluated.</p>"
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    body = []
    capacity = strict.get("capacity") or {}
    body.extend([
        _checklist_adv_participation_row(capacity),
        _checklist_int_gt_row("total_trades", strict_metrics.get("total_trades"), 50),
        _checklist_cagr_drawdown_tier_row(strict_metrics),
    ])
    return _table(["Metric", "Observed", "Threshold", "Result"], body)


def _checklist_min_row(metric: str, value: Any, minimum: float) -> str:
    number = _safe_float(value)
    verdict = "fail" if number is None else ("pass" if number >= minimum else "fail")
    observed = "missing" if number is None else _fmt(value)
    return _checklist_row(metric, observed, f">={minimum:.4f}", verdict)


def _checklist_int_min_row(metric: str, value: Any, minimum: int) -> str:
    number = _safe_float(value)
    observed = "missing" if number is None else str(int(number))
    verdict = "fail" if number is None else ("pass" if number >= minimum else "fail")
    return _checklist_row(metric, observed, f">={minimum}", verdict)


def _checklist_int_gt_row(metric: str, value: Any, minimum: int) -> str:
    number = _safe_float(value)
    observed = "missing" if number is None else str(int(number))
    verdict = "fail" if number is None else ("pass" if number > minimum else "fail")
    return _checklist_row(metric, observed, f">{minimum}", verdict)


def _checklist_adv_participation_row(capacity: Dict[str, Any]) -> str:
    value = capacity.get("max_adv_participation") if isinstance(capacity, dict) else None
    number = _safe_float(value)
    observed = "missing" if number is None else _pct(number)
    verdict = "fail" if number is None else ("pass" if number <= 0.05 else "fail")
    return _checklist_row("max_adv_participation", observed, "<=5.00% ADV", verdict)


def _checklist_cagr_drawdown_tier_row(metrics: Dict[str, Any]) -> str:
    cagr = _safe_float(metrics.get("cagr"))
    drawdown = _safe_float(metrics.get("max_drawdown_pct"))
    if cagr is None or drawdown is None:
        return _checklist_row("cagr_drawdown_tier", "missing", "CAGR/MaxDD required", "fail")
    observed = f"CAGR={_pct(cagr)}; MaxDD={_pct(abs(drawdown))}"
    tier = _cagr_drawdown_tier(cagr)
    if tier is None:
        return _checklist_row("cagr_drawdown_tier", observed, "CAGR >=5.00%", "fail")
    label, max_drawdown = tier
    verdict = "pass" if abs(drawdown) <= max_drawdown else "fail"
    return _checklist_row(
        "cagr_drawdown_tier",
        observed,
        f"CAGR {label} requires MaxDD <={_pct(max_drawdown)}",
        verdict,
    )


def _checklist_pct_min_row(metric: str, value: Any, minimum: float) -> str:
    number = _safe_float(value)
    verdict = "fail" if number is None else ("pass" if number >= minimum else "fail")
    observed = "missing" if number is None else _pct(value)
    return _checklist_row(metric, observed, f">={minimum * 100:.2f}%", verdict)


def _checklist_abs_pct_max_row(metric: str, value: Any, maximum: float) -> str:
    number = _safe_float(value)
    observed = "missing" if number is None else _pct(abs(number))
    verdict = "fail" if number is None else ("pass" if abs(number) <= maximum else "fail")
    return _checklist_row(metric, observed, f"<={maximum * 100:.2f}% abs", verdict)


def _checklist_abs_max_row(metric: str, value: Any, maximum: float) -> str:
    number = _safe_float(value)
    verdict = "fail" if number is None else ("pass" if abs(number) <= maximum else "fail")
    observed = "missing" if number is None else _fmt(value)
    return _checklist_row(metric, observed, f"<={maximum:.4f} abs", verdict)


def _checklist_bool_row(metric: str, value: Any) -> str:
    verdict_bool = _coerce_check_bool(value)
    if verdict_bool is None:
        return _checklist_row(metric, "missing", "must pass", "fail")
    return _checklist_row(metric, str(verdict_bool).lower(), "must pass", "pass" if verdict_bool else "fail")


def _coerce_check_bool(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"true", "pass", "passed", "ok", "yes", "viable"}:
        return True
    if text in {"false", "fail", "failed", "no", "not_viable", "not viable"}:
        return False
    return None


def _cagr_drawdown_tier(cagr: float) -> tuple[str, float] | None:
    if cagr < 0.05:
        return None
    if cagr < 0.10:
        return "5.00%-10.00%", 0.15
    if cagr < 0.15:
        return "10.00%-15.00%", 0.25
    if cagr < 0.20:
        return "15.00%-20.00%", 0.30
    return ">=20.00%", 0.50


def _checklist_row(metric: str, observed: str, threshold: str, verdict: str) -> str:
    klass = "pass" if verdict == "pass" else ("warn" if verdict == "n/a" else "fail")
    return (
        "<tr>"
        + f"<td>{escape(metric)}</td>"
        + f"<td>{escape(observed)}</td>"
        + f"<td>{escape(threshold)}</td>"
        + f"<td>{_badge(verdict, klass)}</td>"
        + "</tr>"
    )


def _stage_specific_sections(stage_key: str, data: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[str]:
    if stage_key == "fast_research":
        return [
            '<section class="panel">',
            "<h2>2. 快研究证据</h2>",
            "<h3>idea 来源与初筛</h3>",
            _idea_source_overview_table(rows),
            "<h3>来源质量与准入评分</h3>",
            _source_quality_score_table(data, rows),
            "<h3>信号定义</h3>",
            _strategy_spec_contract_table(rows),
            "<h3>信号公式</h3>",
            _formula_block(rows),
            "<h3>信号验证</h3>",
            _signal_validation_contract_table(data, rows),
            "<h3>组合诊断</h3>",
            _portfolio_diagnostics_contract_table(data, rows),
            "<h3>PnL 归因桥</h3>",
            _pnl_attribution_bridge_contract_table(data, rows),
            "</section>",
        ]
    if stage_key == "strict_backtest":
        return [
            '<section class="panel">',
            "<h2>2. 严格回测证据</h2>",
            "<h3>策略执行逻辑</h3>",
            _strategy_execution_logic_contract(data, rows),
            "<h3>回测 Equity Curve</h3>",
            _equity_curve_chart(data, rows),
            "<h3>年度收益日历图</h3>",
            _yearly_return_calendar(data, rows),
            "<h3>回测配置</h3>",
            _backtest_config_contract_table(data, rows),
            "<h3>数据完整性审计</h3>",
            _data_quality_contract_table(data, rows),
            "<h3>核心绩效</h3>",
            _core_performance_contract_table(data, rows),
            "<h3>成交与成本诊断</h3>",
            _trade_cost_contract_table(data, rows),
            "<h3>换手与持仓暴露</h3>",
            _turnover_exposure_contract_table(data, rows),
            "<h3>容量与流动性压力</h3>",
            _capacity_contract_table(data, rows),
            "<h3>退市护栏命中归因</h3>",
            _guard_attribution_contract_table(data, rows),
            "<h3>回撤过程</h3>",
            _drawdown_episode_contract_table(data, rows),
            "<h3>交易分布</h3>",
            _trade_distribution_contract_table(data, rows),
            "<h3>滚动稳定性与市场阶段</h3>",
            _rolling_regime_contract_table(data, rows),
            "<h3>成本口径拆分</h3>",
            _cost_decomposition_contract_table(data, rows),
            "</section>",
        ]
    return [
        '<section class="panel">',
        "<h2>2. Walk-forward Audit 证据</h2>",
        "<h3>方法设置</h3>",
        _walkforward_methodology_contract_table(rows),
        "<h3>结果摘要</h3>",
        _walkforward_summary_contract_table(data, rows),
        "<h3>Split 明细</h3>",
        _walkforward_split_contract_table(data, rows),
        "</section>",
    ]


def _stage_conclusions_for_report(data: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metrics = row.get("metrics") or {}
    raw = metrics.get("research_stage_conclusions") or {}
    stages = {key: dict(value) for key, value in raw.items() if isinstance(value, dict)}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    wf_scores, wf_reason, wf_verdict = _walkforward_scores(row)

    stages.setdefault(
        "fast_research",
        {
            "label": "快研究",
            "verdict": (
                _signal_badge_class(metrics)
                if metrics.get("rank_ic") is not None
                else ("fail" if _uses_cross_sectional_fast_validation(row) else "n/a")
            ),
            "conclusion": (
                _signal_validation_summary(metrics, row)
                if metrics.get("rank_ic") is not None
                else _signal_validation_summary(metrics, row)
            ),
            "method": "来源/admission、StrategySpec、HFQ 信号验证和向量化组合诊断。",
        },
    )
    stages.setdefault(
        "strict_backtest",
        {
            "label": "严格回测",
            "verdict": _strict_badge_class(strict_metrics) if strict_metrics else "not_run",
            "conclusion": (
                _strict_backtest_summary(strict_metrics)
                if strict_metrics
                else "本轮未运行 strict Backtester；不能形成严格回测通过结论。"
            ),
            "method": "项目 Backtester，含真实执行约束、成本和持仓 accounting。",
        },
    )
    stages.setdefault(
        "walkforward_strict_audit",
        {
            "label": "Walk-forward strict audit",
            "verdict": wf_verdict or (_walkforward_badge_class(wf_scores) if wf_scores else "not_run"),
            "conclusion": (
                _walkforward_summary_sentence(wf_scores, wf_reason)
                if wf_scores
                else "本轮未运行 walk-forward strict audit；不能形成样本外稳定性通过结论。"
            ),
            "method": "滚动 OOS split 重放 strict Backtester，作为最终稳定性审计。",
        },
    )
    stages.setdefault(
        "final_decision",
        {
            "label": "最终 Go / No-Go",
            "verdict": _report_status([row]),
            "conclusion": _deployment_summary(row),
            "method": "汇总 strict checklist；walk-forward 当前仅作为审计展示。",
        },
    )
    return stages


def _stage_badge_class(verdict: str) -> str:
    value = str(verdict or "").lower()
    if value in {"pass", "candidate", "paper_trading_candidate"}:
        return "pass"
    if value in {"warn", "warning", "needs_more_validation", "validated", "idea_candidate", "not_run", "n/a"}:
        return "warn"
    return "fail"


def _idea_source_overview_table(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>本次没有搜集到策略 idea。</p>"
    evidence = row.get("evidence") or {}
    metadata = evidence.get("metadata") or {}
    authors = evidence.get("authors") or metadata.get("authors") or row.get("authors") or "未记录"
    body = [
        f"<tr><td>标题</td><td>{escape(str(row.get('title') or '未命名 idea'))}</td><td>必须写完整标题</td></tr>",
        f"<tr><td>来源</td><td>{_source_link(row)}</td><td>必须附 URL</td></tr>",
        f"<tr><td>发布时间</td><td>{escape(str(evidence.get('published_date') or '未记录'))}</td><td>用于判断 post-publication 风险</td></tr>",
        f"<tr><td>作者/机构</td><td>{escape(_join_text(authors))}</td><td>用于来源可信度审计</td></tr>",
        f"<tr><td>核心假设</td><td>{_core_hypothesis_cell(row)}</td><td>用中文说明收益来源、适用边界和必须验证的约束</td></tr>",
    ]
    return _table(["字段", "内容", "要求"], body)


def _core_hypothesis_cell(row: Dict[str, Any]) -> str:
    items = _core_hypothesis_items(row)
    if items:
        return '<ol class="hypothesis-list">' + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ol>"
    return escape(str(row.get("thesis") or _decision_reason(row)))


def _core_hypothesis_items(row: Dict[str, Any]) -> List[str]:
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    formula = str(spec.get("signal_formula_key", "") or "").lower()
    if formula == "joinquant_small_cap_low_price_factor":
        return [
            "这不是基本面预测模型，而是一个 A 股日频截面风格信号：在可交易股票中，先限定名义股价位于 2-20 元且具备基本流动性，再偏好市值更小的股票。",
            "经济直觉是低价股更容易受到散户交易、题材资金和单位价格可负担性的影响；在低价股票内部，小市值公司对边际资金更敏感，可能形成短周期的小盘弹性和风险补偿。",
            "主要风险是该收益可能只是小盘、低流动性、涨跌停约束或退市尾部风险补偿，所以必须用 ST/停牌/可交易状态、T+1、涨跌停、成交量、佣金滑点和沪深 300 超额收益一起检验。",
        ]
    if formula == "joinquant_small_cap_size_factor":
        return [
            "核心假设是 A 股小市值股票在部分市场阶段存在风格溢价，市值越小，对边际资金和风险偏好变化越敏感。",
            "信号本身不预测行业或基本面改善，只表达小盘暴露；因此研究结论必须区分真实 alpha、风格 beta 和流动性补偿。",
            "落地前必须检验 ST/停牌、涨跌停、T+1、成交量容量、交易成本和回撤，因为小市值组合最容易在执行层面打折。",
        ]
    if "momentum" in formula:
        return [
            "核心假设是近期强势股票可能存在趋势延续，价格动量反映资金继续流入、信息扩散或风险偏好持续。",
            "该假设容易受市场 regime 影响，牛市中更可能有效，震荡或急跌阶段可能快速反转。",
            "必须用样本外验证、换手成本和回撤检验，避免把阶段性趋势误判为稳定 alpha。",
        ]
    if "reversal" in formula or "mean_reversion" in formula or "mean" in formula:
        return [
            "核心假设是短期过度下跌或偏离均值后，价格存在修复压力，反转收益来自流动性冲击消退或投资者过度反应修正。",
            "该信号在市场急跌或基本面恶化时可能接住下跌趋势，所以需要额外关注尾部风险和止损/容量约束。",
            "研究必须验证反转收益是否能覆盖交易成本，并确认不是由少数极端日期贡献。",
        ]
    text = str(row.get("thesis") or _decision_reason(row)).strip()
    return [text] if text else []


def _source_quality_score_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>本次没有写入准入评估记录。</p>"
    evidence = row.get("evidence") or {}
    quality = evidence.get("discovery_quality") or {}
    metrics = row.get("metrics") or {}
    if not quality and not any(
        _first_present(metrics, *keys) is not None
        for keys in (
            ("data_availability_score", "cost_capacity_score", "implementation_score"),
            ("admission_score",),
        )
    ):
        status = "n/a" if _local_strategy_rerun(row) or not _uses_cross_sectional_fast_validation(row) else "missing"
        reason = (
            "Existing local ETF timing/rotation strategy rerun: idea discovery/admission scoring is not the governing evidence; strict Backtester and walk-forward OOS gates are shown below."
            if status == "n/a"
            else "Fast research admission evidence is missing; rerun fast/full research before treating this cross-sectional alpha as complete."
        )
        return _message_table(
            ["维度", "状态", "解释"],
            "fast research admission",
            status,
            reason,
        )
    score_rows = [
        ("source_quality", _first_present(quality, "source_quality_score", "source_quality"), "来源是否可信、是否有正式论文或可复现材料"),
        ("recency", _first_present(quality, "recency_score", "recency"), "发布时间与当前研究窗口的关系"),
        ("formula_clarity", _first_present(quality, "detail_score", "formula_clarity_score", "formula_clarity"), "是否有明确公式、排序方向、持有期"),
        ("daily_feasibility", _first_present(quality, "daily_data_score", "daily_feasibility_score", "daily_feasibility"), "是否可以用日线 OHLCV / 可得字段实现"),
        ("A 股适配性", _first_present(metrics, "data_availability_score", "cost_capacity_score", "implementation_score"), "是否适合 A 股 long-only、T+1、涨跌停和流动性约束"),
        ("admission_score", metrics.get("admission_score"), "低于阈值不得进入正式研究"),
    ]
    body = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(_fmt(value))}</td><td>{escape(note)}</td></tr>"
        for label, value, note in score_rows
    )
    return '<div class="table-wrap"><table><thead><tr><th>维度</th><th>分数</th><th>解释</th></tr></thead><tbody>' + body + "</tbody></table></div>"


def _strategy_spec_contract_table(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    if not spec:
        return "<p>本次没有生成 ready 状态的 StrategySpec。</p>"
    rows_data = [
        ("strategy_id", _row_strategy_id(row), "策略目录、候选池、报告归档的主键"),
        ("signal_formula_key", spec.get("signal_formula_key"), "对应验证器和策略实现中的公式"),
        ("prediction_direction", _prediction_direction(row), "必须与 IC 符号一致"),
        ("lookback_days", spec.get("lookback_days"), "历史窗口"),
        ("horizon_days", spec.get("horizon_days"), "预测和持有期"),
        ("rebalance_frequency", spec.get("rebalance_frequency") or "daily", "影响换手和成本"),
        ("universe", _universe_summary(row), "A 股默认 long-only；报告显示验证器解析后的真实 universe"),
        ("required_fields", _join_text(spec.get("required_fields") or []), "缺字段不得静默通过"),
    ]
    body = "".join(
        f"<tr><td>{escape(field)}</td><td>{escape(_cell(value))}</td><td>{escape(note)}</td></tr>"
        for field, value, note in rows_data
    )
    return _table(["StrategySpec 字段", "取值", "说明"], body)


def _formula_block(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    lookback = spec.get("lookback_days") or 20
    horizon = spec.get("horizon_days") or 5
    lag = spec.get("execution_lag_days") or 1
    formula = str(spec.get("signal_formula_key") or "")
    if formula == "worldquant_alpha_001":
        lines = [
            "returns_i,t = adj_close_i,t / adj_close_i,t-1 - 1",
            "base_i,t = if returns_i,t < 0 then stddev(returns_i,t-19:t, 20) else adj_close_i,t",
            "signal_i,t = rank_cross_section(ts_argmax(signedpower(base_i,t, 2), 5)) - 0.5",
            f"target_i,t+{lag} = top_20(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    elif formula == "worldquant_alpha_002":
        lines = [
            "x_i,t = rank_cross_section(delta(log(volume_i,t), 2))",
            "y_i,t = rank_cross_section((adj_close_i,t - adj_open_i,t) / adj_open_i,t)",
            f"signal_i,t = -corr_ts(x_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, y_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, {lookback})",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_20(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    elif formula == "worldquant_alpha_003":
        lines = [
            "x_i,t = rank_cross_section(adj_open_i,t)",
            "y_i,t = rank_cross_section(volume_i,t)",
            f"signal_i,t = -corr_ts(x_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, y_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, {lookback})",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_20(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    elif formula == "worldquant_alpha_004":
        lines = [
            "low_rank_i,t = rank_cross_section(adj_low_i,t)",
            f"signal_i,t = -ts_rank(low_rank_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, {lookback})",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_20(rank_i,t), long-only; no signal > 0 filter because signal <= 0",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    elif formula == "joinquant_small_cap_low_price_factor":
        lines = [
            "信号类型：A 股日频横截面 long-only 选股信号；信号值越高，越优先进入目标组合。",
            "输入字段：raw close 用于低价/面值退市风险过滤；turnover 用于基本流动性过滤；point-in-time market_cap/total_mv/circ_mv 用于市值排序；收益验证使用 HFQ adj_close。",
            "有效样本：2 <= price_i,t <= 20；20 日均 turnover_i,t >= 20000；market_cap_i,t > 0。",
            "fast validation: signal_i,t = -market_cap_i,t if eligible else NaN；等价于在低价股票中优先选择市值最小的股票。",
            "strict strategy: signal_i,t = 1 / market_cap_i,t，并额外排除 ST、停牌、tradable=false、list_status != L、近期停牌、无有效价格或无有效市值的股票；持仓触发风险后每日尝试退出。",
            "rank_i,t = rank_desc(signal_i,t)；每天按信号从高到低选择 Top 20。",
            f"执行路径：t 日收盘后形成目标名单，t+{lag} 由 Backtester 下 MARKET 单；long-only、当前研究默认目标总仓位 100%、Top 20 等权目标。",
            "交易约束：A 股 T+1、100 股一手、涨跌停、停牌、成交量限制、现金不足、佣金和 5bps 滑点都会在严格回测中生效。",
            f"验证标签：forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1；horizon={horizon} 个交易日。",
        ]
    elif formula == "joinquant_small_cap_size_factor":
        lines = [
            "信号类型：A 股日频横截面 long-only 小市值选股信号；信号值越高，市值越小。",
            "输入字段：point-in-time market_cap/total_mv/circ_mv；收益验证使用 HFQ adj_close。",
            "有效样本：market_cap_i,t > 0。",
            "signal_i,t = -market_cap_i,t",
            "rank_i,t = rank_desc(signal_i,t)；每天按信号从高到低选择 Top 20。",
            f"执行路径：t 日收盘后形成目标名单，t+{lag} 由 Backtester 按 A 股约束执行。",
            f"验证标签：forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1；horizon={horizon} 个交易日。",
        ]
    elif "mean_reversion" in formula or "reversal" in formula:
        lines = [
            f"ma_i,t = mean(adj_close_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback} ... adj_close_i,t)",
            "raw_reversal_i,t = (ma_i,t - adj_close_i,t) / ma_i,t",
            "industry_momentum_i,t = mean(industry_return_i,t-lookback ... industry_return_i,t)",
            "signal_i,t = raw_reversal_i,t with industry momentum hedge and cross-sectional winsorization",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_20(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    else:
        lines = [
            f"signal_i,t = {formula or 'StrategySpec declared signal'}",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_20(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    return f'<div class="formula">{escape(chr(10).join(lines))}</div>'


def _trade_explanation_list(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    lookback = spec.get("lookback_days") or "StrategySpec"
    horizon = spec.get("horizon_days") or "StrategySpec"
    lag = spec.get("execution_lag_days") or 1
    items = [
        "判断逻辑使用后复权价格：优先 adj_close / adj_open / adj_high / adj_low，缺失时才用 raw price * adj_factor。",
        "A 股不允许 long-short 作为可部署组合；long-short 只能作为 alpha 诊断表。",
        f"信号使用 lookback={lookback} 个交易日、horizon={horizon} 个交易日，信号日和成交日保留至少 {lag} 个交易日延迟以避免 look-ahead。",
    ]
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _data_source_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    benchmark = strict.get("benchmark") or {}
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    data_start, data_end = _data_coverage(metrics, strict)
    n_obs = metrics.get("n_observations") or "未记录"
    data_rows = metrics.get("data_rows") or "未记录"
    benchmark_symbol = benchmark.get("symbol") or _report_benchmark(row)
    fallback_used = bool(benchmark.get("fallback_used", False))
    fallback_note = "未使用；000300 已可用" if not fallback_used else "已使用；需说明 000300 缺失原因"
    body = [
        f"<tr><td>行情表</td><td>DuckDB <code>daily_cn_ochl</code></td><td>{escape(str(data_start))} - {escape(str(data_end))}, {escape(str(data_rows))} symbol-date rows, {escape(str(n_obs))} valid IC dates</td></tr>",
        "<tr><td>价格口径</td><td>HFQ 后复权</td><td>优先 adj_*；缺失时 raw price * adj_factor，不允许静默切换</td></tr>",
        f"<tr><td>Universe</td><td>{escape(_universe_summary(row))}</td><td>当前报告按 A 股 long-only 和 PIT universe 约束审计；清盘 ETF 覆盖取决于底层元数据</td></tr>",
        f"<tr><td>Benchmark</td><td>{escape(str(benchmark_symbol))} 沪深 300</td><td>{escape(str(benchmark.get('coverage_start') or '未记录'))} - {escape(str(benchmark.get('coverage_end') or '未记录'))}, {escape(str(benchmark.get('rows') or '未记录'))} rows</td></tr>",
        f"<tr><td>Fallback</td><td>510300</td><td>{escape(fallback_note)}</td></tr>",
    ]
    return _table(["项目", "定义", "覆盖/备注"], body)


def _data_quality_contract_list(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    benchmark = (strict.get("benchmark") or {}) if strict else {}
    data_start, data_end = _data_coverage(metrics, strict)
    data_symbol_count = metrics.get("data_symbol_count") or "未记录"
    items = [
        f"数据覆盖 {data_start} 至 {data_end}，验证器实际读取 {data_symbol_count} 个 A 股 symbol，信号验证有效截面日期 {metrics.get('n_observations') or '未记录'}；停牌、成交量为 0 和异常价格由数据提供层过滤或在诊断中暴露。",
        "信号日、成交日和 forward return 使用 execution lag / holding horizon 边界，避免训练、验证和成交之间的信息重叠。",
        f"benchmark={benchmark.get('symbol') or _report_benchmark(row)}，覆盖 {benchmark.get('coverage_start') or '未记录'} 至 {benchmark.get('coverage_end') or '未记录'}；当前报告未使用点时成分股数据，需在候选池阶段继续补强 survivorship 审计。",
    ]
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _signal_validation_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    wf_scores, _, wf_verdict = _walkforward_scores(row)
    if not _has_signal_validation_metrics(metrics):
        status = "missing" if _uses_cross_sectional_fast_validation(row) else "n/a"
        reason = (
            "Rank IC / ICIR / hit-rate evidence is missing; this is a failed evidence gate for cross-sectional alpha research."
            if status == "missing"
            else _fast_validation_scope_text(row)
        )
        return _message_table(
            ["Metric", "数值", "通常有意义/较好水平", "解释"],
            "HFQ signal validation",
            status,
            reason,
        )
    rows_data = [
        (
            "Rank IC",
            _fmt(metrics.get("rank_ic")),
            ">=0.02 有研究意义；>=0.04 较好；>=0.06 很强。必须与预测方向一致。",
            "横截面秩相关；正负号必须与预测方向一致",
        ),
        (
            "ICIR",
            _fmt(metrics.get("rank_ic_ir")),
            ">=0.20 有稳定性迹象；>=0.50 较好；>=1.00 很强。",
            "IC 均值 / IC 波动，衡量稳定性",
        ),
        (
            "Rank IC t-stat",
            _fmt(_first_present(metrics, "rank_ic_tstat", "t_stat")),
            "|t|>=2 通常认为显著；>=3 较强。",
            "每日 IC 均值对 0 的 t 检验",
        ),
        (
            "p-value",
            _fmt(_first_present(metrics, "rank_ic_p_value", "p_value", "rank_ic_p", "fdr_adjusted_p")),
            "<0.05 通常显著；<0.01 较强。",
            "每日 IC 均值 t 检验 p 值",
        ),
        (
            "FDR adjusted p",
            _fmt(metrics.get("fdr_adjusted_p")),
            "<0.10 可探索；<0.05 可给正式显著结论；<0.01 很强。",
            "多重检验控制；不过则不能给强结论",
        ),
        (
            "Hit rate",
            _pct(metrics.get("hit_rate")),
            ">50% 方向有效；>=55% 较好；>=60% 很强。",
            "预测方向命中率",
        ),
        (
            "IC decay",
            _format_decay(metrics.get("ic_decay")),
            "目标持有期 IC 仍为正且未快速归零才有意义；若远期 IC 高于近端，需检查滞后和执行口径。",
            "看 alpha 半衰期与持有期是否匹配",
        ),
        (
            "Fama-MacBeth t-stat",
            _fmt(metrics.get("fama_macbeth_tstat")),
            ">=2 通常显著；>=3 较强。",
            "横截面回归显著性",
        ),
        (
            "Factor exposure",
            _factor_exposure_text(metrics),
            "若做独立 alpha，ff_alpha_tstat>=2 且 R² 不应过高；高 R² 更像风格 beta。",
            "是否只是已知风险因子暴露",
        ),
        (
            "OOS validation",
            _signal_oos_text(metrics, wf_scores, wf_verdict),
            "正向 OOS split >50% 有意义；>=60%-70% 较好；最差 split 不能严重失控。",
            "滚动样本外是否保持方向和显著性",
        ),
    ]
    body = "".join(
        f"<tr><td>{escape(metric)}</td><td>{escape(str(value))}</td><td>{escape(good_level)}</td><td>{escape(note)}</td></tr>"
        for metric, value, good_level, note in rows_data
    )
    return _table(["Metric", "数值", "通常有意义/较好水平", "解释"], body)


def _portfolio_diagnostics_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    diag = metrics.get("portfolio_diagnostics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    benchmark = strict.get("benchmark") or {}
    if not diag:
        status = "missing" if _uses_cross_sectional_fast_validation(row) else "n/a"
        reason = (
            f"Vectorized portfolio diagnostics are missing for cross-sectional fast research; benchmark={_report_benchmark(row)}."
            if status == "missing"
            else f"ETF timing/rotation rerun uses strict Backtester results as the portfolio evidence; separate Top-bucket cross-sectional diagnostics are not applicable. benchmark={_report_benchmark(row)}."
        )
        return _message_table(
            ["组合", "年化", "Sharpe", "MaxDD", "Calmar Ratio", "换手", "成本后年化", "用途"],
            "vectorized portfolio diagnostics",
            status,
            reason,
        )
    rows_data = [
        (
            _top_bucket_label(diag),
            _pct(diag.get("top_bucket_annualized_return")),
            _fmt(_first_present(diag, "top_bucket_after_cost_sharpe", "top_bucket_sharpe")),
            _pct(_first_present(diag, "top_bucket_after_cost_max_drawdown", "top_bucket_max_drawdown") or strict_metrics.get("max_drawdown_pct")),
            _fmt(
                _first_present(diag, "top_bucket_after_cost_calmar_ratio", "top_bucket_calmar_ratio")
                or _first_present(strict_metrics, "calmar_ratio", "calmar")
            ),
            _pct(_first_present(diag, "top_bucket_turnover", "turnover")),
            _pct(diag.get("top_bucket_after_cost_annualized_return")),
            "A 股可交易方向诊断",
        ),
        (
            "Top 1% long-only",
            _pct(diag.get("top1_pct_annualized_return")),
            _fmt(_first_present(diag, "top1_pct_after_cost_sharpe", "top1_pct_sharpe")),
            _pct(_first_present(diag, "top1_pct_after_cost_max_drawdown", "top1_pct_max_drawdown")),
            _fmt(_first_present(diag, "top1_pct_after_cost_calmar_ratio", "top1_pct_calmar_ratio")),
            _pct(_first_present(diag, "top1_pct_turnover", "turnover")),
            _pct(diag.get("top1_pct_after_cost_annualized_return")),
            "极端头部信号集中度诊断",
        ),
        (
            f"Top vs {benchmark.get('symbol') or _report_benchmark(row)} excess",
            _pct(_first_present(diag, "benchmark_excess_after_cost_annualized_return", "benchmark_excess_annualized_return")),
            _fmt(_first_present(diag, "benchmark_excess_after_cost_sharpe", "benchmark_excess_sharpe") or benchmark.get("information_ratio")),
            _pct(_first_present(diag, "benchmark_excess_after_cost_max_drawdown", "benchmark_excess_max_drawdown")),
            _fmt(_first_present(diag, "benchmark_excess_after_cost_calmar_ratio", "benchmark_excess_calmar_ratio")),
            _pct(_first_present(diag, "top_bucket_turnover", "turnover")),
            _pct(diag.get("benchmark_excess_after_cost_annualized_return")),
            "相对沪深 300 诊断",
        ),
        (
            "Long-short diagnostic",
            _pct(metrics.get("long_short_spread")),
            _fmt(metrics.get("long_short_sharpe")),
            _pct(metrics.get("long_short_max_drawdown")),
            _fmt(_first_present(metrics, "long_short_calmar_ratio", "long_short_calmar")),
            _fmt(metrics.get("long_short_turnover")),
            _pct(metrics.get("long_short_after_cost_mean_return") or metrics.get("long_short_spread")),
            "仅 alpha 诊断，不可部署",
        ),
    ]
    body = "".join(
        "<tr>"
        + f"<td>{escape(name)}</td><td>{escape(ann)}</td><td>{escape(sharpe)}</td><td>{escape(maxdd)}</td>"
        + f"<td>{escape(calmar)}</td><td>{escape(turnover)}</td><td>{escape(after_cost)}</td><td>{escape(use)}</td></tr>"
        for name, ann, sharpe, maxdd, calmar, turnover, after_cost, use in rows_data
    )
    return _table(["组合", "年化", "Sharpe", "MaxDD", "Calmar Ratio", "换手", "成本后年化", "用途"], body)


def _top_bucket_label(diag: Dict[str, Any]) -> str:
    if str(diag.get("top_bucket_selection") or "") == "top_n":
        count = _safe_int(diag.get("top_bucket_target_count"))
        if count and count > 0:
            return f"Top {count} long-only"
    return "Top bucket long-only"


def _pnl_attribution_bridge_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    diag = metrics.get("portfolio_diagnostics") or {}
    bridge = list(diag.get("pnl_attribution_bridge") or [])
    if not bridge:
        status = "missing" if _uses_cross_sectional_fast_validation(row) else "n/a"
        reason = (
            "Signal -> portfolio -> strict Backtester attribution bridge is missing; rerun fast/full research to localize execution degradation."
            if status == "missing"
            else "Signal -> portfolio -> strict Backtester bridge is not applicable to this local ETF timing/rotation rerun; strict execution and walk-forward evidence are the attribution path."
        )
        return _message_table(
            ["层", "年化", "Δ年化", "Sharpe", "ΔSharpe", "MaxDD", "换手", "选股数", "说明"],
            "PnL attribution bridge",
            status,
            reason,
        )
    body = "".join(
        "<tr>"
        + f"<td>{escape(str(layer.get('label') or layer.get('key') or ''))}</td>"
        + f"<td>{escape(_pct(layer.get('annualized_return')))}</td>"
        + f"<td>{escape(_pct(layer.get('delta_annualized_return')))}</td>"
        + f"<td>{escape(_fmt(layer.get('sharpe')))}</td>"
        + f"<td>{escape(_fmt(layer.get('delta_sharpe')))}</td>"
        + f"<td>{escape(_pct(layer.get('max_drawdown')))}</td>"
        + f"<td>{escape(_pct(layer.get('turnover')))}</td>"
        + f"<td>{escape(_selected_count_text(layer))}</td>"
        + f"<td>{escape(str(layer.get('note') or ''))}</td>"
        + "</tr>"
        for layer in bridge
    )
    return _table(["层", "年化", "Δ年化", "Sharpe", "ΔSharpe", "MaxDD", "换手", "选股数", "说明"], body)


def _selected_count_text(layer: Dict[str, Any]) -> str:
    mean = layer.get("selected_count_mean")
    min_count = layer.get("selected_count_min")
    max_count = layer.get("selected_count_max")
    if mean is None:
        return "-"
    return f"{_fmt(mean)} ({_cell(min_count)}-{_cell(max_count)})"


def _strategy_logic_contract(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>本次没有可展示的策略逻辑。</p>"
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    logic = spec.get("strategy_logic") if isinstance(spec.get("strategy_logic"), dict) else {}
    values = [
        ("核心假设", logic.get("core_idea") or _strict_signal_plaintext(row), "解释为什么这个信号可能产生收益。"),
        ("交易范围", logic.get("universe") or spec.get("universe") or "StrategySpec 未记录", "说明策略在哪个标的池内做选择。"),
        ("入选过滤", _join_text(logic.get("entry_filters") or _logic_filters_from_spec(spec)), "这些条件决定哪些标的有资格被买入或继续持有。"),
        ("排序/信号", logic.get("ranking_rule") or _signal_construction_steps(row), "说明从输入字段到截面排名的路径。"),
        ("组合构建", logic.get("portfolio_construction") or _logic_portfolio_construction(spec), "说明目标持仓数量、目标敞口和现金处理。"),
        ("调仓与成交", logic.get("rebalance_rule") or _logic_rebalance_rule(spec), "说明信号日、下单日和成交日的时间关系。"),
        ("退出与风控", logic.get("exit_rule") or _logic_exit_rule(spec), "说明持仓触发风险时如何离场。"),
        ("风险预算", logic.get("risk_budget") or _logic_risk_budget(spec), "说明回撤控制主要来自哪里。"),
    ]
    body = [
        "<tr>"
        + f"<td>{escape(label)}</td>"
        + f"<td>{escape(_cell(value))}</td>"
        + f"<td>{escape(note)}</td>"
        + "</tr>"
        for label, value, note in values
    ]
    return _table(["项目", "当前策略逻辑", "阅读提示"], body)


def _logic_filters_from_spec(spec: Dict[str, Any]) -> List[str]:
    controls = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    filters = []
    min_price = controls.get("min_price")
    if min_price is not None:
        filters.append(f"价格 >= {_fmt(min_price)}")
    min_adv = controls.get("min_adv_value")
    if min_adv is not None:
        filters.append(f"20日均成交额 >= {_fmt(min_adv)}")
    if controls.get("stock_trend_window"):
        filters.append(f"个股收盘价不低于 {controls.get('stock_trend_window')} 日均线")
    if not filters:
        filters = ["按 StrategySpec 的可交易、状态和字段完整性约束过滤"]
    return filters


def _logic_portfolio_construction(spec: Dict[str, Any]) -> str:
    controls = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    max_positions = controls.get("max_positions") or spec.get("max_positions") or "StrategySpec"
    exposure = controls.get("target_exposure")
    if exposure is not None:
        return f"目标持仓 {max_positions} 只，组合总目标敞口 {_pct(exposure)}，每只目标权重约为总敞口 / 持仓数，剩余资金保留现金。"
    return f"目标持仓 {max_positions} 只；具体权重按策略实现或风险配置决定。"


def _logic_rebalance_rule(spec: Dict[str, Any]) -> str:
    frequency = spec.get("rebalance_frequency") or "StrategySpec 未记录"
    lag = spec.get("execution_lag_days") or 1
    return f"{frequency}；收盘后生成信号，新订单按 execution_lag={lag} 在后续交易日执行。"


def _logic_exit_rule(spec: Dict[str, Any]) -> str:
    controls = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    pieces = ["持仓每日先做风险检查，触发不可交易、状态异常、价格或流动性规则时提交退出订单"]
    if controls.get("market_timing_symbol"):
        pieces.append(f"市场风控标的 {controls.get('market_timing_symbol')} 触发 risk-off 时降低或清空风险敞口")
    return "；".join(pieces) + "。"


def _logic_risk_budget(spec: Dict[str, Any]) -> str:
    controls = spec.get("risk_controls") if isinstance(spec.get("risk_controls"), dict) else {}
    exposure = controls.get("target_exposure")
    max_positions = controls.get("max_positions")
    min_adv = controls.get("min_adv_value")
    parts = []
    if exposure is not None:
        parts.append(f"总敞口 {_pct(exposure)}")
    if max_positions is not None:
        parts.append(f"分散到 {max_positions} 只")
    if min_adv is not None:
        parts.append(f"流动性下限 {_fmt(min_adv)}")
    return "；".join(parts) if parts else "风险预算来自 StrategySpec 和 Backtester 的执行约束。"


def _strategy_execution_logic_contract(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次没有可展示的策略执行逻辑。</p>"
    return "\n".join(
        [
            '<div class="execution-logic">',
            "<h4>信号详细说明</h4>",
            _strict_signal_detail_table(data, rows),
            "<h4>每日运行步骤</h4>",
            _daily_execution_steps_table(data, rows),
            "<h4>执行约束摘要</h4>",
            _strict_execution_constraint_table(data, rows),
            "</div>",
        ]
    )


def _daily_execution_steps_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    constraints = strict.get("constraints") or {}
    t_plus_1 = "T+1 延迟成交" if constraints.get("t_plus_1", True) else "按回测配置执行"
    steps = [
        ("1", "开盘前调用策略 on_before_trading（若策略实现），只允许做状态检查或预计算。", "不能使用当日收盘后才知道的信息。"),
        ("2", "Backtester 加载当日 OHLCV，并先执行上一交易日收盘后生成的 deferred orders。", f"成交使用当日开盘价，执行路径包含 {t_plus_1}、涨跌停、停牌、手数、现金和成交量限制。"),
        ("3", "将当日 bar 批量喂给策略 on_data_batch；无批量 hook 时回落到逐条 on_data。", "策略只把当日及历史数据写入内部缓存，不在此处使用未来价格。"),
        ("4", "按当日收盘价更新持仓市值、组合 NAV、现金和风险快照。", "这里完成估值，不提前成交新信号。"),
        ("5", "收盘后调用 on_after_trading，策略根据截至当日收盘的数据计算信号、目标标的和目标仓位。", "这是信号日 t；所有新订单进入 deferred order 队列。"),
        ("6", "下一个交易日开盘执行 t 日订单，并记录成交、拒单、佣金、滑点、换手和容量诊断。", "这是成交日 t+1；报告里的交易成本和执行约束来自真实 Backtester 流水。"),
        ("7", "日终记录 equity curve、年度收益日历、回撤、持仓暴露和成交诊断。", "这些结构化结果驱动本页所有严格回测指标。"),
    ]
    body = [
        "<tr>"
        + f"<td>{escape(number)}</td>"
        + f"<td>{escape(action)}</td>"
        + f"<td>{escape(boundary)}</td>"
        + "</tr>"
        for number, action, boundary in steps
    ]
    return _table(["步骤", "每日动作", "信息边界 / 执行约束"], body)


def _strict_signal_detail_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    logic = spec.get("strategy_logic") if isinstance(spec.get("strategy_logic"), dict) else {}
    formula = str(spec.get("signal_formula_key") or "")
    lookback = spec.get("lookback_days") or "StrategySpec"
    horizon = spec.get("horizon_days") or "StrategySpec"
    lag = spec.get("execution_lag_days") or 1
    fields = _join_text(spec.get("required_fields") or [])
    fallback = spec.get("fallback_symbol") or spec.get("cash_symbol") or "未设置"
    values = [
        ("信号公式", formula or "StrategySpec declared signal", "用于追溯报告解释和策略实现。"),
        ("核心假设", logic.get("core_idea") or _strict_signal_plaintext(row), "说明为什么这个信号可能产生收益。"),
        ("交易范围", logic.get("universe") or spec.get("universe") or "StrategySpec 未记录", "说明策略在哪个标的池内做选择。"),
        ("输入字段", fields or "StrategySpec 未列出", "缺少字段时不能静默通过研究结论。"),
        ("入选过滤", _join_text(logic.get("entry_filters") or _logic_filters_from_spec(spec)), "这里必须展示状态、流动性、质量控制等所有买入前过滤。"),
        ("排序/信号", logic.get("ranking_rule") or _signal_construction_steps(row), "说明从输入字段到截面排名的路径。"),
        ("组合构建", logic.get("portfolio_construction") or _logic_portfolio_construction(spec), "说明目标持仓数量、目标敞口和现金处理。"),
        ("调仓与信号时点", logic.get("rebalance_rule") or _logic_rebalance_rule(spec), "说明信号日、下单日和成交日的时间关系。"),
        ("退出/风控信号", logic.get("exit_rule") or _logic_exit_rule(spec), "说明持仓触发风险时如何离场。"),
        ("风险预算", logic.get("risk_budget") or _logic_risk_budget(spec), "说明回撤控制主要来自哪里。"),
        ("预测方向", _prediction_direction(row), "解释信号值越高或越低时代表的预期收益方向。"),
        ("时间结构", f"lookback={lookback}; horizon={horizon}; execution_lag={lag}", "信号日与成交日分离，避免 look-ahead。"),
        ("防御/空仓腿", str(fallback), "候选不足、风险触发或信号不达标时使用的退路。"),
    ]
    body = [
        "<tr>"
        + f"<td>{escape(label)}</td>"
        + f"<td>{escape(_cell(value))}</td>"
        + f"<td>{escape(note)}</td>"
        + "</tr>"
        for label, value, note in values
    ]
    return _table(["项目", "内容", "说明"], body)


def _strict_signal_explanation_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    formula = str(spec.get("signal_formula_key") or "")
    lookback = spec.get("lookback_days") or "StrategySpec"
    horizon = spec.get("horizon_days") or "StrategySpec"
    lag = spec.get("execution_lag_days") or 1
    fields = _join_text(spec.get("required_fields") or [])
    fallback = spec.get("fallback_symbol") or spec.get("cash_symbol") or "未设置"
    rows_data = [
        ("信号公式", formula or "StrategySpec declared signal", "报告中的信号解释必须能追溯到 StrategySpec 或策略实现。"),
        ("信号含义", _strict_signal_plaintext(row), "把公式翻译成交易含义，避免只展示代码名。"),
        ("构造步骤", _signal_construction_steps(row), "说明从输入字段到排序/目标持仓的完整路径。"),
        ("预测方向", _prediction_direction(row), "解释信号值越高或越低时代表的预期收益方向。"),
        ("时间结构", f"lookback={lookback}; horizon={horizon}; execution_lag={lag}", "信号日与成交日分离，避免 look-ahead。"),
        ("字段依赖", fields or "StrategySpec 未列出", "缺少字段时不能静默通过研究结论。"),
        ("防御/空仓腿", str(fallback), "候选不足、风险触发或信号不达标时使用的退路。"),
    ]
    body = [
        "<tr>"
        + f"<td>{escape(label)}</td>"
        + f"<td>{escape(_cell(value))}</td>"
        + f"<td>{escape(note)}</td>"
        + "</tr>"
        for label, value, note in rows_data
    ]
    return _table(["项目", "内容", "说明"], body)


def _strict_signal_plaintext(row: Dict[str, Any]) -> str:
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    logic = spec.get("strategy_logic") if isinstance(spec.get("strategy_logic"), dict) else {}
    if logic.get("core_idea"):
        return str(logic.get("core_idea"))
    formula = str(spec.get("signal_formula_key") or "").lower()
    strategy_type = str(spec.get("strategy_type") or "").lower()
    fallback = str(spec.get("fallback_symbol") or spec.get("cash_symbol") or "防御资产")
    if "qixing" in formula:
        return (
            "七星高照日线版信号为 24日加权对数回归年化收益 × R²：年化斜率收益衡量趋势强度，"
            "R²衡量趋势拟合质量；分数越高，代表该 ETF/LOF 的趋势越强且越稳定。"
            f"通过成交量/流动性过滤和止损检查后，只持有最高正分标的；候选不足或触发风险时切换到 {fallback}。"
        )
    if "wufu" in formula or strategy_type == "etf_momentum_rotation":
        return (
            "ETF/LOF 动量轮动信号使用近期后复权价格趋势强度和趋势拟合质量排序；"
            f"分数为正且通过流动性/风险过滤时持有最强标的，否则切换到 {fallback}。"
        )
    if formula == "joinquant_small_cap_low_price_factor":
        return "低价小市值信号先限定可交易、非 ST、未停牌且价格处于低价区间的股票，再优先选择市值更小的标的。"
    if formula == "joinquant_small_cap_size_factor":
        return "小市值信号将市值作为核心截面排序变量，市值越小信号越高，用于捕捉小盘风格暴露。"
    if formula == "ashare_small_cap_guarded_size_factor":
        return "小市值防护基线在全 A 股中先排除 ST、停牌、非上市状态、低价、低流动性和缺失市值的股票，再按当前可见市值从小到大排序；市值越小越优先进入组合。"
    if formula.startswith("worldquant_alpha_"):
        return "WorldQuant 因子按公开公式构造截面信号，并在 A 股 long-only 约束下只选择高分端。"
    if "momentum" in formula:
        return "动量信号认为近期强势标的存在趋势延续，信号值越高越优先进入目标组合。"
    if "reversal" in formula or "mean" in formula:
        return "反转信号认为偏离近期均值或过度下跌的标的存在均值回归机会，按反转强度排序。"
    return f"策略使用 {formula or 'StrategySpec declared signal'} 生成截面信号，并按报告声明方向选择目标组合。"


def _strict_execution_constraint_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    constraints = strict.get("constraints") or {}
    values = [
        ("成交延迟", "T+1" if constraints.get("t_plus_1", True) else "按配置", "信号在 t 日收盘后生成，t+1 开盘尝试成交。"),
        ("手数约束", f"CN lot size={constraints.get('cn_lot_size', 100)}", "A 股/ETF 买入按整手约束；卖出按引擎可卖数量处理。"),
        ("滑点", f"{constraints.get('slippage_bps', 5)} bps", "市场单成交价按方向加入固定滑点；若有冲击模型则继续调整。"),
        ("佣金", _join_text(constraints.get("commission") or {}), "股票与 ETF/LOF 费率按品种路由。"),
        ("成交量/涨跌停/停牌", str(constraints.get("volume_limit") or "由 Backtester execution diagnostics 记录"), "无法成交或超约束的订单会进入诊断而不是强制成交。"),
    ]
    model = constraints.get("execution_cost_model")
    if model:
        values.append(("冲击成本模型", _execution_cost_model_text(model), "小市值或容量敏感策略使用更严格的成交冲击假设。"))
    body = [
        "<tr>"
        + f"<td>{escape(label)}</td>"
        + f"<td>{escape(_cell(value))}</td>"
        + f"<td>{escape(note)}</td>"
        + "</tr>"
        for label, value, note in values
    ]
    return _table(["约束", "当前口径", "说明"], body)


def _backtest_config_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["项目", "取值", "说明"],
            "本轮未运行严格 Backtester",
            "信号验证失败后流水线停止；不得展示历史残留回测指标作为本轮结论。",
        )
    constraints = strict.get("constraints") or {}
    guard = constraints.get("delisting_risk_guard") or {}
    guard_text = (
        f"启用；最低价格 {_fmt(guard.get('min_trade_price'))}；"
        f"{int(guard.get('liquidity_lookback') or 0)} 日均成交额 >= {_fmt(guard.get('min_avg_turnover'))}"
        if guard.get("enabled")
        else "未启用"
    )
    position_pct = _safe_float(constraints.get("strategy_max_position_pct"))
    max_positions = _safe_int(constraints.get("strategy_max_positions")) or 0
    if position_pct is not None and position_pct > 0:
        target_exposure = _pct(position_pct)
    elif max_positions > 0:
        target_exposure = f"按 {max_positions} 只等权目标持仓"
    else:
        target_exposure = "未记录"
    rows_data = [
        ("回测区间", strict.get("period") or "未记录", "必须与数据覆盖一致"),
        ("初始资金", strict.get("initial_cash") or metrics.get("initial_cash") or "500000 CNY", "A 股示例默认 500000 CNY"),
        ("默认目标总仓位", target_exposure, "研究生成策略默认满仓；按持仓数等权分配"),
        ("调仓频率", strict.get("rebalance_frequency") or "daily signal with holding horizon gate", "影响换手"),
        ("退市风险护栏", guard_text, "买入过滤低价/低流动性/非上市状态，持仓风险每日尝试退出"),
        ("滑点", f"{constraints.get('slippage_bps', 5)} bps", "默认配置"),
        ("执行成本模型", _execution_cost_model_text(constraints.get("execution_cost_model")), "小市值策略不应只依赖固定滑点"),
        ("佣金", _commission_text(constraints.get("commission")), "股票按 A 股费率；ETF/LOF 按 fund_percent/fund_min_per_order 且不收股票印花税"),
        ("T+1", "启用" if constraints.get("t_plus_1", True) else "未启用", "当日买入不可卖出"),
        ("100 股手数", "启用" if constraints.get("cn_lot_size", 100) else "未启用", "A 股下单约束"),
        ("成交量限制", str(constraints.get("volume_limit") or "Backtester diagnostics"), "记录 volume_limited_trades"),
        ("涨跌停拒单", str(constraints.get("price_limits") or "启用"), "记录 limit_rejected_orders"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(_cell(value))}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["项目", "取值", "说明"], body)


def _execution_cost_model_text(model: Any) -> str:
    if not isinstance(model, dict) or not model.get("enabled"):
        return "disabled"
    parts = [str(model.get("name") or "execution_cost_model")]
    if model.get("tick_size") is not None:
        parts.append(f"tick_size={_fmt(model.get('tick_size'))}")
    if model.get("half_spread_ticks") is not None:
        parts.append(f"half_spread_ticks={_fmt(model.get('half_spread_ticks'))}")
    if model.get("max_participation_rate") is not None:
        parts.append(f"max_participation_rate={_pct(model.get('max_participation_rate'))}")
    if model.get("impact_coefficient") is not None:
        parts.append(f"impact_coefficient={_fmt(model.get('impact_coefficient'))}")
    if model.get("volatility_fallback") is not None:
        parts.append(f"volatility_fallback={_pct(model.get('volatility_fallback'))}")
    return "; ".join(parts)


def _core_performance_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["Metric", "策略", "000300", "超额/说明"],
            "本轮未运行严格 Backtester",
            "信号验证失败，未进入可比较的成本后回测阶段。",
        )
    metrics = strict.get("metrics") or {}
    benchmark = strict.get("benchmark") or {}
    rows_data = [
        ("CAGR", _pct(metrics.get("cagr")), _pct(benchmark.get("benchmark_cagr")), _pct(_excess(metrics.get("cagr"), benchmark.get("benchmark_cagr")))),
        (
            "Total Return",
            _pct(metrics.get("total_return")),
            _pct(_first_present(benchmark, "benchmark_total_return", "benchmark_return")),
            _pct(_excess(metrics.get("total_return"), _first_present(benchmark, "benchmark_total_return", "benchmark_return"))),
        ),
        ("Sharpe", _fmt(metrics.get("sharpe")), _fmt(benchmark.get("benchmark_sharpe")), "必须成本后"),
        ("Sortino", _fmt(metrics.get("sortino")), _fmt(benchmark.get("benchmark_sortino")), "下行风险调整"),
        ("Max Drawdown", _pct(metrics.get("max_drawdown_pct")), _pct(benchmark.get("benchmark_max_drawdown_pct")), "风险底线"),
        ("Calmar Ratio", _fmt(_first_present(metrics, "calmar_ratio", "calmar")), _fmt(_first_present(benchmark, "benchmark_calmar_ratio", "benchmark_calmar")), "CAGR / |Max Drawdown|"),
        ("Win Rate", _pct(metrics.get("win_rate")), "-", "按交易统计"),
        ("Profit Factor", _fmt(metrics.get("profit_factor")), "-", "总盈利 / 总亏损"),
        ("Payoff Ratio", _fmt(metrics.get("payoff_ratio")), "-", "平均盈利 / 平均亏损"),
        ("Expectancy", _fmt(metrics.get("expectancy")), "-", "单笔交易期望"),
        ("Gain/Pain", _fmt(metrics.get("gain_to_pain_ratio")), "-", "收益痛苦比"),
        ("Tail Ratio", _fmt(metrics.get("tail_ratio")), "-", "右尾 / 左尾"),
        ("Ulcer Index", _fmt(metrics.get("ulcer_index")), "-", "回撤深度与持续性的复合惩罚"),
        ("Recovery Factor", _fmt(metrics.get("recovery_factor")), "-", "净收益 / 最大回撤"),
        ("Avg Trade Duration", _fmt(metrics.get("avg_trade_duration_days")), "-", "平均持仓天数"),
        ("Total Trades", str(metrics.get("total_trades") or "n/a"), "-", "含拒单和成交诊断"),
        ("Information Ratio", _fmt(benchmark.get("information_ratio")), "-", "相对 000300"),
        ("Up / Down Capture", f"{_fmt(benchmark.get('up_capture'))} / {_fmt(benchmark.get('down_capture'))}", "-", "基准上涨/下跌阶段捕获率"),
        ("Beta / Alpha", f"{_fmt(benchmark.get('beta'))} / {_pct(benchmark.get('alpha'))}", "-", "基准归因"),
    ]
    body = "".join(
        f"<tr><td>{escape(metric)}</td><td>{escape(strategy)}</td><td>{escape(bench)}</td><td>{escape(note)}</td></tr>"
        for metric, strategy, bench, note in rows_data
    )
    return _table(["Metric", "策略", "000300", "超额/说明"], body)


def _trade_cost_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "本轮未运行交易成本诊断",
            "信号验证失败，未生成成交、拒单、手续费或容量诊断。",
        )
    diagnostics = strict.get("diagnostics") or {}
    suspended_symbols = list(diagnostics.get("final_suspended_symbols") or [])
    suspended_note = "最终停牌/冻结持仓按最后有效价估值"
    if suspended_symbols:
        suspended_note += "；样例：" + ", ".join(str(symbol) for symbol in suspended_symbols[:8])
    rows_data = [
        ("total_commission", _fmt(diagnostics.get("total_commission")), "总交易成本"),
        ("cost_drag_pct", _pct(_cost_drag_value(diagnostics)), "成本拖累"),
        ("lot_adjusted_trades", _int_cell(diagnostics.get("lot_adjusted_trades")), "因 100 股手数调整的交易"),
        ("t1_rejected_sells", _int_cell(diagnostics.get("t1_rejected_sells")), "T+1 拒绝卖出"),
        ("limit_rejected_orders", _int_cell(diagnostics.get("limit_rejected_orders")), "涨跌停拒单"),
        ("insufficient_cash_rejected_orders", str(_insufficient_cash_rejected_orders(diagnostics)), "现金不足拒单"),
        ("volume_limited_trades", _int_cell(diagnostics.get("volume_limited_trades")), "成交量限制"),
        ("risk_skipped_orders", _int_cell(diagnostics.get("risk_skipped_orders")), "风控跳过订单"),
        ("final_suspended_holding_nav", _money(diagnostics.get("final_suspended_holding_nav")), suspended_note),
        ("final_suspended_holding_nav_pct", _pct(diagnostics.get("final_suspended_holding_nav_pct_of_final_nav")), "最终 frozen 持仓占期末 NAV"),
        ("frozen_zero_final_nav", _money(diagnostics.get("frozen_zero_final_nav")), "退市/frozen 持仓归零压力下的期末 NAV"),
        ("frozen_zero_cagr", _pct(diagnostics.get("frozen_zero_cagr")), "退市/frozen 持仓归零压力下的 CAGR"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _turnover_exposure_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "本轮未运行换手与持仓暴露诊断",
            "strict Backtester 未执行，无法记录成交额、现金占比和持仓数量。",
        )
    turnover = strict.get("turnover") or {}
    exposure = strict.get("exposure") or {}
    if not turnover and not exposure:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "未记录换手与持仓暴露",
            "报告缺少 trades 或 daily exposure snapshots。",
        )
    rows_data = [
        ("gross_traded_value", _money(turnover.get("gross_traded_value")), "双边总成交额"),
        ("one_way_traded_value", _money(turnover.get("one_way_traded_value")), "按买卖较小侧估算的一边成交额"),
        ("annual_gross_turnover", _pct(turnover.get("annual_gross_turnover")), "双边年化换手"),
        ("annual_one_way_turnover", _pct(turnover.get("annual_one_way_turnover")), "单边年化换手"),
        ("avg_daily_traded_value", _money(turnover.get("avg_daily_traded_value")), "有交易日平均成交额"),
        ("max_daily_traded_value", _money(turnover.get("max_daily_traded_value")), "单日最高成交额"),
        ("avg_position_count", _fmt(exposure.get("avg_position_count")), "日均持仓只数"),
        ("position_count_range", f"{_fmt(exposure.get('min_position_count'))} - {_fmt(exposure.get('max_position_count'))}", "最少/最多持仓只数"),
        ("avg_gross_exposure_pct", _pct(exposure.get("avg_gross_exposure_pct")), "日均总持仓市值 / NAV"),
        ("avg_cash_pct", _pct(exposure.get("avg_cash_pct")), "日均现金 / NAV"),
        ("max_position_weight", _pct(exposure.get("max_position_weight")), "单票最大权重"),
        ("p95_max_position_weight", _pct(exposure.get("p95_max_position_weight")), "单票权重 95 分位"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _capacity_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "本轮未运行容量诊断",
            "strict Backtester 未执行，无法记录 ADV 参与率。",
        )
    capacity = strict.get("capacity") or {}
    if not capacity:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "未记录容量诊断",
            "报告缺少 execution observations；旧报告需重跑后才会填充。",
        )
    rows_data = [
        ("executed_orders", str(capacity.get("executed_orders") or 0), "实际成交订单数"),
        ("avg_adv_participation", _pct(capacity.get("avg_adv_participation")), "平均 ADV 金额参与率"),
        ("p95_adv_participation", _pct(capacity.get("p95_adv_participation")), "ADV 金额参与率 95 分位"),
        ("max_adv_participation", _pct(capacity.get("max_adv_participation")), "单笔最大 ADV 金额参与率"),
        ("p95_volume_participation", _pct(capacity.get("p95_volume_participation")), "成交量参与率 95 分位"),
        ("max_volume_participation", _pct(capacity.get("max_volume_participation")), "单笔最大成交量参与率"),
        ("p95_trade_notional", _money(capacity.get("p95_trade_notional")), "单笔成交额 95 分位"),
        ("max_trade_notional", _money(capacity.get("max_trade_notional")), "单笔最大成交额"),
        ("estimated_capacity_at_1pct_adv_p95", _money(capacity.get("estimated_capacity_at_1pct_adv_p95")), "以 p95 ADV 参与率不超过 1% 反推资金容量"),
        ("estimated_capacity_at_1pct_adv_max", _money(capacity.get("estimated_capacity_at_1pct_adv_max")), "以最大 ADV 参与率不超过 1% 反推资金容量"),
        ("max_impact_bps", _fmt(capacity.get("max_impact_bps")), "执行冲击模型记录的最大 bps；禁用模型时应接近 0"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _guard_attribution_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "本轮未运行退市护栏归因",
            "strict Backtester 未执行，无法记录护栏命中原因。",
        )
    guard = strict.get("guard_diagnostics") or {}
    constraints = (strict.get("constraints") or {}).get("delisting_risk_guard") or {}
    diagnostics = strict.get("diagnostics") or {}
    entry_rejections = guard.get("entry_rejections") or {}
    exit_triggers = guard.get("exit_triggers") or {}
    enabled = bool(guard.get("enabled", constraints.get("enabled", False)))
    rows_data = [
        ("enabled", "是" if enabled else "否", "是否启用策略层退市风险护栏"),
        ("parameters", _join_text(guard.get("parameters") or constraints), "护栏阈值"),
        ("entry_rejections_total", str(_sum_count_values(entry_rejections)), "买入候选被护栏过滤次数"),
        ("entry_rejections_top", _count_summary(entry_rejections), "买入过滤原因 Top"),
        ("exit_triggers_total", str(_sum_count_values(exit_triggers)), "持仓触发风险退出次数"),
        ("exit_triggers_top", _count_summary(exit_triggers), "退出触发原因 Top"),
        ("submission_rejected", _int_cell(diagnostics.get("submission_rejected")), "提交阶段拒单；可用于衡量护栏后仍不可交易的冲击"),
        ("risk_skipped_orders", _int_cell(diagnostics.get("risk_skipped_orders")), "风控跳过订单"),
        ("limit_rejected_orders", _int_cell(diagnostics.get("limit_rejected_orders")), "涨跌停拒单"),
        ("discarded_orders", _int_cell(diagnostics.get("discarded_orders")), "执行阶段丢弃订单"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(_cell(value))}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _drawdown_episode_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    episodes = strict.get("drawdown_episodes") or []
    if not episodes:
        return _not_run_table(
            ["开始", "谷底", "修复", "深度", "持续天数"],
            "未记录回撤过程",
            "报告缺少 equity curve 或回撤 episode 诊断。",
        )
    body = "".join(
        "<tr>"
        + f"<td>{escape(str(item.get('start') or ''))}</td>"
        + f"<td>{escape(str(item.get('trough') or ''))}</td>"
        + f"<td>{escape(str(item.get('recovery') or '未修复'))}</td>"
        + f"<td>{escape(_pct(item.get('drawdown_pct')))}</td>"
        + f"<td>{escape(str(item.get('duration_days') or 0))}</td>"
        + "</tr>"
        for item in episodes
        if isinstance(item, dict)
    )
    return _table(["开始", "谷底", "修复", "深度", "持续天数"], body)


def _trade_distribution_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    distribution = strict.get("trade_distribution") or {}
    if not distribution:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "未记录交易分布",
            "报告缺少成交明细，无法拆分 PnL、收益率和持仓天数分布。",
        )
    rows_data = [
        ("sell_trades", str(distribution.get("sell_trades") or 0), "以卖出成交统计闭合交易"),
        ("avg_pnl", _money(distribution.get("avg_pnl")), "平均单笔 PnL"),
        ("median_pnl", _money(distribution.get("median_pnl")), "单笔 PnL 中位数"),
        ("p05_pnl", _money(distribution.get("p05_pnl")), "单笔 PnL 5 分位"),
        ("p95_pnl", _money(distribution.get("p95_pnl")), "单笔 PnL 95 分位"),
        ("max_win", _money(distribution.get("max_win")), "最大盈利单笔"),
        ("max_loss", _money(distribution.get("max_loss")), "最大亏损单笔"),
        ("avg_return", _pct(distribution.get("avg_return")), "平均单笔收益率"),
        ("median_return", _pct(distribution.get("median_return")), "单笔收益率中位数"),
        ("avg_duration_days", _fmt(distribution.get("avg_duration_days")), "平均持仓天数"),
        ("p95_duration_days", _fmt(distribution.get("p95_duration_days")), "持仓天数 95 分位"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _rolling_regime_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    rolling = strict.get("rolling_stability") or {}
    regime = strict.get("regime_breakdown") or {}
    if not rolling and not regime:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "未记录滚动稳定性与市场阶段",
            "报告缺少 equity curve 或 benchmark curve。",
        )
    rows_data = []
    for key, label in (
        ("rolling_1y_sharpe", "rolling_1y_sharpe"),
        ("rolling_3y_sharpe", "rolling_3y_sharpe"),
        ("rolling_1y_information_ratio", "rolling_1y_information_ratio"),
        ("rolling_1y_beta", "rolling_1y_beta"),
    ):
        summary = rolling.get(key) or {}
        if summary:
            rows_data.append((
                label,
                f"latest={_fmt(summary.get('latest'))}; median={_fmt(summary.get('median'))}; min={_fmt(summary.get('min'))}; max={_fmt(summary.get('max'))}",
                f"observations={summary.get('observations') or 0}",
            ))
    if regime:
        best_year = regime.get("best_year") or {}
        worst_year = regime.get("worst_year") or {}
        rows_data.extend([
            ("positive_years", f"{regime.get('positive_years') or 0}/{regime.get('total_years') or 0}", "年度收益为正的年份数"),
            ("outperform_years", str(regime.get("outperform_years") or 0), "跑赢 benchmark 的年份数"),
            ("benchmark_up_regime", _pct(regime.get("avg_return_when_benchmark_up")), "benchmark 上涨年份的策略平均收益"),
            ("benchmark_up_excess", _pct(regime.get("avg_excess_when_benchmark_up")), "benchmark 上涨年份的平均超额"),
            ("benchmark_down_regime", _pct(regime.get("avg_return_when_benchmark_down")), "benchmark 下跌年份的策略平均收益"),
            ("benchmark_down_excess", _pct(regime.get("avg_excess_when_benchmark_down")), "benchmark 下跌年份的平均超额"),
            ("best_year", f"{best_year.get('year') or 'n/a'}: {_pct(best_year.get('return'))}", "最佳年度"),
            ("worst_year", f"{worst_year.get('year') or 'n/a'}: {_pct(worst_year.get('return'))}", "最差年度"),
        ])
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(_cell(value))}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _cost_decomposition_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    cost = strict.get("cost_decomposition") or {}
    if not cost:
        return _not_run_table(
            ["诊断项", "数值", "解释"],
            "未记录成本口径拆分",
            "报告缺少 gross PnL、net PnL 或显式交易成本字段。",
        )
    diagnostics = strict.get("diagnostics") or {}
    rejection_counts = diagnostics.get("rejection_counts") or {}
    rejection_total = (
        _safe_int(diagnostics.get("limit_rejected_orders")) or 0
    ) + (
        _safe_int(diagnostics.get("submission_rejected")) or 0
    ) + (
        _safe_int(diagnostics.get("risk_skipped_orders")) or 0
    ) + (
        _safe_int(diagnostics.get("discarded_orders")) or 0
    ) + (
        _safe_int(diagnostics.get("expired_orders")) or 0
    )
    rows_data = [
        ("gross_pnl_before_explicit_cost", _money(cost.get("gross_pnl_before_explicit_cost")), "显式佣金税费前交易毛 PnL"),
        ("net_pnl_after_cost", _money(cost.get("net_pnl_after_cost")), "期末 NAV - 初始资金"),
        ("explicit_commission_tax", _money(cost.get("explicit_commission_tax")), "total_commission"),
        ("explicit_cost_pct_initial_cash", _pct(cost.get("explicit_cost_pct_initial_cash")), "显式成本 / 初始资金"),
        ("explicit_cost_pct_gross_pnl", _pct(cost.get("explicit_cost_pct_gross_pnl")), "显式成本 / |毛 PnL|"),
        ("rejection_total", str(rejection_total), "涨跌停、提交、风控、丢弃、过期订单合计"),
        ("rejection_breakdown", _count_summary(rejection_counts), "底层拒单原因"),
        ("slippage_impact_note", str(cost.get("slippage_impact_note") or ""), "成交价内含滑点/冲击，显式成本只含佣金税费"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(_cell(value))}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _data_quality_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return _not_run_table(
            ["审计项", "数值", "解释"],
            "本轮未运行数据完整性审计",
            "strict Backtester 未执行，无法比较 OHLC/status/daily_basic 覆盖。",
        )
    audit = ((strict.get("data_quality") or {}).get("survivorship_audit") or {})
    if not audit:
        return _not_run_table(
            ["审计项", "数值", "解释"],
            "未记录 survivorship audit",
            "报告缺少 daily_basic 与 OHLC/status universe 的覆盖对账。",
        )
    if audit.get("kind") == "etf_metadata_survivorship_audit":
        samples = audit.get("bar_symbols_missing_fund_meta_sample") or []
        sample_text = ", ".join(str(item.get("symbol")) for item in samples[:8] if isinstance(item, dict) and item.get("symbol"))
        rows_data = [
            ("kind", str(audit.get("kind") or ""), "ETF survivorship audit 类型"),
            ("material", "是" if audit.get("material") else "否", audit.get("reason") or "是否足以影响 strict 结论"),
            ("fund_bar_symbols", str(audit.get("fund_bar_symbols") or audit.get("etf_bar_symbols") or 0), "ETF/LOF 日线表中本回测期出现过的 symbol 数"),
            ("fund_meta_etf_symbols", str(audit.get("fund_meta_etf_symbols") or 0), "基金元数据表中标记为 ETF 的 symbol 数"),
            ("bar_symbols_missing_fund_meta", str(audit.get("bar_symbols_missing_fund_meta") or 0), "有 ETF/LOF 日线但缺少基金元数据的 symbol 数"),
            ("fund_bar_symbols_with_non_etf_metadata", str(audit.get("fund_bar_symbols_with_non_etf_metadata") or 0), "ETF/LOF 日线表中元数据标记为非 ETF 的 symbol 数"),
            ("fund_meta_delisted_symbols", str(audit.get("fund_meta_delisted_symbols") or 0), "基金元数据中带 delist_date 的 ETF 数"),
            ("universe_registry_version", str(audit.get("universe_registry_version") or "-"), "ETF 类别注册表版本；新增类别必须人工审计注册"),
            ("registered_universe_symbol_count", str(audit.get("registered_universe_symbol_count") or 0), "本策略注册 ETF 代表池 symbol 数"),
            ("registered_universe_symbols_with_bars", str(audit.get("registered_universe_symbols_with_bars") or 0), "回测窗口内有日线数据的注册 ETF 数"),
            ("registered_universe_missing_bar_count", str(audit.get("registered_universe_missing_bar_count") or 0), "注册 ETF 中缺少回测窗口日线的 symbol 数"),
            ("bar_symbols_missing_fund_meta_sample", sample_text or "-", "样例 bar-only symbol；不会自动扩入注册策略候选池"),
        ]
        body = "".join(
            f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(_cell(note))}</td></tr>"
            for item, value, note in rows_data
        )
        return _table(["审计项", "数值", "解释"], body)
    samples = audit.get("sample_missing_symbols") or []
    sample_text = ", ".join(str(item.get("symbol")) for item in samples[:8] if isinstance(item, dict) and item.get("symbol"))
    rows_data = [
        ("material", "是" if audit.get("material") else "否", audit.get("reason") or "是否足以影响 strict 结论"),
        ("daily_basic_symbols", str(audit.get("daily_basic_symbols") or 0), "daily_basic 中的历史 symbol 数"),
        ("ohlc_symbols", str(audit.get("ohlc_symbols") or 0), "OHLC 可回测 symbol 数"),
        ("daily_basic_not_ohlc_symbols", str(audit.get("daily_basic_not_ohlc_symbols") or 0), "daily_basic 有但 OHLC 缺失的 symbol 数"),
        ("missing_low_price_symbols_excluding_920", str(audit.get("missing_low_price_symbols_excluding_920") or 0), "缺失且曾满足低价条件的非 920 symbol"),
        ("missing_symbols_below_top20_excluding_920", str(audit.get("missing_symbols_below_top20_excluding_920") or 0), "缺失且市值可能进入当前 Top20 小市值阈值的非 920 symbol"),
        ("dates_with_missing_below_top20_excluding_920", str(audit.get("dates_with_missing_below_top20_excluding_920") or 0), "受影响交易日数"),
        ("sample_missing_symbols", sample_text or "-", "样例缺失 symbol"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(_cell(note))}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["审计项", "数值", "解释"], body)


def _equity_curve_chart(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    curves = strict.get("equity_curve") or {}
    raw_strategy = _curve_points(curves.get("strategy"))
    raw_benchmark = _curve_points(curves.get("benchmark"))
    strategy = _downsample_curve_points(_normalize_curve_points(raw_strategy))
    benchmark = _downsample_curve_points(_normalize_curve_points(raw_benchmark))
    if not strategy:
        return "<p>本轮严格 Backtester 未保存 equity curve，无法渲染曲线图。</p>"

    width = 940
    height = 360
    left = 70
    right = 22
    top = 26
    bottom = 52
    plot_width = width - left - right
    plot_height = height - top - bottom

    dates = sorted({date for date, _ in strategy}.union({date for date, _ in benchmark}))
    if len(dates) < 2:
        return "<p>equity curve 样本点不足，无法渲染曲线图。</p>"
    date_pos = {date: idx for idx, date in enumerate(dates)}

    values = [value for _, value in strategy + benchmark]
    y_min = min(values)
    y_max = max(values)
    if abs(y_max - y_min) < 1e-12:
        pad = max(abs(y_max) * 0.05, 1.0)
    else:
        pad = (y_max - y_min) * 0.06
    y_min -= pad
    y_max += pad

    def x_for(date: str) -> float:
        return left + plot_width * date_pos[date] / max(1, len(dates) - 1)

    def y_for(value: float) -> float:
        return top + plot_height * (1.0 - (value - y_min) / (y_max - y_min))

    strategy_path = _svg_path(strategy, x_for, y_for)
    benchmark_path = _svg_path(benchmark, x_for, y_for)
    grid = []
    labels = []
    for i in range(5):
        value = y_min + (y_max - y_min) * i / 4
        y = y_for(value)
        grid.append(f'<line class="chart-grid" x1="{left:.1f}" y1="{y:.1f}" x2="{width - right:.1f}" y2="{y:.1f}" />')
        labels.append(
            f'<text class="chart-label" x="{left - 10:.1f}" y="{y + 4:.1f}" text-anchor="end">{escape(_compact_number(value))}</text>'
        )
    x_labels = [
        f'<text class="chart-label" x="{left:.1f}" y="{height - 16:.1f}" text-anchor="start">{escape(dates[0])}</text>',
        f'<text class="chart-label" x="{width - right:.1f}" y="{height - 16:.1f}" text-anchor="end">{escape(dates[-1])}</text>',
    ]
    benchmark_svg = (
        f'<path class="benchmark-line" fill="none" stroke="#16a34a" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" d="{escape(benchmark_path, quote=True)}" />'
        if benchmark_path
        else ""
    )
    benchmark_legend = (
        '<span><i class="legend-benchmark"></i>Benchmark</span>'
        if benchmark_path
        else '<span class="muted">Benchmark curve unavailable</span>'
    )
    return (
        '<figure class="equity-chart">'
        '<div class="equity-chart-meta">'
        '<span><i class="legend-strategy"></i>策略</span>'
        f"{benchmark_legend}"
        f"<b>策略期末 {_money(raw_strategy[-1][1])}（指数 {_compact_number(strategy[-1][1])}）</b>"
        + (f"<b>Benchmark 期末 {_money(raw_benchmark[-1][1])}（指数 {_compact_number(benchmark[-1][1])}）</b>" if benchmark and raw_benchmark else "")
        + "</div>"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Strict backtest equity curve">'
        + "".join(grid)
        + "".join(labels)
        + "".join(x_labels)
        + f'<line class="chart-axis" x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{height - bottom:.1f}" />'
        + f'<line class="chart-axis" x1="{left:.1f}" y1="{height - bottom:.1f}" x2="{width - right:.1f}" y2="{height - bottom:.1f}" />'
        + benchmark_svg
        + f'<path class="strategy-line" fill="none" stroke="#dc2626" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" d="{escape(strategy_path, quote=True)}" />'
        + "</svg>"
        + "<figcaption>曲线按首日=100 的归一化指数绘制，避免原始金额尺度差压缩线形；期末金额仍来自 strict Backtester 原始账户 NAV，benchmark 按同一初始资金买入并持有 000300。</figcaption>"
        + "</figure>"
    )


def _yearly_return_calendar(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strict = _strict_backtest_for_report(data, row)
    if not strict:
        return "<p>本轮严格 Backtester 未运行，无法生成年度收益日历图。</p>"
    benchmark = strict.get("benchmark") or {}
    curves = strict.get("equity_curve") or {}
    strategy_returns = _yearly_returns_for_report(strict.get("yearly_returns"))
    benchmark_returns = _yearly_returns_for_report(benchmark.get("benchmark_yearly_returns"))
    if not strategy_returns:
        strategy_returns = _yearly_returns_from_curve_points(curves.get("strategy"), strict.get("initial_cash"))
    if not benchmark_returns:
        benchmark_returns = _yearly_returns_from_curve_points(curves.get("benchmark"), strict.get("initial_cash"))
    if not strategy_returns:
        return "<p>本轮 strict Backtester 未保存足够的年度收益或 equity curve，无法生成年度收益日历图。</p>"

    strategy_monthly = _monthly_returns_from_curve_points(curves.get("strategy"), strict.get("initial_cash"))
    benchmark_monthly = _monthly_returns_from_curve_points(curves.get("benchmark"), strict.get("initial_cash"))
    years = sorted(
        set(strategy_returns).union(benchmark_returns),
        key=lambda item: int(item) if str(item).isdigit() else str(item),
    )
    benchmark_symbol = str(benchmark.get("symbol") or _report_benchmark(row))
    cells = []
    for year in years:
        strategy_value = strategy_returns.get(year)
        benchmark_value = benchmark_returns.get(year)
        excess = _excess(strategy_value, benchmark_value)
        monthly_grid = _monthly_return_grid(
            strategy_monthly.get(str(year), {}),
            benchmark_monthly.get(str(year), {}),
            benchmark_symbol,
        )
        cells.append(
            '<details class="return-cell '
            + _return_calendar_class(strategy_value)
            + '">'
            + '<summary class="return-year-summary">'
            + f'<b class="return-year-label">{escape(str(year))}</b>'
            + f'<strong class="return-year-value">{escape(_pct(strategy_value))}</strong>'
            + '<span class="return-year-caption">策略</span>'
            + f'<small class="return-year-meta">{escape(benchmark_symbol)} {_pct(benchmark_value)} · 超额 {_pct(excess)}</small>'
            + "</summary>"
            + monthly_grid
            + "</details>"
        )
    return (
        '<figure class="return-calendar-chart">'
        '<div class="return-calendar">'
        + "".join(cells)
        + "</div>"
        + f"<figcaption>年度收益按 strict Backtester 的策略账户 NAV 计算；benchmark 使用同一初始资金的 {escape(benchmark_symbol)} equity curve。</figcaption>"
        + "</figure>"
    )


def _monthly_return_grid(
    strategy_returns: Dict[str, float],
    benchmark_returns: Dict[str, float],
    benchmark_symbol: str,
) -> str:
    month_cells = []
    for month in range(1, 13):
        month_key = f"{month:02d}"
        strategy_value = strategy_returns.get(month_key)
        benchmark_value = benchmark_returns.get(month_key)
        excess = _excess(strategy_value, benchmark_value)
        month_cells.append(
            '<div class="return-month '
            + _return_calendar_class(strategy_value)
            + '">'
            + '<div class="return-month-head">'
            + f"<span>{month_key}月</span>"
            + "<em>策略</em>"
            + "</div>"
            + f"<strong>{escape(_pct(strategy_value))}</strong>"
            + "<dl>"
            + f"<div><dt>{escape(benchmark_symbol)}</dt><dd>{escape(_pct(benchmark_value))}</dd></div>"
            + f"<div><dt>超额</dt><dd>{escape(_pct(excess))}</dd></div>"
            + "</dl>"
            + "</div>"
        )
    return '<div class="return-month-grid">' + "".join(month_cells) + "</div>"


def _yearly_returns_for_report(value: Any) -> Dict[str, float]:
    result: Dict[str, float] = {}
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                year = item.get("year") or item.get("date")
                ret = _first_present(item, "return", "yearly_return", "strategy_return")
                items.append((year, ret))
    else:
        items = []
    for raw_year, raw_value in items:
        year = str(raw_year or "")[:4]
        if not year.isdigit():
            continue
        number = _safe_float(raw_value)
        if number is not None:
            result[year] = number
    return result


def _monthly_returns_from_curve_points(points: Any, initial_cash: Any = None) -> Dict[str, Dict[str, float]]:
    period_returns = _period_returns_from_curve_points(points, initial_cash, date_len=7)
    result: Dict[str, Dict[str, float]] = {}
    for period, value in period_returns.items():
        year, month = period[:4], period[5:7]
        if year.isdigit() and month.isdigit():
            result.setdefault(year, {})[month] = value
    return result


def _yearly_returns_from_curve_points(points: Any, initial_cash: Any = None) -> Dict[str, float]:
    return _period_returns_from_curve_points(points, initial_cash, date_len=4)


def _period_returns_from_curve_points(points: Any, initial_cash: Any = None, date_len: int = 4) -> Dict[str, float]:
    if not isinstance(points, list):
        return {}
    parsed = []
    for point in points:
        if not isinstance(point, dict):
            continue
        period = str(point.get("date") or "")[:date_len]
        value = _safe_float(point.get("value"))
        if len(period) == date_len and value is not None:
            parsed.append((str(point.get("date") or ""), period, value))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return {}
    result: Dict[str, float] = {}
    previous_close = _safe_float(initial_cash)
    current_period = ""
    first_value = None
    last_value = None
    for _, period, value in parsed:
        if current_period and period != current_period:
            base = previous_close if previous_close is not None and previous_close > 0 else first_value
            if base is not None and base > 0 and last_value is not None:
                result[current_period] = last_value / base - 1.0
            previous_close = last_value
            first_value = value
            last_value = value
            current_period = period
            continue
        if not current_period:
            current_period = period
            first_value = value
        last_value = value
    base = previous_close if previous_close is not None and previous_close > 0 else first_value
    if current_period and base is not None and base > 0 and last_value is not None:
        result[current_period] = last_value / base - 1.0
    return result


def _return_calendar_class(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "neutral"
    if number > 0.0005:
        return "positive"
    if number < -0.0005:
        return "negative"
    return "neutral"


def _walkforward_methodology_contract_table(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    scores, _, _ = _walkforward_scores(row)
    thresholds = _walkforward_thresholds(scores)
    horizon = spec.get("horizon_days") or 5
    lookback = spec.get("lookback_days") or 20
    rows_data = [
        ("train_window", f"{_threshold_int(thresholds, 'train_window_days')} trading days", "用于参数/阈值确认"),
        ("test_window", f"{_threshold_int(thresholds, 'test_window_days')} trading days", "样本外测试区间"),
        ("step", f"{_threshold_int(thresholds, 'step_days')} trading days", "滚动步长"),
        ("purge_gap", f"{_threshold_int(thresholds, 'purge_days', horizon)} trading days", "训练和测试之间剔除重叠信息"),
        ("embargo", f"{_threshold_int(thresholds, 'embargo_days')} trading days", "测试开始前的禁用间隔"),
        ("min_train_observations", f"{_threshold_int(thresholds, 'min_train_observations')} trading days", "低于该训练样本数不生成 split"),
        ("parameter_grid", f"lookback={lookback}; horizon={horizon}; frozen parameters", "若无参数优化，写明 frozen parameters"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["项目", "取值", "解释"], body)


def _walkforward_summary_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    scores, reason, _ = _walkforward_scores(row)
    thresholds = _walkforward_thresholds(scores)
    min_worst = _threshold_float(thresholds, "min_worst_oos_sharpe")
    min_profitable = _threshold_float(thresholds, "min_profitable_splits_pct")
    min_dsr = _threshold_float(thresholds, "min_deflated_sharpe_ratio")
    max_adv = _threshold_float(thresholds, "max_adv_pct")
    total_splits = _first_present(scores, "total_splits", "generated_splits")
    evaluated_splits = _first_present(scores, "evaluated_splits", "n_splits")
    no_trade_splits = scores.get("no_trade_splits")
    rows_data = [
        (
            "total_splits",
            _int_cell(total_splits),
            ">0",
            _min_threshold_verdict(total_splits, 1.0),
            "实际生成的样本外 split 总数",
        ),
        (
            "evaluated_splits",
            _int_cell(evaluated_splits),
            ">0",
            _min_threshold_verdict(evaluated_splits, 1.0),
            "有交易的 OOS split；aggregate/worst/profitable/DSR 只统计这些区间",
        ),
        (
            "no_trade_splits",
            _int_cell(no_trade_splits),
            "excluded from OOS stats",
            "excluded" if _safe_float(no_trade_splits) else "pass",
            "无交易 OOS split 保留明细，但不进入样本外统计分母",
        ),
        (
            "aggregate_oos_sharpe",
            _fmt(scores.get("aggregate_oos_sharpe")),
            ">0 作为均值表现参考；不单独决定 pass",
            _reference_verdict(scores.get("aggregate_oos_sharpe")),
            "有交易 OOS split 的聚合表现",
        ),
        (
            "worst_oos_sharpe",
            _fmt(scores.get("worst_oos_sharpe")),
            f">={_fmt(min_worst)}",
            _min_threshold_verdict(scores.get("worst_oos_sharpe"), min_worst),
            "有交易 OOS split 中的最差样本外窗口；当前仅作 walk-forward 审计，不进入 Go / No-Go checklist",
        ),
        (
            "pct_profitable_splits",
            _pct(scores.get("pct_profitable_splits")),
            f">={_pct(min_profitable)}",
            _min_threshold_verdict(scores.get("pct_profitable_splits"), min_profitable),
            "有交易 OOS split 的赚钱占比；当前仅作 walk-forward 审计，不进入 Go / No-Go checklist",
        ),
        (
            "deflated_sharpe_ratio",
            _fmt(scores.get("deflated_sharpe_ratio")),
            f">={_fmt(min_dsr)}；缺失时不触发 DSR 警告",
            _dsr_threshold_verdict(scores.get("deflated_sharpe_ratio"), min_dsr),
            "调整多重试验后的 Sharpe 可靠性；低于阈值为 warn",
        ),
        (
            "regime_breakdown",
            _cell(scores.get("regime_breakdown") or reason or "未保存分 regime 明细"),
            "bear regime Sharpe >= -0.5000 可避免牛市单一依赖警告",
            _regime_threshold_verdict(scores),
            "分市场状态稳定性；当前不单独决定 pass",
        ),
        (
            "capacity_viability",
            _walkforward_capacity_value(scores),
            f"所有交易可估算成交量且单笔参与率 <={_pct(max_adv)} ADV",
            _capacity_threshold_verdict(scores),
            "walk-forward 容量审计；当前 checklist 使用 strict 回测的 max_adv_participation",
        ),
    ]
    body = "".join(
        f"<tr><td>{escape(metric)}</td><td>{escape(str(value))}</td><td>{escape(threshold)}</td>"
        + f"<td>{_threshold_badge(verdict)}</td><td>{escape(note)}</td></tr>"
        for metric, value, threshold, verdict, note in rows_data
    )
    return _table(["Metric", "数值", "通过阈值", "当前判定", "解释"], body)


def _walkforward_split_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    detail, reason, verdict = _walkforward_scores(row)
    include_maxdd = False
    include_turnover = False
    include_trade_count = False
    split_rows = []
    if isinstance(detail, dict):
        raw_splits = detail.get("splits") or detail.get("split_results") or []
        if isinstance(raw_splits, list):
            for idx, split in enumerate(raw_splits[:12], start=1):
                if not isinstance(split, dict):
                    continue
                maxdd_value = _first_present(split, "max_drawdown", "maxdd")
                turnover_value = split.get("turnover")
                include_maxdd = include_maxdd or _safe_float(maxdd_value) is not None
                include_turnover = include_turnover or _safe_float(turnover_value) is not None
                trade_count = split.get("trade_count")
                include_trade_count = include_trade_count or trade_count is not None
                split_rows.append(
                    (
                        str(split.get("split") or idx),
                        _range_text(split, "train_start", "train_end"),
                        _range_text(split, "test_start", "test_end"),
                        _cell(split.get("params") or split.get("parameters") or "frozen parameters"),
                        _split_oos_sharpe_cell(split),
                        _pct(maxdd_value),
                        _pct(turnover_value),
                        _int_cell(trade_count),
                        _split_verdict(split),
                    )
                )
    if not split_rows:
        scores = detail if isinstance(detail, dict) else {}
        split_rows = [
            (
                "汇总",
                "rolling train windows",
                "rolling OOS windows",
                "frozen parameters",
                _fmt(scores.get("aggregate_oos_sharpe")),
                "missing",
                "missing",
                "missing",
                verdict or ("fail" if _walkforward_badge_class(scores) == "fail" else "pass"),
            )
        ]
        if reason:
            split_rows.append(("原因", "missing", "missing", reason, _fmt(scores.get("worst_oos_sharpe")), "missing", "missing", "missing", "fail"))
    headers = ["Split", "Train", "Test", "参数", "OOS Sharpe"]
    if include_maxdd:
        headers.append("MaxDD")
    if include_turnover:
        headers.append("Turnover")
    if include_trade_count:
        headers.append("Trades")
    headers.append("结论")
    body_rows = []
    for split, train, test, params, sharpe, maxdd, turnover, trade_count, result in split_rows:
        cells = [
            escape(split),
            escape(train),
            escape(test),
            escape(params),
            escape(sharpe),
        ]
        if include_maxdd:
            cells.append(escape(maxdd))
        if include_turnover:
            cells.append(escape(turnover))
        if include_trade_count:
            cells.append(escape(trade_count))
        cells.append(escape(result))
        body_rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return _table(headers, body_rows)


def _split_oos_sharpe_cell(split: Dict[str, Any]) -> str:
    if split.get("has_trades") is False:
        return "n/a (no trades)"
    if _safe_int(split.get("trade_count")) == 0:
        return "n/a (no trades)"
    return _fmt(_first_present(split, "oos_sharpe", "test_sharpe", "sharpe"))


def _decision_contract(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    status = _report_status(rows)
    klass = _status_badge_class(status)
    reasons = _decision_reasons(data, row)
    return (
        '<div class="decision">'
        '<div class="decision-mark">'
        f'{_badge("Go / No-Go", klass)}'
        f"<b>{escape(status)}</b>"
        "</div>"
        "<div><h3>推荐理由</h3><ul>"
        + "".join(f"<li>{escape(reason)}</li>" for reason in reasons)
        + "</ul></div></div>"
    )


def _next_steps_contract_table(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    strategy_id = _row_strategy_id(row)
    status = _report_status(rows)
    if status == "rejected":
        rows_data = [
            ("P0", "拒绝归档", "候选池状态保持 rejected，报告和审计 ledger 可追溯", f"quant/infrastructure/var/research/reports/{strategy_id}/"),
            ("P1", "若重启研究，补充独立 OOS、行业分类和容量约束", "walk-forward aggregate Sharpe 转正且最差 split 不崩", "quant/features/research/rigor/"),
            ("P2", "rejected_strategy 归档", "策略代码只保留在 rejected_strategy，不进入策略池或 paper trading", f"quant/features/rejected_strategy/{strategy_id}/"),
        ]
    else:
        rows_data = [
            ("P0", "人工复核信号和回测配置", "公式方向、复权、成本和 benchmark 均被复核", f"quant/features/strategies/{strategy_id}/"),
            ("P1", "扩大 OOS、容量分析和因子暴露", "通过 walk-forward、容量和风险因子阈值", "quant/features/research/validation/"),
            ("P2", "paper trading 观察", "观察期、风险预算和停机规则明确", "quant/api/ 与 quant/frontend/"),
        ]
    body = "".join(
        f"<tr><td>{escape(priority)}</td><td>{escape(task)}</td><td>{escape(criteria)}</td><td>{escape(owner)}</td></tr>"
        for priority, task, criteria, owner in rows_data
    )
    return _table(["优先级", "任务", "验收标准", "负责人/入口"], body)


def _template_style() -> str:
    base_style = (
        ":root{color-scheme:light;--bg:#f6f3ec;--panel:#fffdfa;--ink:#18222b;--muted:#66727e;--line:#d8dee3;--soft:#f0ece3;--accent:#0f766e;--good:#166534;--warn:#b45309;--bad:#991b1b}"
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:'Microsoft YaHei','PingFang SC','Noto Sans SC','Source Han Sans SC','Segoe UI',system-ui,sans-serif;line-height:1.62;letter-spacing:0}main{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:40px 0 72px}.panel{margin:18px 0;padding:24px;background:var(--panel);border:1px solid var(--line)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric,.decision-mark{padding:14px;border:1px solid var(--line);background:#fff}.logic-plain{margin:10px 0 14px;padding:14px 16px;background:#f8fafc;border:1px solid var(--line);color:#334155}.table-wrap{overflow-x:auto;margin:12px 0 16px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--soft)}.hypothesis-list{margin:0;padding-left:20px}.hypothesis-list li{margin:0 0 6px}.badge{display:inline-block;padding:2px 8px;border:1px solid var(--line);border-radius:999px;font-size:12px;font-weight:800}.pass{color:var(--good);background:#ecfdf5;border-color:#86efac}.warn{color:var(--warn);background:#fff7ed;border-color:#fed7aa}.fail{color:var(--bad);background:#fef2f2;border-color:#fecaca}.formula{padding:16px;margin:10px 0 16px;background:#fbf7ef;border:1px solid var(--line);font-family:'Cascadia Mono',Consolas,monospace;white-space:pre-wrap}.decision{display:grid;grid-template-columns:180px 1fr;gap:16px}.audit-details{margin:12px 0;border:1px solid var(--line);background:#fff}.audit-details>summary{cursor:pointer;padding:12px 14px;font-weight:800;background:var(--soft)}.audit-details[open]>summary{border-bottom:1px solid var(--line)}.audit-body{padding:14px}.appendix-panel h3{margin-top:18px}"
    )
    return base_style + "\n" + _REQUIRED_CHART_STYLE


def _strict_backtest_for_report(data: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    strict = (row.get("metrics") or {}).get("strict_backtest") or {}
    if strict:
        return strict
    if int(data.get("backtested", 0) or 0) <= 0:
        return {}
    return strict


def _not_run_table(headers: List[str], item: str, reason: str) -> str:
    body = "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in (item, "not_run", reason)[: len(headers)]) + "</tr>"
    while body.count("<td>") < len(headers):
        body = body.replace("</tr>", "<td>n/a</td></tr>")
    return _table(headers, [body])


def _message_table(headers: List[str], item: str, status: str, reason: str) -> str:
    if len(headers) <= 2:
        cells = f"<td>{escape(item)}</td><td>{escape(reason)}</td>"
    else:
        cells = (
            f"<td>{escape(item)}</td>"
            f"<td>{escape(status)}</td>"
            f'<td colspan="{len(headers) - 2}">{escape(reason)}</td>'
        )
    return _table(headers, [f"<tr>{cells}</tr>"])


def _stage1_score(data: Dict[str, Any], key: str, fallback: Any) -> Any:
    return fallback


def _first_present(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _join_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}={val}" for key, val in value.items())
    return str(value)


def _prediction_direction(row: Dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    rank_ic = _safe_float(metrics.get("rank_ic"))
    if rank_ic is not None and rank_ic < 0:
        return "lower_is_better / inverse"
    return "higher_is_better"


def _data_coverage(metrics: Dict[str, Any], strict: Dict[str, Any]) -> tuple[str, str]:
    period = str(strict.get("period") or "")
    start = str(metrics.get("data_start") or "")
    end = str(metrics.get("data_end") or "")
    if period and " to " in period:
        left, right = period.split(" to ", 1)
        start = start or left
        end = end or right
    return start or "未记录", end or "未记录"


def _universe_summary(row: Dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    evidence = row.get("evidence") or {}
    spec = evidence.get("strategy_spec") or {}
    spec_universe = list(spec.get("universe") or [])
    size = _safe_int(metrics.get("universe_size"))
    data_symbol_count = _safe_int(metrics.get("data_symbol_count"))
    sample = list(metrics.get("universe_sample") or spec_universe[:8])
    source = str(metrics.get("universe_source") or "")
    if spec_universe and len(spec_universe) > 20:
        size = size or len(spec_universe)
    if spec.get("pit_universe_enabled") or spec.get("risk_category_symbols"):
        size = size or len(spec_universe)
        sample_text = ", ".join(str(symbol) for symbol in sample[:8])
        actual = f"，实际取数 {data_symbol_count} 个 symbol" if data_symbol_count else ""
        suffix = f"；样例：{sample_text}" if sample_text else ""
        as_of = str(spec.get("universe_as_of") or "未记录")
        start = str(spec.get("universe_start") or "")
        end = str(spec.get("universe_end") or "")
        min_history = spec.get("universe_min_history_days_as_of") or 0
        category_cap = spec.get("universe_max_symbols_per_category") or "不限"
        policy = str(spec.get("universe_selection_policy") or "pit_category")
        counts = spec.get("registered_universe_counts") or {}
        quality_text = ""
        if counts:
            quality_text = (
                f"，注册池 active={int(counts.get('active_symbol_count') or 0)}"
                f"/registered={int(counts.get('registered_symbol_count') or 0)}"
                f"/missing_data={int(counts.get('missing_data_count') or 0)}"
            )
        if policy == "audited_stable_etf_registry":
            return (
                f"已审计稳定 ETF 注册池（策略={policy}，新增类别必须人工审计注册，"
                f"每类最多={category_cap}，解析 {size or len(spec_universe)} 个 symbol{actual}{quality_text}{suffix}）"
            )
        if policy == "dynamic_pit_category_wide":
            window = f"{start or '未记录'}~{end or '未记录'}"
            return (
                f"动态 PIT ETF 类别宽 universe（策略={policy}，窗口={window}，"
                f"每个调仓点按当时可见 bar/PIT规模/流动性/lookback 过滤，每类最多={category_cap}，解析 {size or len(spec_universe)} 个 symbol{actual}{quality_text}{suffix}）"
            )
        return (
            f"PIT ETF 类别 universe（策略={policy}，as-of={as_of}，"
            f"起点前最少历史={min_history}日，每类最多={category_cap}，解析 {size or len(spec_universe)} 个 symbol{actual}{quality_text}{suffix}）"
        )
    if size and (size > 20 or size > len(spec_universe)):
        sample_text = ", ".join(str(symbol) for symbol in sample[:8])
        actual = f"，实际取数 {data_symbol_count} 个 symbol" if data_symbol_count else ""
        suffix = f"；样例：{sample_text}" if sample_text else ""
        source_text = f"，来源：{source}" if source else ""
        return f"全 A 股 daily_cn_ochl universe（解析 {size} 个 symbol{actual}{source_text}{suffix}）"
    return _join_text(spec_universe or "CN A-share full daily_cn_ochl universe")


def _format_decay(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key}={_fmt(val)}" for key, val in value.items())
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, dict):
                horizon = _first_present(item, "horizon", "days", "lag")
                decay_value = _first_present(item, "ic", "rank_ic", "value")
                parts.append(
                    f"{horizon}d={_fmt(decay_value)}" if horizon is not None else _fmt(decay_value)
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                parts.append(f"{item[0]}d={_fmt(item[1])}")
            else:
                parts.append(_fmt(item))
        return "; ".join(parts) if parts else "n/a"
    return _fmt(value)


def _factor_exposure_text(metrics: Dict[str, Any]) -> str:
    exposure = metrics.get("factor_exposure") or metrics.get("factor_exposures")
    if exposure:
        return _join_text(exposure)
    parts = []
    for key in ("ff_alpha_monthly", "ff_alpha_tstat", "ff_r2", "beta", "size", "value", "momentum"):
        if key in metrics:
            parts.append(f"{key}={_fmt(metrics.get(key))}")
    return "; ".join(parts) if parts else "未保存独立因子暴露明细"


def _signal_oos_text(metrics: Dict[str, Any], wf_scores: Dict[str, Any], wf_verdict: str = "") -> str:
    if metrics.get("oos_validation"):
        return _join_text(metrics.get("oos_validation"))
    verdict = str(wf_verdict or "").strip().lower()
    if verdict in {"pass", "warn", "fail"}:
        return verdict
    aggregate = _safe_float(wf_scores.get("aggregate_oos_sharpe"))
    if aggregate is None:
        return "需结合 purged walk-forward 结果"
    return _walkforward_badge_class(wf_scores)


def _commission_text(value: Any) -> str:
    if isinstance(value, dict):
        cn = value.get("CN") or value.get("cn") or value
        return _join_text(cn)
    return str(value or "CN realistic")


def _excess(left: Any, right: Any) -> float | None:
    left_float = _safe_float(left)
    right_float = _safe_float(right)
    if left_float is None or right_float is None:
        return None
    return left_float - right_float


def _range_text(split: Dict[str, Any], start_key: str, end_key: str) -> str:
    start = split.get(start_key) or split.get(start_key.replace("_", ""))
    end = split.get(end_key) or split.get(end_key.replace("_", ""))
    if start or end:
        return f"{start or 'n/a'} - {end or 'n/a'}"
    return "n/a"


def _split_verdict(split: Dict[str, Any]) -> str:
    explicit = split.get("verdict") or split.get("result")
    if explicit:
        return str(explicit)
    if split.get("has_trades") is False or _safe_int(split.get("trade_count")) == 0:
        return "excluded_no_trade"
    sharpe = _safe_float(_first_present(split, "oos_sharpe", "test_sharpe", "sharpe"))
    return "pass" if sharpe is not None and sharpe > 0 else "fail"


def _walkforward_scores(row: Dict[str, Any]) -> tuple[Dict[str, Any], str, str]:
    metrics = row.get("metrics") or {}
    detail = metrics.get("walkforward") or metrics.get("purged_walkforward") or {}
    if isinstance(detail, dict):
        return detail, str(detail.get("reason") or ""), str(detail.get("verdict") or "")
    return {}, "", ""


def _walkforward_thresholds(scores: Dict[str, Any]) -> Dict[str, Any]:
    values = dict(_WALKFORWARD_DEFAULT_THRESHOLDS)
    raw = scores.get("thresholds") if isinstance(scores, dict) else {}
    if isinstance(raw, dict):
        for key in values:
            if raw.get(key) is not None:
                values[key] = raw.get(key)
    return values


def _threshold_float(thresholds: Dict[str, Any], key: str) -> float:
    fallback = _safe_float(_WALKFORWARD_DEFAULT_THRESHOLDS.get(key)) or 0.0
    return _safe_float(thresholds.get(key)) if _safe_float(thresholds.get(key)) is not None else fallback


def _threshold_int(thresholds: Dict[str, Any], key: str, fallback: Any = None) -> int:
    default = fallback if fallback is not None else _WALKFORWARD_DEFAULT_THRESHOLDS.get(key)
    value = _safe_float(thresholds.get(key))
    if value is None:
        value = _safe_float(default)
    return int(value or 0)


def _min_threshold_verdict(value: Any, minimum: float) -> str:
    number = _safe_float(value)
    if number is None:
        return "missing"
    return "pass" if number >= minimum else "fail"


def _reference_verdict(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "missing"
    return "参考-正" if number > 0 else "参考-弱"


def _dsr_threshold_verdict(value: Any, minimum: float) -> str:
    number = _safe_float(value)
    if number is None:
        return "missing"
    return "pass" if number >= minimum else "warn"


def _regime_threshold_verdict(scores: Dict[str, Any]) -> str:
    if scores.get("bull_only_warning") is True:
        return "warn"
    if scores.get("regime_breakdown"):
        return "pass"
    return "missing"


def _walkforward_capacity_value(scores: Dict[str, Any]) -> str:
    explicit = scores.get("capacity_viability")
    if explicit:
        return _cell(explicit)
    if scores.get("capacity_ok") is True:
        return "通过"
    if scores.get("capacity_ok") is False:
        return "未通过"
    return "未记录"


def _capacity_threshold_verdict(scores: Dict[str, Any]) -> str:
    if scores.get("capacity_ok") is True:
        return "pass"
    if scores.get("capacity_ok") is False:
        return "fail"
    return "missing"


def _threshold_badge(verdict: str) -> str:
    value = str(verdict or "missing")
    klass = "pass" if value == "pass" else "fail" if value == "fail" else "warn"
    return _badge(value, klass)


def _signal_badge_class(metrics: Dict[str, Any]) -> str:
    rank_ic = _safe_float(metrics.get("rank_ic"))
    fdr = _safe_float(metrics.get("fdr_adjusted_p"))
    hit = _safe_float(metrics.get("hit_rate"))
    if rank_ic is None:
        return "warn"
    if rank_ic < 0.02:
        return "fail"
    if (fdr is None or fdr <= 0.05) and (hit is None or hit >= 0.5):
        return "pass"
    if rank_ic > 0:
        return "warn"
    return "fail"


def _has_signal_validation_metrics(metrics: Dict[str, Any]) -> bool:
    return any(
        metrics.get(key) is not None
        for key in (
            "rank_ic",
            "rank_ic_ir",
            "rank_ic_tstat",
            "rank_ic_t_stat",
            "fdr_adjusted_p",
            "hit_rate",
            "ic_decay",
            "rank_ic_p_value",
        )
    )


def _strict_badge_class(metrics: Dict[str, Any]) -> str:
    cagr = _safe_float(metrics.get("cagr"))
    drawdown = _safe_float(metrics.get("max_drawdown_pct"))
    trades = _safe_float(metrics.get("total_trades"))
    if cagr is None or drawdown is None:
        return "fail"
    tier = _cagr_drawdown_tier(cagr)
    if tier is not None and abs(drawdown) <= tier[1] and (trades is None or trades > 50):
        return "pass"
    if cagr > 0:
        return "warn"
    return "fail"


def _walkforward_badge_class(scores: Dict[str, Any]) -> str:
    if scores.get("verdict") in {"pass", "warn", "warning", "fail"}:
        return _stage_badge_class(str(scores.get("verdict")))
    if scores.get("is_viable") is True:
        thresholds = _walkforward_thresholds(scores)
        dsr = _safe_float(scores.get("deflated_sharpe_ratio"))
        if dsr is not None and dsr < _threshold_float(thresholds, "min_deflated_sharpe_ratio"):
            return "warn"
        return "pass"
    if scores.get("is_viable") is False:
        return "fail"
    aggregate = _safe_float(scores.get("aggregate_oos_sharpe"))
    worst = _safe_float(scores.get("worst_oos_sharpe"))
    pct_profitable = _safe_float(scores.get("pct_profitable_splits"))
    thresholds = _walkforward_thresholds(scores)
    if aggregate is None and worst is None:
        return "fail"
    if (
        worst is not None
        and worst >= _threshold_float(thresholds, "min_worst_oos_sharpe")
        and (pct_profitable is None or pct_profitable >= _threshold_float(thresholds, "min_profitable_splits_pct"))
    ):
        return "pass"
    if aggregate is not None and aggregate > 0:
        return "warn"
    return "fail"


def _status_badge_class(status: str) -> str:
    if status in {"candidate", "paper_trading_candidate"}:
        return "pass"
    if status in {"needs_more_validation", "validated", "idea_candidate", "needs_manual_spec"}:
        return "warn"
    return "fail"


def _signal_validation_summary(metrics: Dict[str, Any], row: Dict[str, Any] | None = None) -> str:
    if not _has_signal_validation_metrics(metrics):
        if row is not None and not _uses_cross_sectional_fast_validation(row):
            return _fast_validation_scope_text(row)
        return "Fast research / HFQ signal validation evidence is missing; this is a failed evidence gate for cross-sectional alpha research."
    return (
        f"Rank IC={_fmt(metrics.get('rank_ic'))}，FDR={_fmt(metrics.get('fdr_adjusted_p'))}，"
        f"ICIR={_fmt(metrics.get('rank_ic_ir'))}，hit rate={_pct(metrics.get('hit_rate'))}，"
        "准入阈值 Rank IC>=0.0200"
    )


def _strict_backtest_summary(metrics: Dict[str, Any]) -> str:
    if not metrics:
        return "本轮未运行严格 Backtester；信号验证失败后流水线停止。"
    return (
        f"Sharpe={_fmt(metrics.get('sharpe'))}，CAGR={_pct(metrics.get('cagr'))}，"
        f"MaxDD={_pct(metrics.get('max_drawdown_pct'))}，"
        f"Calmar={_fmt(_first_present(metrics, 'calmar_ratio', 'calmar'))}，交易数={metrics.get('total_trades') or 'n/a'}"
    )


def _walkforward_summary_sentence(scores: Dict[str, Any], reason: str) -> str:
    base = (
        f"aggregate OOS Sharpe={_fmt(scores.get('aggregate_oos_sharpe'))}，"
        f"worst OOS Sharpe={_fmt(scores.get('worst_oos_sharpe'))}，"
        f"盈利 split 占比={_pct(scores.get('pct_profitable_splits'))}"
    )
    return f"{base}；{reason}" if reason else base


def _deployment_summary(row: Dict[str, Any]) -> str:
    status = str(row.get("status") or "needs_more_validation")
    if status == "rejected":
        return "不进入候选池或 paper trading；保留报告、代码和审计记录。"
    if status == "paper_trading_candidate":
        return "可进入 paper trading 候选，但需要人工复核和风控审批。"
    if status == "candidate":
        return "进入候选池继续做容量、组合相关性和风控预算验证。"
    return "暂不部署；需要更多正式验证。"


def _decision_reasons(data: Dict[str, Any], row: Dict[str, Any]) -> List[str]:
    if not row:
        return ["缺少 hypothesis ledger，无法形成 Go / No-Go。"]
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    wf_scores, wf_reason, _ = _walkforward_scores(row)
    reasons = [
        _signal_validation_summary(metrics, row),
        _strict_backtest_summary(strict_metrics),
        _walkforward_summary_sentence(wf_scores, wf_reason),
    ]
    decision = _decision_reason(row)
    if decision:
        reasons.append(decision)
    return reasons


def _html_document(title: str, body: str) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            "<style>",
            _template_style(),
            "</style>",
            "</head>",
            "<body>",
            "<main>",
            body,
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _summary_table(data: Dict[str, Any]) -> str:
    labels = {
        "discovered": "已搜集 idea",
        "evaluated": "已评估 idea",
        "specified": "已生成 StrategySpec",
        "validated": "已验证信号",
        "validated_passed": "信号验证通过",
        "integrated": "已实现/集成策略",
        "backtested": "已严格回测",
        "walkforward_passed": "Walk-forward 通过",
        "rejected": "已拒绝",
    }
    rows = []
    for key in (
        "discovered",
        "evaluated",
        "specified",
        "validated",
        "validated_passed",
        "integrated",
        "backtested",
        "walkforward_passed",
        "rejected",
    ):
        rows.append(f"<tr><td>{escape(labels[key])}</td><td>{escape(str(data.get(key, 0)))}</td></tr>")
    return '<div class="table-wrap"><table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


def _conclusion_summary(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次运行没有形成可审计的 hypothesis 记录，因此不能给出策略推荐。</p>"
    statuses: Dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    deployable = statuses.get("paper_trading_candidate", 0)
    candidates = statuses.get("candidate", 0)
    needs_more = statuses.get("needs_more_validation", 0) + statuses.get("idea_candidate", 0) + statuses.get("validated", 0)
    rejected = statuses.get("rejected", 0)
    strict_done = int(data.get("backtested", 0) or 0)
    wf_passed = int(data.get("walkforward_passed", 0) or 0)
    conclusion = "本次没有策略可以直接进入实盘或 paper trading。"
    if deployable:
        conclusion = "本次存在 paper_trading_candidate，但仍不能自动实盘，需要进入人工复核、容量和组合分配流程。"
    elif candidates:
        conclusion = "本次存在 candidate，可进入候选池继续做稳定性、容量和组合层验证。"
    elif needs_more:
        conclusion = "本次主要结论是 needs_more_validation，需要补充样本外、容量或稳健性验证。"
    rows_html = [
        f"<tr><td>结论</td><td>{escape(conclusion)}</td></tr>",
        f"<tr><td>候选策略</td><td>{escape(str(candidates))}</td></tr>",
        f"<tr><td>Paper trading 候选</td><td>{escape(str(deployable))}</td></tr>",
        f"<tr><td>需要更多验证</td><td>{escape(str(needs_more))}</td></tr>",
        f"<tr><td>已拒绝</td><td>{escape(str(rejected))}</td></tr>",
        f"<tr><td>严格回测完成</td><td>{escape(str(strict_done))}</td></tr>",
        f"<tr><td>Purged walk-forward 通过</td><td>{escape(str(wf_passed))}</td></tr>",
    ]
    return '<div class="table-wrap"><table><thead><tr><th>项目</th><th>结论</th></tr></thead><tbody>' + "".join(rows_html) + "</tbody></table></div>"


def _pipeline_contract_table(data: Dict[str, Any]) -> str:
    stages = [
        (
            "阶段一",
            "搜集多个 idea，按来源质量和 admission_score 做准入评估，并写成 StrategySpec 候选。",
            f"{data.get('discovered', 0)} discovered, {data.get('evaluated', 0)} evaluated, {data.get('specified', 0)} specified",
        ),
        (
            "阶段二",
            "对每个候选逐一做 HFQ 真实数据验证、long-only 组合诊断、严格 Backtester、000300 基准比较和 Go / No-Go。",
            f"{data.get('validated_passed', 0)} passed / {data.get('validated', 0)} validated, {data.get('backtested', 0)} strict backtests",
        ),
    ]
    rows = "".join(
        f"<tr><td>{escape(stage)}</td><td>{escape(rule)}</td><td>{escape(outcome)}</td></tr>"
        for stage, rule, outcome in stages
    )
    return '<div class="table-wrap"><table><thead><tr><th>阶段</th><th>要求</th><th>本次运行</th></tr></thead><tbody>' + rows + "</tbody></table></div>"


def _idea_discovery_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次没有搜集到策略 idea。</p>"
    body = []
    for row in rows:
        evidence = row.get("evidence") or {}
        quality = evidence.get("discovery_quality") or {}
        metadata = evidence.get("metadata") or {}
        body.append(
            "<tr>"
            + f"<td>{_source_link(row)}</td>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(evidence.get('published_date', '')))}</td>"
            + f"<td>{escape(_fmt(quality.get('score')))}</td>"
            + f"<td>{escape(', '.join(quality.get('matched_terms', []) or metadata.get('matched_terms', []) or []))}</td>"
            + f"<td>{escape(', '.join(quality.get('risk_flags', []) or [])) or '无'}</td>"
            + "</tr>"
        )
    return _table(["来源", "Idea", "发布时间", "来源质量", "命中标签", "风险旗标"], body)


def _admission_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次没有写入准入评估记录。</p>"
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(row.get('stage', '')))}</td>"
            + f"<td>{escape(str(row.get('status', '')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('admission_score')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('signal_quality_score')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('research_confidence_score')))}</td>"
            + f"<td>{escape(', '.join(metrics.get('required_data_fields', []) or []))}</td>"
            + f"<td>{escape(_decision_reason(row))}</td>"
            + "</tr>"
        )
    return _table(["Idea", "阶段", "状态", "Admission", "Signal", "Confidence", "字段", "决策"], body)


def _risk_flags_list(rows: List[Dict[str, Any]]) -> str:
    items = []
    for row in rows:
        evidence = row.get("evidence") or {}
        quality = evidence.get("discovery_quality") or {}
        metrics = row.get("metrics") or {}
        flags = list(quality.get("risk_flags", []) or []) + list(metrics.get("risk_flags", []) or [])
        deduped = []
        for flag in flags:
            if flag and flag not in deduped:
                deduped.append(str(flag))
        if deduped:
            title = escape(str(row.get("title", "")))
            items.append(f"<li><strong>{title}</strong>: {escape(', '.join(deduped))}</li>")
    if not items:
        items = [
            "<li>本次初筛未记录高优先级风险旗标；正式研究仍需复核 look-ahead、survivorship、data snooping、成本敏感性和容量。</li>"
        ]
    return "<ul>" + "".join(items) + "</ul>"


def _spec_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        spec = (row.get("evidence") or {}).get("strategy_spec") or {}
        if not spec:
            continue
        strategy_id = row.get("strategy_id") or spec.get("strategy_id", "")
        body.append(
            "<tr>"
            + f"<td><code>{escape(str(strategy_id))}</code></td>"
            + f"<td>{escape(str(spec.get('signal_formula_key', '')))}</td>"
            + f"<td>{escape(', '.join(str(s) for s in spec.get('universe', []) or []))}</td>"
            + f"<td>{escape(str(spec.get('lookback_days', '')))}</td>"
            + f"<td>{escape(str(spec.get('horizon_days', '')))}</td>"
            + f"<td>{escape(str(spec.get('execution_lag_days', '')))}</td>"
            + f"<td>{escape(', '.join(spec.get('required_fields', []) or []))}</td>"
            + "<td>A 股可部署组合仅 long-only；long-short 仅作 alpha 诊断</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有生成 ready 状态的 StrategySpec。</p>"
    return _table(["Strategy ID", "信号公式", "Universe", "Lookback", "Horizon", "执行延迟", "字段", "A股约束"], body)


def _signal_definition_detail(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        evidence = row.get("evidence") or {}
        spec = evidence.get("strategy_spec") or {}
        metrics = row.get("metrics") or {}
        if not spec and not metrics:
            continue
        formula = str(spec.get("signal_formula_key", "") or "")
        lookback = spec.get("lookback_days", "")
        horizon = spec.get("horizon_days", "")
        lag = spec.get("execution_lag_days", "")
        direction = _signal_direction(row)
        construction = _signal_construction_steps(row)
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(row.get('thesis', '')))}</td>"
            + f"<td>{escape(formula)}</td>"
            + f"<td>{escape(construction)}</td>"
            + f"<td>{escape(direction)}</td>"
            + f"<td>lookback={escape(str(lookback))}; horizon={escape(str(horizon))}; lag={escape(str(lag))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有足够信息展开信号定义。</p>"
    return _table(["Idea", "经济学假设", "公式/模型", "构造步骤", "预测方向", "时间结构"], body)


def _signal_implementation_checks(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        evidence = row.get("evidence") or {}
        spec = evidence.get("strategy_spec") or {}
        metrics = row.get("metrics") or {}
        if not spec and not metrics:
            continue
        validation_tests = metrics.get("validation_tests") or []
        risk_flags = metrics.get("risk_flags") or []
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + "<td>后复权 adj_*；缺失时 raw price × adj_factor</td>"
            + "<td>A 股 long-only；long-short 只作 alpha 诊断</td>"
            + f"<td>{escape(', '.join(spec.get('required_fields', []) or metrics.get('required_data_fields', []) or []))}</td>"
            + f"<td>{escape(', '.join(validation_tests) if validation_tests else 'rank_ic, fdr_control, ic_decay, purged_walk_forward')}</td>"
            + f"<td>{escape(', '.join(risk_flags) if risk_flags else '无')}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有可实现性检查记录。</p>"
    return _table(["Idea", "价格口径", "交易约束", "字段需求", "验证清单", "风险旗标"], body)


def _signal_direction(row: Dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    rank_ic = _safe_float(metrics.get("rank_ic"))
    if rank_ic is not None and rank_ic < 0:
        return "信号值越低，预期未来收益越高；若要部署必须显式反向。"
    return "信号值越高，预期未来收益越高；组合只允许选 Top 20 long-only。"


def _signal_construction_steps(row: Dict[str, Any]) -> str:
    evidence = row.get("evidence") or {}
    spec = evidence.get("strategy_spec") or {}
    formula = str(spec.get("signal_formula_key", "") or "").lower()
    lookback = spec.get("lookback_days", "")
    horizon = spec.get("horizon_days", "")
    if "ridge" in formula or "daily return" in formula or "drif" in str(row.get("strategy_id", "")).lower():
        return (
            f"1. 用后复权收盘价计算过去 {lookback} 个交易日的日收益序列；"
            "2. 提取日收益序列及分布统计特征；"
            f"3. 用滚动 ridge 模型预测未来 {horizon} 日后复权收益；"
            "4. 每个截面按预测值排序；5. 只允许买入 Top 20，禁止 A 股 long-short 部署。"
        )
    if "momentum" in formula:
        return f"1. 用后复权价格计算过去 {lookback} 日收益；2. 截面排序；3. 选择高动量标的；4. 下一交易日执行。"
    if "reversal" in formula or "mean" in formula:
        return f"1. 计算价格相对均值或近期收益偏离；2. 截面排序；3. 选择反转概率最高标的；4. 下一交易日执行。"
    if formula == "ashare_small_cap_guarded_size_factor":
        return "1. 使用全 A 股日线和 daily_basic 字段；2. 过滤 ST、停牌、非 L 上市状态、价格低于下限、成交额低于下限和市值缺失标的；3. 按 point-in-time 市值升序排列；4. 选择最小的 20 只；5. 按目标总敞口等权分配，剩余资金留现金；6. 信号在收盘后生成，下一交易日执行。"
    return "1. 使用 StrategySpec 声明字段构造信号；2. 只用当日及以前数据；3. 截面排序；4. 下一交易日按 long-only 约束执行。"


def _signal_validation_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        if "rank_ic" not in metrics:
            continue
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(metrics.get('data_start', '')))}</td>"
            + f"<td>{escape(str(metrics.get('data_end', '')))}</td>"
            + f"<td>{escape(str(metrics.get('n_observations', '')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('rank_ic')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('rank_ic_ir')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('fama_macbeth_tstat')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('fdr_adjusted_p')))}</td>"
            + f"<td>{escape(_pct(metrics.get('hit_rate')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('long_short_spread')))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有完成真实数据的信号验证。</p>"
    return _table(["Idea", "数据起点", "数据终点", "样本数", "Rank IC", "ICIR", "FM t-stat", "FDR p", "Hit Rate", "LS诊断"], body)


def _signal_validation_judgement(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        if "rank_ic" not in metrics:
            continue
        rank_ic = _safe_float(metrics.get("rank_ic"))
        icir = _safe_float(metrics.get("rank_ic_ir"))
        fdr = _safe_float(metrics.get("fdr_adjusted_p"))
        hit = _safe_float(metrics.get("hit_rate"))
        diag = metrics.get("portfolio_diagnostics") or {}
        signal_read = _read_rank_ic(rank_ic, icir)
        stat_read = _read_fdr(fdr)
        portfolio_read = _read_portfolio_diagnostic(diag)
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(signal_read)}</td>"
            + f"<td>{escape(stat_read)}</td>"
            + f"<td>{escape(_read_hit_rate(hit))}</td>"
            + f"<td>{escape(portfolio_read)}</td>"
            + f"<td>{escape(_decision_reason(row))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有可判读的信号验证指标。</p>"
    return _table(["Idea", "IC/ICIR 判读", "统计显著性", "Hit Rate 判读", "组合诊断判读", "研究结论"], body)


def _signal_validation_metric_explanations() -> str:
    rows = [
        ("Rank IC", "截面信号排名与未来收益排名的 Spearman 相关。日线 A 股信号通常不要求单期很高，但需要方向稳定、样本足够、分期不崩。"),
        ("ICIR", "Rank IC 的均值除以波动，衡量 IC 稳定性。ICIR 越高，越像可重复的截面预测而不是偶然噪声。"),
        ("FM t-stat", "Fama-MacBeth 风格的截面回归统计量，用来辅助判断信号收益斜率是否稳定大于 0。"),
        ("FDR p", "多重检验校正后的 p 值。大量 idea 扫描时必须用 FDR 控制 data snooping，p 值不显著时不能因组合回测好看就推荐上线。"),
        ("Hit Rate", "Top 20 或信号方向预测为正的比例。它不等于胜率，但能辅助判断信号方向是否一致。"),
        ("LS诊断", "long-short spread 只用于 alpha 诊断。A 股策略报告不能把它当可部署组合，最终组合必须 long-only。"),
    ]
    body = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rows)
    return '<div class="table-wrap"><table><thead><tr><th>指标</th><th>解释与使用方式</th></tr></thead><tbody>' + body + "</tbody></table></div>"


def _read_rank_ic(rank_ic: float | None, icir: float | None) -> str:
    if rank_ic is None:
        return "缺少 Rank IC，不能判断信号方向。"
    if rank_ic < 0:
        return f"Rank IC={rank_ic:.4f} 为负，原始方向与未来收益相反。"
    if rank_ic < 0.02:
        return f"Rank IC={rank_ic:.4f} 偏弱；即使组合表现为正，也需要警惕噪声和样本选择。"
    if icir is not None and icir < 0.2:
        return f"Rank IC={rank_ic:.4f} 达标但 ICIR={icir:.4f} 偏低，稳定性不足。"
    return f"Rank IC={rank_ic:.4f} 且方向为正，具备继续研究价值。"


def _read_fdr(fdr: float | None) -> str:
    if fdr is None:
        return "缺少 FDR p 值，不能完成多重检验控制。"
    if fdr <= 0.05:
        return f"FDR p={fdr:.4f}，通过 5% 多重检验阈值。"
    if fdr <= 0.10:
        return f"FDR p={fdr:.4f}，仅边际显著，需要更长样本或独立 OOS。"
    return f"FDR p={fdr:.4f}，不显著；不能作为候选池准入的强证据。"


def _read_hit_rate(hit: float | None) -> str:
    if hit is None:
        return "缺少 hit rate。"
    if hit >= 0.55:
        return f"Hit rate={hit:.2%}，方向一致性尚可。"
    if hit >= 0.50:
        return f"Hit rate={hit:.2%}，略高于随机水平，需要结合 IC 和成本。"
    return f"Hit rate={hit:.2%}，方向一致性不足。"


def _read_portfolio_diagnostic(diag: Dict[str, Any]) -> str:
    if not diag:
        return "缺少 Top 20 long-only 诊断。"
    ann = _safe_float(diag.get("top_bucket_annualized_return"))
    excess = _safe_float(diag.get("benchmark_excess_after_cost_mean_return"))
    benchmark = str(diag.get("benchmark_symbol", "") or "")
    parts = []
    if ann is not None:
        parts.append(f"Top 20 近似年化 {ann:.2%}")
    if excess is not None:
        parts.append(f"成本后相对 {benchmark or 'benchmark'} 单期超额 {excess:.4%}")
    parts.append("该诊断不能替代正式 Backtester。")
    return "；".join(parts)


def _data_benchmark_definition(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        evidence = row.get("evidence") or {}
        spec = evidence.get("strategy_spec") or {}
        strict = metrics.get("strict_backtest") or {}
        strict_benchmark = strict.get("benchmark") or {}
        diag = metrics.get("portfolio_diagnostics") or {}
        diag_coverage = diag.get("benchmark_coverage") or {}
        if not metrics and not spec:
            continue
        benchmark_symbol = strict_benchmark.get("symbol") or diag.get("benchmark_symbol") or "000300 优先，510300 fallback"
        benchmark_start = strict_benchmark.get("coverage_start") or diag_coverage.get("start") or ""
        benchmark_end = strict_benchmark.get("coverage_end") or diag_coverage.get("end") or ""
        fallback = strict_benchmark.get("fallback_used", diag_coverage.get("fallback_used", ""))
        data_start = metrics.get("data_start") or strict.get("period", "").split(" to ")[0] if strict.get("period") else metrics.get("data_start", "")
        data_end = metrics.get("data_end") or strict.get("period", "").split(" to ")[-1] if strict.get("period") else metrics.get("data_end", "")
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + "<td>DuckDB daily_cn_ochl</td>"
            + "<td>后复权 adj_*；缺失时 raw price × adj_factor</td>"
            + f"<td>{escape(str(data_start))}</td>"
            + f"<td>{escape(str(data_end))}</td>"
            + f"<td>{escape(str(metrics.get('n_observations', '')))}</td>"
            + f"<td><code>{escape(str(benchmark_symbol))}</code></td>"
            + f"<td>{escape(str(benchmark_start))} / {escape(str(benchmark_end))}</td>"
            + f"<td>{escape(str(fallback))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有足够的数据覆盖或 benchmark 记录。完整 A 股报告必须写清 daily_cn_ochl 覆盖、后复权口径和 000300/510300 fallback 决策。</p>"
    return _table(["Idea", "数据源", "价格口径", "数据起点", "数据终点", "样本数", "Benchmark", "Benchmark覆盖", "Fallback"], body)


def _data_quality_checks(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        strict = metrics.get("strict_backtest") or {}
        benchmark = strict.get("benchmark") or {}
        if not metrics:
            continue
        checks = [
            "后复权价格优先使用 adj_*，缺失时 raw price × adj_factor",
            "信号日到成交日至少保留 execution lag，避免未来信息",
            "A 股部署口径只允许 long-only，long-short 仅作诊断",
        ]
        if benchmark:
            checks.append(
                f"benchmark={benchmark.get('symbol', '000300')}，覆盖 {benchmark.get('coverage_start', '')} 至 {benchmark.get('coverage_end', '')}"
            )
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(metrics.get('data_start', '')))}</td>"
            + f"<td>{escape(str(metrics.get('data_end', '')))}</td>"
            + f"<td>{escape(str(metrics.get('n_observations', '')))}</td>"
            + f"<td>{escape('; '.join(checks))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有保存数据质量检查记录；正式研究报告不得省略复权、缺失值、停牌、样本边界和 benchmark 覆盖说明。</p>"
    return _table(["Idea", "数据起点", "数据终点", "样本数", "检查结论"], body)


def _portfolio_diagnostics_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        diag = (row.get("metrics") or {}).get("portfolio_diagnostics") or {}
        if not diag:
            continue
        rolling = diag.get("rolling_oos") or []
        rolling_text = "; ".join(
            f"{item.get('split')}: {_pct(item.get('annualized_return'))}, hit {_pct(item.get('hit_rate'))}"
            for item in rolling[:6]
        )
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(diag.get('kind', '')))}</td>"
            + f"<td>{escape(_pct(diag.get('top_bucket_annualized_return')))}</td>"
            + f"<td>{escape(_pct(diag.get('top_bucket_hit_rate')))}</td>"
            + f"<td>{escape(_fmt(_first_present(diag, 'top_bucket_after_cost_calmar_ratio', 'top_bucket_calmar_ratio')))}</td>"
            + f"<td>{escape(_pct(diag.get('top_bucket_after_cost_mean_return')))}</td>"
            + f"<td>{escape(_pct(diag.get('top1_pct_annualized_return')))}</td>"
            + f"<td>{escape(_fmt(_first_present(diag, 'top1_pct_after_cost_calmar_ratio', 'top1_pct_calmar_ratio')))}</td>"
            + f"<td>{escape(str(diag.get('benchmark_symbol', '')))}</td>"
            + f"<td>{escape(_pct(diag.get('benchmark_excess_after_cost_mean_return')))}</td>"
            + f"<td>{escape(rolling_text or 'n/a')}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有记录 long-only 组合诊断；这会阻断完整研究结论。</p>"
    return _table(["Idea", "诊断类型", "Top 年化", "Top Hit", "Top Calmar", "成本后均值", "Top 1% 年化", "Top 1% Calmar", "Benchmark", "成本后超额", "Rolling OOS"], body)


def _strict_backtest_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        metrics = strict.get("metrics") or {}
        diagnostics = strict.get("diagnostics") or {}
        if not strict:
            continue
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(strict.get('period', '')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('sharpe')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('sortino')))}</td>"
            + f"<td>{escape(_pct(metrics.get('cagr')))}</td>"
            + f"<td>{escape(_pct(metrics.get('total_return')))}</td>"
            + f"<td>{escape(_pct(metrics.get('max_drawdown_pct')))}</td>"
            + f"<td>{escape(_fmt(_first_present(metrics, 'calmar_ratio', 'calmar')))}</td>"
            + f"<td>{escape(_pct(metrics.get('win_rate')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('profit_factor')))}</td>"
            + f"<td>{escape(str(metrics.get('total_trades', 0)))}</td>"
            + f"<td>{escape(_fmt(diagnostics.get('total_commission')))}</td>"
            + f"<td>{escape(str(diagnostics.get('limit_rejected_orders', 0)))}/{escape(str(diagnostics.get('t1_rejected_sells', 0)))}</td>"
            + f"<td>{escape(str(_insufficient_cash_rejected_orders(diagnostics)))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有记录严格 Backtester 结果；这会阻断 Go / No-Go。</p>"
    return _table(["Idea", "区间", "Sharpe", "Sortino", "CAGR", "累计", "MaxDD", "Calmar", "胜率", "PF", "成交数", "手续费", "涨跌停/T+1拒单", "现金不足拒单"], body)


def _backtest_return_risk_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        metrics = strict.get("metrics") or {}
        if not strict:
            continue
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(_pct(metrics.get('cagr')))}</td>"
            + f"<td>{escape(_pct(metrics.get('total_return')))}</td>"
            + f"<td>{escape(_pct(metrics.get('annualized_volatility')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('sharpe')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('sortino')))}</td>"
            + f"<td>{escape(_pct(metrics.get('max_drawdown_pct')))}</td>"
            + f"<td>{escape(_fmt(_first_present(metrics, 'calmar_ratio', 'calmar')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('tail_ratio')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('ulcer_index')))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>缺少收益与风险拆解。</p>"
    return _table(["Idea", "CAGR", "累计收益", "年化波动", "Sharpe", "Sortino", "MaxDD", "Calmar", "Tail Ratio", "Ulcer Index"], body)


def _backtest_trade_cost_constraints_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        metrics = strict.get("metrics") or {}
        diagnostics = strict.get("diagnostics") or {}
        constraints = strict.get("constraints") or {}
        if not strict:
            continue
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td>{escape(str(metrics.get('total_trades', 0)))}</td>"
            + f"<td>{escape(str(metrics.get('round_trip_trades', diagnostics.get('round_trip_trades', ''))))}</td>"
            + f"<td>{escape(_pct(metrics.get('win_rate')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('profit_factor')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('payoff_ratio')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('expectancy')))}</td>"
            + f"<td>{escape(_fmt(diagnostics.get('total_commission')))}</td>"
            + f"<td>{escape(_pct(_cost_drag_value(diagnostics)))}</td>"
            + f"<td>{escape(str(diagnostics.get('volume_limited_trades', 0)))}</td>"
            + f"<td>{escape(str(diagnostics.get('limit_rejected_orders', 0)))}</td>"
            + f"<td>{escape(str(diagnostics.get('t1_rejected_sells', 0)))}</td>"
            + f"<td>{escape(str(_insufficient_cash_rejected_orders(diagnostics)))}</td>"
            + f"<td>{escape(str(constraints.get('cn_lot_size', 100)))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>缺少交易、成本或执行约束诊断。</p>"
    return _table(["Idea", "成交/Fill数", "Round Trips", "胜率", "Profit Factor", "Payoff", "Expectancy", "总佣金", "成本拖累", "成交量限制", "涨跌停拒单", "T+1拒单", "现金不足拒单", "手数"], body)


def _backtest_stat_benchmark_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        metrics = strict.get("metrics") or {}
        benchmark = strict.get("benchmark") or {}
        stat = strict.get("statistical_significance") or {}
        if not strict:
            continue
        t_stat = metrics.get("t_stat", stat.get("t_stat"))
        p_value = metrics.get("p_value", stat.get("p_value"))
        ci = stat.get("confidence_interval") or metrics.get("confidence_interval") or []
        ci_text = ""
        if isinstance(ci, (list, tuple)) and len(ci) >= 2:
            ci_text = f"{_fmt(ci[0])} / {_fmt(ci[1])}"
        body.append(
            "<tr>"
            + f"<td>{escape(str(row.get('title', '')))}</td>"
            + f"<td><code>{escape(str(benchmark.get('symbol', '')))}</code></td>"
            + f"<td>{escape(_pct(benchmark.get('benchmark_return')))}</td>"
            + f"<td>{escape(_pct(benchmark.get('alpha')))}</td>"
            + f"<td>{escape(_fmt(benchmark.get('beta')))}</td>"
            + f"<td>{escape(_fmt(benchmark.get('information_ratio')))}</td>"
            + f"<td>{escape(_pct(benchmark.get('tracking_error')))}</td>"
            + f"<td>{escape(_fmt(t_stat))}</td>"
            + f"<td>{escape(_fmt(p_value))}</td>"
            + f"<td>{escape(ci_text or 'n/a')}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>缺少统计显著性或 benchmark 归因。</p>"
    return _table(["Idea", "Benchmark", "Benchmark收益", "Alpha", "Beta", "IR", "Tracking Error", "t-stat", "p-value", "置信区间"], body)


def _yearly_returns_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        yearly = strict.get("yearly_returns") or {}
        if not yearly:
            continue
        for year, value in yearly.items():
            body.append(
                "<tr>"
                + f"<td>{escape(str(row.get('title', '')))}</td>"
                + f"<td>{escape(str(year))}</td>"
                + f"<td>{escape(_pct(value))}</td>"
                + "</tr>"
            )
    if not body:
        return "<p>本次没有年度收益拆解。</p>"
    return _table(["Idea", "年份", "收益"], body)


def _cost_drag_value(diagnostics: Dict[str, Any]) -> Any:
    value = diagnostics.get("cost_drag_pct")
    numeric = _safe_float(value)
    if numeric is not None and abs(numeric) > 1.0:
        return numeric / 100.0
    return value


def _sum_count_values(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    total = 0
    for count in value.values():
        number = _safe_int(count)
        if number is not None:
            total += number
    return total


def _count_summary(value: Any, limit: int = 5) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    pairs = []
    for key, count in value.items():
        number = _safe_int(count)
        if number is None or number == 0:
            continue
        pairs.append((str(key), number))
    if not pairs:
        return "-"
    pairs.sort(key=lambda item: abs(item[1]), reverse=True)
    return "; ".join(f"{key}={count}" for key, count in pairs[:limit])


def _int_cell(value: Any) -> str:
    number = _safe_int(value)
    return str(number) if number is not None else "0"


def _insufficient_cash_rejected_orders(diagnostics: Dict[str, Any]) -> int:
    value = diagnostics.get("insufficient_cash_rejected_orders")
    count = _safe_int(value)
    if count is not None:
        return count
    rejection_counts = diagnostics.get("rejection_counts") or {}
    if isinstance(rejection_counts, dict):
        return int(rejection_counts.get("insufficient_cash", 0) or 0)
    return 0


def _backtest_requirements_table() -> str:
    rows = [
        ("收益", "CAGR、累计收益、benchmark alpha、information ratio"),
        ("风险", "MaxDD、年化波动、Sortino、尾部损失和回撤恢复"),
        ("交易", "成交数、换手、胜率、Profit Factor、订单拒绝原因"),
        ("成本", "佣金、滑点、印花税、成本后收益和成本拖累"),
        ("A股约束", "T+1、100 股手数、涨跌停、成交量限制、long-only"),
        ("审计产物", "equity curve、trades、diagnostics、benchmark 覆盖和配置快照"),
    ]
    body = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rows)
    return '<div class="table-wrap"><table><thead><tr><th>模块</th><th>专业回测要求</th></tr></thead><tbody>' + body + "</tbody></table></div>"


def _benchmark_table(rows: List[Dict[str, Any]]) -> str:
    body = []
    for row in rows:
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        benchmark = strict.get("benchmark") or {}
        if benchmark:
            body.append(
                "<tr>"
                + f"<td>{escape(str(row.get('title', '')))}</td>"
                + f"<td><code>{escape(str(benchmark.get('symbol', '')))}</code></td>"
                + f"<td>{escape(str(benchmark.get('coverage_start', '')))}</td>"
                + f"<td>{escape(str(benchmark.get('coverage_end', '')))}</td>"
                + f"<td>{escape(str(benchmark.get('rows', 0)))}</td>"
                + f"<td>{escape(str(benchmark.get('price_column', '')))}</td>"
                + f"<td>{escape(str(benchmark.get('fallback_used', False)))}</td>"
                + f"<td>{escape(_pct(benchmark.get('alpha')))}</td>"
                + f"<td>{escape(_fmt(benchmark.get('information_ratio')))}</td>"
                + "</tr>"
            )
    if not body:
        return "<p>本次没有记录 benchmark 比较。A 股报告必须优先使用 000300；只有缺失时才 fallback 到 510300。</p>"
    return _table(["Idea", "Benchmark", "起点", "终点", "行数", "价格列", "Fallback", "Alpha", "IR"], body)


def _go_no_go(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次没有写入 Go / No-Go 决策记录。</p>"
    items = []
    for row in sorted(rows, key=_ledger_sort_key):
        status = str(row.get("status", ""))
        decision = status if status in {"rejected", "needs_more_validation", "candidate", "paper_trading_candidate"} else "needs_more_validation"
        strict = (row.get("metrics") or {}).get("strict_backtest") or {}
        strict_state = "已记录严格 Backtester" if strict else "缺少严格 Backtester"
        items.append(
            "<article class=\"card\">"
            + f"<h3>{escape(str(row.get('title', 'Untitled Hypothesis')))}</h3>"
            + '<div class="meta">'
            + '<span class="label">决策</span>' + f'<span><span class="badge {escape(decision)}">{escape(decision)}</span></span>'
            + _meta("阶段", row.get("stage", ""))
            + _meta("原因", _decision_reason(row))
            + _meta("严格回测", strict_state)
            + _meta("实盘分配", "本流水线不允许自动实盘")
            + "</div></article>"
        )
    return '<div class="ledger">' + "\n".join(items) + "</div>"


def _walkforward_table(data: Dict[str, Any]) -> str:
    detail = data.get("walkforward_detail") or data.get("oos") or {}
    rows = []
    if isinstance(detail, dict) and detail:
        rows.append(
            "<tr>"
            + f"<td>{escape(str(data.get('run_id') or 'research_pipeline'))}</td>"
            + f"<td>{escape(str(detail.get('verdict', '')))}</td>"
            + f"<td>{escape(_fmt(detail.get('aggregate_oos_sharpe')))}</td>"
            + f"<td>{escape(_fmt(detail.get('worst_oos_sharpe')))}</td>"
            + f"<td>{escape(_pct(detail.get('pct_profitable_splits')))}</td>"
            + f"<td>{escape(_fmt(detail.get('deflated_sharpe_ratio')))}</td>"
            + f"<td>{escape(str(detail.get('reason', '')))}</td>"
            + "</tr>"
        )
    if not rows:
        return "<p>本次没有记录 purged walk-forward 结果。完整研究结论至少需要报告 OOS split、最差 OOS Sharpe、盈利 split 占比和 DSR。</p>"
    return _table(["策略", "结论", "Aggregate OOS Sharpe", "Worst OOS Sharpe", "盈利Split占比", "DSR", "原因"], rows)


def _walkforward_methodology() -> str:
    rows = [
        ("目的", "用时间顺序切分训练/验证区间，检验策略是否只在某一段历史样本内有效。"),
        ("Purged", "训练集和测试集之间留出 embargo / purge gap，避免持仓 horizon、标签重叠或信息泄漏。"),
        ("Walk-forward", "按时间向前滚动多段 OOS split，每段只使用过去信息决定参数或信号状态。"),
        ("通过标准", "不能只看平均值，还要看最差 split、盈利 split 占比、Sharpe 衰减、DSR 和 regime breakdown。"),
        ("报告口径", "A 股可部署结果仍以 long-only 为准；long-short split 只作为诊断。"),
    ]
    body = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rows)
    return '<div class="table-wrap"><table><thead><tr><th>项目</th><th>说明</th></tr></thead><tbody>' + body + "</tbody></table></div>"


def _walkforward_detail_table(data: Dict[str, Any]) -> str:
    detail = data.get("walkforward_detail") or data.get("oos") or {}
    if not isinstance(detail, dict) or not detail:
        return "<p>本次没有保存 split 级别的 walk-forward 细节。后续正式报告应保存每个 split 的起止日期、样本内/样本外指标、参数和 regime。</p>"
    rows = [
        ("Split 数量", str(detail.get("n_splits", "n/a")), "OOS 分段数量，越少越容易受偶然区间影响。"),
        ("Top excess avg Sharpe", _fmt(detail.get("top_excess_vs_benchmark_avg_sharpe")), "long-only Top 20 相对 benchmark 的 OOS 平均 Sharpe。"),
        ("Top excess positive split", _pct(detail.get("top_excess_vs_benchmark_pct_positive")), "OOS split 中超额收益为正的比例。"),
        ("Long-short avg Sharpe diagnostic", _fmt(detail.get("long_short_avg_sharpe_diagnostic")), "仅用于 alpha 诊断，不作为 A 股可部署组合。"),
        ("Long-short positive split diagnostic", _pct(detail.get("long_short_pct_positive_diagnostic")), "long-short 诊断在 OOS split 中为正的比例。"),
    ]
    body = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td><td>{escape(note)}</td></tr>" for k, v, note in rows)
    return '<div class="table-wrap"><table><thead><tr><th>指标</th><th>数值</th><th>解释</th></tr></thead><tbody>' + body + "</tbody></table></div>"


def _walkforward_metric_explanations() -> str:
    rows = [
        ("Aggregate OOS Sharpe", "所有 OOS split 合并后的风险调整收益，反映整体样本外表现。"),
        ("Worst OOS Sharpe", "最差样本外 split 的 Sharpe。专业研究不能只看平均，最差区间决定策略抗压能力。"),
        ("盈利Split占比", "OOS split 中收益或超额收益为正的比例，用来衡量跨时间稳定性。"),
        ("DSR", "Deflated Sharpe Ratio，用来校正多次试验、非正态和样本长度带来的 Sharpe 高估。"),
        ("Sharpe Degradation", "样本内到样本外的 Sharpe 衰减。若衰减过大，通常意味着过拟合或 regime 依赖。"),
        ("Regime Breakdown", "按牛/熊/震荡、高低波动等 regime 拆分，检查信号是否只依赖单一市场环境。"),
    ]
    body = "".join(f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in rows)
    return '<div class="table-wrap"><table><thead><tr><th>指标</th><th>解释</th></tr></thead><tbody>' + body + "</tbody></table></div>"


def _next_steps_list(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        items = ["补齐 idea 搜集、准入评分、StrategySpec、真实数据验证和严格回测后再形成研究结论。"]
        return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"
    statuses = {str(row.get("status", "")) for row in rows}
    items = []
    if "paper_trading_candidate" in statuses:
        items.append("进入 paper trading 复核，但仍需容量、组合相关性、风控阈值和实盘分配审批。")
    if "candidate" in statuses:
        items.append("candidate 进入候选池，下一步补充更长 OOS、容量估计、参数稳定性和组合层相关性分析。")
    if "needs_more_validation" in statuses or "validated" in statuses or "idea_candidate" in statuses:
        items.append("needs_more_validation / validated / idea_candidate 不允许上线，优先补充 purged walk-forward、严格 Backtester 和 benchmark 归因。")
    if "rejected" in statuses:
        items.append("rejected idea 保留审计记录，不进入候选池；如需重启研究，必须有新的经济学假设、数据口径或样本外证据。")
    items.append("任何新策略默认保持研究产物或候选状态，不自动进入实盘分配。")
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _artifact_links(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return (
            "<ul>"
            "<li>Fast research report: <code>quant/infrastructure/var/research/reports/latest/fast_research_report.html</code></li>"
            "<li>Strict backtest report: <code>quant/infrastructure/var/research/reports/latest/strict_backtest_report.html</code></li>"
            "<li>Walk-forward audit report: <code>quant/infrastructure/var/research/reports/latest/walkforward_audit_report.html</code></li>"
            "<li>Idea bank: <code>quant/infrastructure/var/research/idea_bank/idea_bank.json</code></li>"
            "</ul>"
        )
    items = [
        "<li>Latest fast research report: <code>quant/infrastructure/var/research/reports/latest/fast_research_report.html</code></li>",
        "<li>Latest strict backtest report: <code>quant/infrastructure/var/research/reports/latest/strict_backtest_report.html</code></li>",
        "<li>Latest walk-forward audit report: <code>quant/infrastructure/var/research/reports/latest/walkforward_audit_report.html</code></li>",
        "<li>Idea bank: <code>quant/infrastructure/var/research/idea_bank/idea_bank.json</code></li>",
    ]
    seen = set()
    for row in sorted(rows, key=_ledger_sort_key):
        spec = (row.get("evidence") or {}).get("strategy_spec") or {}
        strategy_id = str(row.get("strategy_id") or spec.get("strategy_id") or "").strip()
        if not strategy_id or strategy_id in seen:
            continue
        seen.add(strategy_id)
        if row.get("status") == "rejected":
            items.extend(
                [
                    f"<li>Rejected strategy archive: <code>quant/features/rejected_strategy/{escape(strategy_id)}/strategy.py</code></li>",
                    f"<li>Rejected strategy config: <code>quant/features/rejected_strategy/{escape(strategy_id)}/config.yaml</code></li>",
                ]
            )
        elif row.get("status") not in {"error", "needs_manual_spec"}:
            items.extend(
                [
                    f"<li>Strategy code: <code>quant/features/strategies/{escape(strategy_id)}/strategy.py</code></li>",
                    f"<li>Config: <code>quant/features/strategies/{escape(strategy_id)}/config.yaml</code></li>",
                ]
            )
        items.extend(
            [
                f"<li>Fast research report: <code>quant/infrastructure/var/research/reports/{escape(strategy_id)}/fast_research_report.html</code></li>",
                f"<li>Strict backtest report: <code>quant/infrastructure/var/research/reports/{escape(strategy_id)}/strict_backtest_report.html</code></li>",
                f"<li>Walk-forward audit report: <code>quant/infrastructure/var/research/reports/{escape(strategy_id)}/walkforward_audit_report.html</code></li>",
            ]
        )
    return "<ul>" + "".join(items) + "</ul>"


def _table(headers: List[str], body_rows: List[str]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    return '<div class="table-wrap"><table><thead><tr>' + head + "</tr></thead><tbody>" + "".join(body_rows) + "</tbody></table></div>"


def _decision_reason(row: Dict[str, Any]) -> str:
    reason = str(row.get("decision_reason", "") or "")
    if reason and not _looks_corrupt_text(reason):
        return reason
    return _fallback_decision_reason(row)


def _fallback_decision_reason(row: Dict[str, Any]) -> str:
    status = str(row.get("status", "") or "")
    metrics = row.get("metrics") or {}
    strict = metrics.get("strict_backtest") or {}
    fdr_p = _safe_float(metrics.get("fdr_adjusted_p"))
    rank_ic = _safe_float(metrics.get("rank_ic"))
    if status == "rejected":
        if fdr_p is not None and fdr_p > 0.05:
            return "信号 FDR 不显著，严格回测结果不能单独支持进入实盘；保留为研究组件。"
        if rank_ic is not None and rank_ic < 0.02:
            return "Rank IC 未达到准入阈值，暂不进入候选池。"
        if not strict:
            return "缺少严格 Backtester 结果，不能形成可上线结论。"
        return "未通过 Go / No-Go 标准，不进入候选池。"
    if status == "candidate":
        return "通过当前研究门槛，进入候选池继续做稳定性、容量和组合层验证。"
    if status == "paper_trading_candidate":
        return "可进入 paper trading 候选，但仍需人工复核、容量评估和风控审批。"
    if status in {"needs_more_validation", "validated", "idea_candidate"}:
        return "需要补充正式回测、purged walk-forward、容量或稳健性验证。"
    if status == "needs_manual_spec":
        return "StrategySpec 尚未 ready，需要人工补全信号定义和执行约束。"
    return "研究记录缺少明确决策原因，需要补充审计说明。"


def _looks_corrupt_text(text: str) -> bool:
    if "�" in text:
        return True
    compact = "".join(ch for ch in text if not ch.isspace())
    if not compact:
        return False
    question_count = compact.count("?")
    return question_count >= 3 and question_count / len(compact) >= 0.25


def _ledger_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次没有写入 hypothesis ledger。</p>"
    cards = []
    for row in sorted(rows, key=_ledger_sort_key):
        metrics = row.get("metrics") or {}
        evidence = row.get("evidence") or {}
        risk_flags = metrics.get("risk_flags") or []
        validation_tests = metrics.get("validation_tests") or []
        status = str(row.get("status", ""))
        source = _source_link(row)
        cards.append(
            "\n".join(
                [
                    '<article class="card">',
                    f"<h3>{escape(str(row.get('title', 'Untitled Hypothesis')))}</h3>",
                    '<div class="meta">',
                    '<span class="label">状态</span>' + f'<span><span class="badge {escape(status)}">{escape(status)}</span></span>',
                    _meta("阶段", row.get("stage", "")),
                    _meta("Strategy ID", row.get("strategy_id", "") or "not_integrated", code=True),
                    '<span class="label">来源</span>' + f"<span>{source}</span>",
                    _meta("决策", _decision_reason(row)),
                    _meta("研究假设", row.get("thesis", "")),
                    _meta("来源质量", _fmt(evidence.get("discovery_quality", {}).get("score"))),
                    _meta("Admission Score", _fmt(metrics.get("admission_score"))),
                    _meta("信号质量", _fmt(metrics.get("signal_quality_score"))),
                    _meta("研究置信度", _fmt(metrics.get("research_confidence_score"))),
                    _meta("Rank IC", _fmt(metrics.get("rank_ic"))),
                    _meta("Rank IC IR", _fmt(metrics.get("rank_ic_ir"))),
                    _meta("FDR p-value", _fmt(metrics.get("fdr_adjusted_p"))),
                    _meta("Hit Rate", _fmt(metrics.get("hit_rate"))),
                    _meta("风险旗标", ", ".join(risk_flags) if risk_flags else "无"),
                    _meta("验证清单", ", ".join(validation_tests) if validation_tests else "无"),
                    "</div>",
                    "</article>",
                ]
            )
        )
    return '<div class="ledger">' + "\n".join(cards) + "</div>"


def _log_table(log_rows: List[Dict[str, Any]]) -> str:
    if not log_rows:
        return "<p>本次没有记录 pipeline log。</p>"
    rows = []
    for entry in log_rows:
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{escape(_cell(entry.get(key, '')))}</td>"
                for key in ("phase", "verdict", "title", "reason")
            )
            + "</tr>"
        )
    return '<div class="table-wrap"><table><thead><tr><th>阶段</th><th>结论</th><th>标题</th><th>原因</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


def _requirements_list() -> str:
    items = [
        "阶段一必须保留 source notes、来源质量评分、admission_score、risk flags 和 hypothesis ledger。",
        "阶段一通过的 idea 必须写成明确 StrategySpec：信号公式、预测方向、lookback、holding horizon、调仓频率、universe、字段需求和验证清单。",
        "阶段二必须写清数据 lineage、universe 定义、后复权政策和样本覆盖。",
        "阶段二必须报告信号定义、方向、IC、IC decay、FDR、hit rate、OOS 稳定性和敏感性检查。",
        "A 股报告里 long-short 只能标记为不可部署 alpha 诊断；可部署组合必须是 long-only。",
        "Strict framework backtest report 必须包含 Sharpe、Sortino、CAGR、MaxDD、Win Rate、Profit Factor、成本、拒单、成交和产物链接。",
        "A 股报告必须优先使用 000300 CSI 300 index 作为 benchmark；只有 daily_cn_ochl 缺失 000300 时才 fallback 到 510300，并写清 benchmark 覆盖区间。",
        "最终只给 reject / needs_more_validation / candidate / paper_trading_candidate，不自动进入实盘分配。",
    ]
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _meta(label: str, value: Any, code: bool = False) -> str:
    text = escape(str(value))
    value_html = f"<code>{text}</code>" if code else text
    return f'<span class="label">{escape(label)}</span><span>{value_html}</span>'


def _source_link(row: Dict[str, Any]) -> str:
    label = str(row.get("source", "") or row.get("source_url", "") or "source")
    url = str(row.get("source_url", "") or "")
    if not url:
        return escape(label)
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def _ledger_sort_key(row: Dict[str, Any]) -> tuple:
    rank = {
        "candidate": 0,
        "idea_candidate": 1,
        "validated": 1,
        "needs_more_validation": 2,
        "needs_manual_spec": 3,
        "rejected": 4,
        "error": 5,
        "skipped": 6,
    }.get(row.get("status", ""), 9)
    updated = row.get("updated_at") or ""
    return rank, updated, row.get("title", "")


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _compact_money(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    abs_value = abs(number)
    if abs_value >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{number / 1_000:.0f}K"
    return f"{number:.0f}"


def _compact_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    abs_value = abs(number)
    if abs_value >= 100:
        return f"{number:.0f}"
    if abs_value >= 10:
        return f"{number:.1f}"
    return f"{number:.2f}"


def _curve_points(value: Any) -> List[tuple[str, float]]:
    if not isinstance(value, list):
        return []
    points: List[tuple[str, float]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        date_text = str(item.get("date") or "")[:10]
        number = _safe_float(item.get("value"))
        if not date_text or number is None or not math.isfinite(number):
            continue
        points.append((date_text, number))
    return sorted(points, key=lambda item: item[0])


def _normalize_curve_points(points: List[tuple[str, float]]) -> List[tuple[str, float]]:
    base = next((value for _, value in points if value > 0 and math.isfinite(value)), None)
    if base is None:
        return []
    normalized = []
    for date_text, value in points:
        if math.isfinite(value):
            normalized.append((date_text, value / base * 100.0))
    return normalized


def _downsample_curve_points(points: List[tuple[str, float]], max_points: int = 620) -> List[tuple[str, float]]:
    if len(points) <= max_points or max_points < 2:
        return points
    sampled: List[tuple[str, float]] = []
    previous_idx = -1
    for position in range(max_points):
        idx = round(position * (len(points) - 1) / (max_points - 1))
        if idx != previous_idx:
            sampled.append(points[idx])
            previous_idx = idx
    return sampled


def _svg_path(points: List[tuple[str, float]], x_for: Any, y_for: Any) -> str:
    commands = []
    for idx, (date_text, value) in enumerate(points):
        command = "M" if idx == 0 else "L"
        commands.append(f"{command}{x_for(date_text):.2f},{y_for(value):.2f}")
    return " ".join(commands)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text[:240] + "..." if len(text) > 240 else text
