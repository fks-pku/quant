from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, Iterable, List


def build_full_research_report_html(
    result: Any,
    hypotheses: Iterable[Dict[str, Any]],
    generated_at: str | None = None,
) -> str:
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    rows = list(hypotheses or [])
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    run_id = str(data.get("run_id") or "research_pipeline")
    body = [
        '<section class="hero">',
        '<p class="eyebrow">Two-Stage Quant Research Pipeline</p>',
        "<h1>完整研究报告</h1>",
        f"<p>生成时间 {escape(generated)}，Run ID <code>{escape(run_id)}</code>。本报告固定采用 8 章中文格式：结论、来源、信号、数据与 benchmark、信号验证、策略回测、purged walk-forward、最终推荐。</p>",
        "</section>",
        '<section class="panel">',
        "<h2>1. 结论汇总</h2>",
        "<h3>一句话结论</h3>",
        _conclusion_summary(data, rows),
        _summary_table(data),
        "</section>",
        '<section class="panel">',
        "<h2>2. idea 来源与初筛</h2>",
        "<p>本章对应 Stage 1：从 arXiv / SSRN / NBER / blog 等来源搜集多个 idea，先做来源质量评分，再以 admission_score 为准判断是否进入正式研究队列。</p>",
        _idea_discovery_table(rows),
        "<h3>来源质量与准入评分</h3>",
        _admission_table(rows),
        "<h3>初筛风险旗标</h3>",
        _risk_flags_list(rows),
        "</section>",
        '<section class="panel">',
        "<h2>3. 信号定义</h2>",
        "<p>通过初筛的 idea 必须被翻译成可执行 StrategySpec：信号公式、方向、lookback、holding horizon、调仓频率、universe、字段需求和验证清单必须明确。</p>",
        _spec_table(rows),
        "<h3>信号公式</h3>",
        _signal_definition_detail(rows),
        "<h3>交易解释</h3>",
        _signal_implementation_checks(rows),
        "</section>",
        '<section class="panel">',
        "<h2>4. 数据来源及 benchmark 定义</h2>",
        "<p>研究判断逻辑统一使用 DuckDB <code>daily_cn</code> 中的后复权价格：优先 <code>adj_*</code>，缺失时才使用 raw price × <code>adj_factor</code>。A 股默认 benchmark 是 <code>000300</code> 沪深 300（000300 CSI 300 index）；只有 DB 缺失 000300 时才 fallback 到 <code>510300</code>。</p>",
        _data_benchmark_definition(rows),
        _benchmark_table(rows),
        "<h3>数据质量检查</h3>",
        _data_quality_checks(rows),
        "</section>",
        '<section class="panel">',
        "<h2>5. 信号验证</h2>",
        "<p>本章只回答信号本身是否有研究价值，重点看 rank IC、ICIR、FDR、hit rate、IC decay、OOS 稳定性。这里的 long-short 只允许作为 alpha 诊断，不允许作为 A 股可部署组合。</p>",
        _signal_validation_table(rows),
        _signal_validation_judgement(rows),
        _signal_validation_metric_explanations(),
        "<h3>组合诊断</h3>",
        _portfolio_diagnostics_table(rows),
        "</section>",
        '<section class="panel">',
        "<h2>6. 策略回测报告</h2>",
        "<p>Strict framework backtest report：正式结论必须来自项目 Backtester + DataFrameProvider + Strategy + Portfolio/RiskEngine/SubPortfolio，并纳入 T+1、手续费、滑点、A 股 100 股手数、涨跌停拒单、成交量限制、风险约束和交易产物。</p>",
        "<h3>回测配置</h3>",
        _backtest_requirements_table(),
        "<h3>核心绩效</h3>",
        _strict_backtest_table(rows),
        _backtest_return_risk_table(rows),
        _backtest_stat_benchmark_table(rows),
        _yearly_returns_table(rows),
        "<h3>成交与成本诊断</h3>",
        _backtest_trade_cost_constraints_table(rows),
        "</section>",
        '<section class="panel">',
        "<h2>7. purged walk-forward</h2>",
        "<p>本章用于检查样本外稳定性、Sharpe 衰减、最差 OOS split、盈利 split 占比和 deflated Sharpe ratio。通过信号验证并不等于通过 purged walk-forward。</p>",
        "<h3>方法设置</h3>",
        _walkforward_methodology(),
        "<h3>结果摘要</h3>",
        _walkforward_table(data),
        _walkforward_metric_explanations(),
        "<h3>Split 明细</h3>",
        _walkforward_detail_table(data),
        "</section>",
        '<section class="panel">',
        "<h2>8. 最终推荐与下一步计划</h2>",
        "<h3>推荐理由</h3>",
        _go_no_go(rows),
        "<h3>下一步计划</h3>",
        _next_steps_list(rows),
        "<h3>产物链接</h3>",
        _artifact_links(rows),
        "<details><summary>审计 Ledger</summary>",
        _ledger_html(rows),
        "</details>",
        "<details><summary>Pipeline Log</summary>",
        _log_table(data.get("log") or []),
        "</details>",
        "</section>",
    ]
    return _html_document("完整研究报告", "\n".join(body))


def build_full_research_report_index(
    result: Any,
    html_filename: str,
    generated_at: str | None = None,
) -> str:
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    run_id = data.get("run_id") or "research_pipeline"
    return "\n".join(
        [
            "# 完整研究报告",
            "",
            f"详细报告：[{html_filename}]({html_filename})",
            "",
            f"- 生成时间：{generated}",
            f"- Run ID: `{run_id}`",
            f"- 已搜集 idea：{data.get('discovered', 0)}",
            f"- 已评估 idea：{data.get('evaluated', 0)}",
            f"- 已集成策略：{data.get('integrated', 0)}",
            f"- 已拒绝：{data.get('rejected', 0)}",
            "",
            "复杂研究报告统一使用 HTML 呈现。轻量说明和 AGENTS 文件可继续使用 Markdown。",
            "",
        ]
    )


def build_full_research_report(
    result: Any,
    hypotheses: Iterable[Dict[str, Any]],
    generated_at: str | None = None,
) -> str:
    return build_full_research_report_html(result, hypotheses, generated_at=generated_at)


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
            ":root{color-scheme:light;--ink:#172026;--muted:#66737f;--line:#d7dde2;--paper:#f7f4ed;--panel:#fffdfa;--accent:#0f766e;--warn:#b45309;--bad:#991b1b;--good:#166534;}",
            "*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:'Microsoft YaHei','PingFang SC','Noto Sans SC','Source Han Sans SC','Segoe UI',system-ui,sans-serif;line-height:1.62;font-variant-numeric:tabular-nums lining-nums;font-feature-settings:'tnum' 1,'lnum' 1;letter-spacing:0}main{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:40px 0 64px}.hero{border-bottom:2px solid var(--ink);padding:28px 0 32px;margin-bottom:28px}.eyebrow{font:700 12px/1.2 'Segoe UI',system-ui,sans-serif;text-transform:uppercase;letter-spacing:.05em;color:var(--accent);margin:0 0 16px}.hero h1{font-size:42px;line-height:1.12;margin:0 0 14px;font-weight:750;letter-spacing:0}.hero p{max-width:860px;color:var(--muted);font-size:17px}.panel{background:var(--panel);border:1px solid var(--line);padding:24px;margin:18px 0}.panel h2{font-size:25px;line-height:1.25;margin:0 0 18px;border-bottom:1px solid var(--line);padding-bottom:10px;font-weight:720}.panel h3{font-size:18px;line-height:1.35;margin:22px 0 10px;font-weight:720}.panel h3:first-child{margin-top:0}.standard p{max-width:920px}.table-wrap{overflow-x:auto;margin-bottom:14px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}th{font:700 12px/1.2 'Segoe UI',system-ui,sans-serif;text-transform:uppercase;letter-spacing:.04em;color:#34414c;background:#f0ece3}code{font-family:'Cascadia Mono','Consolas','SFMono-Regular',monospace;background:#eee7da;padding:2px 5px;border-radius:4px}.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}.card{border:1px solid var(--line);background:#fff;padding:18px}.card h3{margin:0 0 12px;font-size:19px;line-height:1.3}.meta{display:grid;grid-template-columns:145px 1fr;gap:7px 12px;font-size:14px}.label{color:var(--muted);font-family:'Segoe UI',system-ui,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:.04em}.badge{display:inline-block;border:1px solid var(--line);padding:2px 8px;border-radius:999px;font:700 12px/1.4 'Segoe UI',system-ui,sans-serif}.badge.candidate,.badge.validated{color:var(--good);border-color:#86efac;background:#ecfdf5}.badge.idea_candidate{color:#155e75;border-color:#67e8f9;background:#ecfeff}.badge.rejected,.badge.error{color:var(--bad);border-color:#fecaca;background:#fef2f2}.badge.needs_more_validation,.badge.needs_manual_spec{color:var(--warn);border-color:#fed7aa;background:#fff7ed}ul{margin:0;padding-left:22px}a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:3px}@media(max-width:720px){main{width:min(100% - 24px,1180px);padding-top:24px}.hero h1{font-size:32px}.panel{padding:18px}.meta{grid-template-columns:1fr}}",
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
    return "信号值越高，预期未来收益越高；组合只允许选 top bucket long-only。"


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
            "4. 每个截面按预测值排序；5. 只允许买入 top bucket，禁止 A 股 long-short 部署。"
        )
    if "momentum" in formula:
        return f"1. 用后复权价格计算过去 {lookback} 日收益；2. 截面排序；3. 选择高动量标的；4. 下一交易日执行。"
    if "reversal" in formula or "mean" in formula:
        return f"1. 计算价格相对均值或近期收益偏离；2. 截面排序；3. 选择反转概率最高标的；4. 下一交易日执行。"
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
        ("Hit Rate", "top bucket 或信号方向预测为正的比例。它不等于胜率，但能辅助判断信号方向是否一致。"),
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
        return "缺少 top bucket long-only 诊断。"
    ann = _safe_float(diag.get("top_bucket_annualized_return"))
    excess = _safe_float(diag.get("benchmark_excess_after_cost_mean_return"))
    benchmark = str(diag.get("benchmark_symbol", "") or "")
    parts = []
    if ann is not None:
        parts.append(f"top bucket 近似年化 {ann:.2%}")
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
            + "<td>DuckDB daily_cn</td>"
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
        return "<p>本次没有足够的数据覆盖或 benchmark 记录。完整 A 股报告必须写清 daily_cn 覆盖、后复权口径和 000300/510300 fallback 决策。</p>"
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
            + f"<td>{escape(_pct(diag.get('top_bucket_after_cost_mean_return')))}</td>"
            + f"<td>{escape(str(diag.get('benchmark_symbol', '')))}</td>"
            + f"<td>{escape(_pct(diag.get('benchmark_excess_after_cost_mean_return')))}</td>"
            + f"<td>{escape(rolling_text or 'n/a')}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有记录 long-only 组合诊断；这会阻断完整研究结论。</p>"
    return _table(["Idea", "诊断类型", "Top 年化", "Top Hit", "成本后均值", "Benchmark", "成本后超额", "Rolling OOS"], body)


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
            + f"<td>{escape(_pct(metrics.get('win_rate')))}</td>"
            + f"<td>{escape(_fmt(metrics.get('profit_factor')))}</td>"
            + f"<td>{escape(str(metrics.get('total_trades', 0)))}</td>"
            + f"<td>{escape(_fmt(diagnostics.get('total_commission')))}</td>"
            + f"<td>{escape(str(diagnostics.get('limit_rejected_orders', 0)))}/{escape(str(diagnostics.get('t1_rejected_sells', 0)))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>本次没有记录严格 Backtester 结果；这会阻断 Go / No-Go。</p>"
    return _table(["Idea", "区间", "Sharpe", "Sortino", "CAGR", "累计", "MaxDD", "胜率", "PF", "成交数", "手续费", "涨跌停/T+1拒单"], body)


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
            + f"<td>{escape(_fmt(metrics.get('calmar')))}</td>"
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
            + f"<td>{escape(str(constraints.get('cn_lot_size', 100)))}</td>"
            + "</tr>"
        )
    if not body:
        return "<p>缺少交易、成本或执行约束诊断。</p>"
    return _table(["Idea", "成交/Fill数", "Round Trips", "胜率", "Profit Factor", "Payoff", "Expectancy", "总佣金", "成本拖累", "成交量限制", "涨跌停拒单", "T+1拒单", "手数"], body)


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
    log_rows = data.get("log") or []
    rows = []
    for entry in log_rows:
        if not isinstance(entry, dict):
            continue
        phase = str(entry.get("phase", ""))
        if phase not in {"rigor", "walkforward", "purged_walk_forward"}:
            continue
        scores = entry.get("scores") or {}
        rows.append(
            "<tr>"
            + f"<td>{escape(str(entry.get('title', '')))}</td>"
            + f"<td>{escape(str(entry.get('verdict', '')))}</td>"
            + f"<td>{escape(_fmt(scores.get('aggregate_oos_sharpe')))}</td>"
            + f"<td>{escape(_fmt(scores.get('worst_oos_sharpe')))}</td>"
            + f"<td>{escape(_pct(scores.get('pct_profitable_splits')))}</td>"
            + f"<td>{escape(_fmt(scores.get('deflated_sharpe_ratio')))}</td>"
            + f"<td>{escape(str(entry.get('reason', '')))}</td>"
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
        ("Top excess avg Sharpe", _fmt(detail.get("top_excess_vs_benchmark_avg_sharpe")), "long-only top bucket 相对 benchmark 的 OOS 平均 Sharpe。"),
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
            "<li>Report: <code>quant/infrastructure/var/research/reports/latest/full_research_report.html</code></li>"
            "<li>Idea bank: <code>quant/infrastructure/var/research/idea_bank/idea_bank.json</code></li>"
            "</ul>"
        )
    items = [
        "<li>Latest report: <code>quant/infrastructure/var/research/reports/latest/full_research_report.html</code></li>",
        "<li>Idea bank: <code>quant/infrastructure/var/research/idea_bank/idea_bank.json</code></li>",
    ]
    seen = set()
    for row in sorted(rows, key=_ledger_sort_key):
        spec = (row.get("evidence") or {}).get("strategy_spec") or {}
        strategy_id = str(row.get("strategy_id") or spec.get("strategy_id") or "").strip()
        if not strategy_id or strategy_id in seen:
            continue
        seen.add(strategy_id)
        items.extend(
            [
                f"<li>Strategy code: <code>quant/features/strategies/{escape(strategy_id)}/strategy.py</code></li>",
                f"<li>Config: <code>quant/features/strategies/{escape(strategy_id)}/config.yaml</code></li>",
                f"<li>Report: <code>quant/infrastructure/var/research/reports/{escape(strategy_id)}/full_research_report.html</code></li>",
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
        "A 股报告必须优先使用 000300 CSI 300 index 作为 benchmark；只有 daily_cn 缺失 000300 时才 fallback 到 510300，并写清 benchmark 覆盖区间。",
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


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
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
