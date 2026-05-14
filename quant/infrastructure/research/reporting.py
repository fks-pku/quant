from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List


_REPORT_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "var"
    / "research"
    / "report_templates"
    / "full_research_report_template.html"
)
_FALLBACK_REPORT_TEMPLATE_PATH = Path(__file__).resolve().parent / "golden_reports" / "full_research_report_template.html"


def build_full_research_report_html(
    result: Any,
    hypotheses: Iterable[Dict[str, Any]],
    generated_at: str | None = None,
) -> str:
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    rows = list(hypotheses or [])
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    run_id = str(data.get("run_id") or "research_pipeline")
    report_title = _report_title(rows)
    body = [
        '<section class="hero">',
        '<p class="eyebrow">Two-Stage Quant Research Pipeline</p>',
        f"<h1>{escape(report_title)}</h1>",
        f"<p>生成时间 {escape(generated)}，Run ID <code>{escape(run_id)}</code>。本报告按 <code>quant/infrastructure/var/research/report_templates/full_research_report_template.html</code> 渲染，区分 idea 初筛、信号验证、组合诊断、严格 Backtester、purged walk-forward 和最终 Go / No-Go。</p>",
        '<div class="template-note">模板字段已替换为本次研究的实际数据；报告不得保留方括号占位符，也不得用轻量组合诊断替代正式结论。</div>',
        "</section>",
        '<section class="panel">',
        "<h2>1. 结论汇总</h2>",
        _report_metric_grid(rows, generated),
        "<h3>一句话结论</h3>",
        _conclusion_paragraph(data, rows),
        _judgement_table(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>2. idea 来源与初筛</h2>",
        "<p>本节回答：这个 idea 从哪里来，是否足够可信，为什么值得进入正式研究。第一阶段只做搜集、来源质量评分、admission 评估和 StrategySpec 草拟，不跑正式验证。</p>",
        _idea_source_overview_table(rows),
        "<h3>来源质量与准入评分</h3>",
        _source_quality_score_table(data, rows),
        "<h3>初筛风险旗标</h3>",
        _risk_flags_list(rows),
        "</section>",
        '<section class="panel">',
        "<h2>3. 信号定义</h2>",
        "<p>本节必须把 idea 翻译成可审计的 StrategySpec。这里要足够详细，让别人不读代码也能复现信号方向和调仓逻辑。</p>",
        _strategy_spec_contract_table(rows),
        "<h3>信号公式</h3>",
        _formula_block(rows),
        "<h3>交易解释</h3>",
        _trade_explanation_list(rows),
        "</section>",
        '<section class="panel">',
        "<h2>4. 数据来源及 benchmark 定义</h2>",
        "<p>本节回答：用了什么数据、覆盖期是什么、后复权处理是否严格、benchmark 是否为 A 股默认的沪深 300。</p>",
        _data_source_contract_table(data, rows),
        "<h3>数据质量检查</h3>",
        _data_quality_contract_list(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>5. 信号验证</h2>",
        "<p>本节验证信号本身是否有统计研究价值，不等同于正式回测。核心是横截面预测能力、稳健性、显著性和方向一致性。</p>",
        _signal_validation_contract_table(data, rows),
        "<h3>组合诊断</h3>",
        _portfolio_diagnostics_contract_table(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>6. 策略回测报告</h2>",
        "<p>正式结论必须来自项目 Backtester：<code>Backtester + DataFrameProvider + Strategy + Portfolio/RiskEngine/SubPortfolio</code>。本节回答真实交易约束下策略是否仍成立。</p>",
        "<h3>回测配置</h3>",
        _backtest_config_contract_table(data, rows),
        "<h3>核心绩效</h3>",
        _core_performance_contract_table(data, rows),
        "<h3>成交与成本诊断</h3>",
        _trade_cost_contract_table(data, rows),
        "</section>",
        '<section class="panel">',
        "<h2>7. purged walk-forward</h2>",
        "<p>Purged walk-forward 用来检查参数和信号是否在滚动样本外稳定。这里的“训练”不是机器学习训练的必要含义，而是指每个窗口内用于确定参数、阈值或组合规则的历史区间；即使策略没有 ML，也要防止未来信息泄露。</p>",
        "<h3>方法设置</h3>",
        _walkforward_methodology_contract_table(rows),
        "<h3>结果摘要</h3>",
        _walkforward_summary_contract_table(data),
        "<h3>Split 明细</h3>",
        _walkforward_split_contract_table(data),
        "</section>",
        '<section class="panel">',
        "<h2>8. 最终推荐与下一步计划</h2>",
        _decision_contract(data, rows),
        "<h3>下一步计划</h3>",
        _next_steps_contract_table(rows),
        "<h3>产物链接</h3>",
        _artifact_links(rows),
        "</section>",
    ]
    return _html_document(report_title, "\n".join(body))


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


def _report_title(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    title = str(row.get("title") or "").strip()
    strategy_id = _row_strategy_id(row)
    subject = title or strategy_id or "策略"
    return f"{subject} 完整策略研究报告"


def _primary_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    return sorted(rows, key=_ledger_sort_key)[0]


def _row_strategy_id(row: Dict[str, Any]) -> str:
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    return str(row.get("strategy_id") or spec.get("strategy_id") or "not_integrated")


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


def _conclusion_paragraph(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>本次运行没有形成可审计的 hypothesis 记录，因此不能给出策略推荐。</p>"
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    wf_scores, wf_reason, wf_verdict = _walkforward_scores(data)
    status = str(row.get("status") or "needs_more_validation")
    strategy_id = _row_strategy_id(row)
    rank_ic = _fmt(metrics.get("rank_ic"))
    fdr = _fmt(metrics.get("fdr_adjusted_p"))
    sharpe = _fmt(strict_metrics.get("sharpe"))
    cagr = _pct(strict_metrics.get("cagr"))
    aggregate = _fmt(wf_scores.get("aggregate_oos_sharpe"))
    worst = _fmt(wf_scores.get("worst_oos_sharpe"))
    if status == "rejected":
        if _signal_badge_class(metrics) == "fail":
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
            f"{strategy_id} 当前状态为 {status}，已完成信号验证、严格回测和样本外稳定性审查。"
            "进入下一阶段前仍需人工复核容量、组合相关性、风控预算和实盘执行细节。"
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
    wf_scores, wf_reason, _ = _walkforward_scores(data)
    signal_class = _signal_badge_class(metrics)
    strict_class = _strict_badge_class(strict_metrics)
    walk_class = _walkforward_badge_class(wf_scores)
    deploy_class = _status_badge_class(_report_status(rows))
    body = [
        "<tr><td>信号验证</td>"
        + f"<td>{_badge(signal_class, signal_class)}</td>"
        + f"<td>{escape(_signal_validation_summary(metrics))}</td></tr>",
        "<tr><td>严格回测</td>"
        + f"<td>{_badge(strict_class, strict_class)}</td>"
        + f"<td>{escape(_strict_backtest_summary(strict_metrics))}</td></tr>",
        "<tr><td>Walk-forward</td>"
        + f"<td>{_badge(walk_class, walk_class)}</td>"
        + f"<td>{escape(_walkforward_summary_sentence(wf_scores, wf_reason))}</td></tr>",
        "<tr><td>部署建议</td>"
        + f"<td>{_badge(_report_status(rows), deploy_class)}</td>"
        + f"<td>{escape(_deployment_summary(row))}</td></tr>",
    ]
    return _table(["判断项", "结果", "解释"], body)


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
        f"<tr><td>核心假设</td><td>{escape(str(row.get('thesis') or _decision_reason(row)))}</td><td>不能只写“可能赚钱”</td></tr>",
    ]
    return _table(["字段", "内容", "要求"], body)


def _source_quality_score_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    if not row:
        return "<p>本次没有写入准入评估记录。</p>"
    evidence = row.get("evidence") or {}
    quality = evidence.get("discovery_quality") or {}
    metrics = row.get("metrics") or {}
    score_rows = [
        ("source_quality", _first_present(quality, "source_quality_score", "source_quality"), "来源是否可信、是否有正式论文或可复现材料"),
        ("recency", _first_present(quality, "recency_score", "recency"), "发布时间与当前研究窗口的关系"),
        ("formula_clarity", _first_present(quality, "detail_score", "formula_clarity_score", "formula_clarity"), "是否有明确公式、排序方向、持有期"),
        ("daily_feasibility", _first_present(quality, "daily_data_score", "daily_feasibility_score", "daily_feasibility"), "是否可以用日线 OHLCV / 可得字段实现"),
        ("A 股适配性", _first_present(metrics, "data_availability_score", "cost_capacity_score", "implementation_score"), "是否适合 A 股 long-only、T+1、涨跌停和流动性约束"),
        ("admission_score", _stage1_score(data, "admission", metrics.get("admission_score")), "低于阈值不得进入正式研究"),
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
            f"target_i,t+{lag} = top_bucket(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    elif formula == "worldquant_alpha_002":
        lines = [
            "x_i,t = rank_cross_section(delta(log(volume_i,t), 2))",
            "y_i,t = rank_cross_section((adj_close_i,t - adj_open_i,t) / adj_open_i,t)",
            f"signal_i,t = -corr_ts(x_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, y_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback}:t, {lookback})",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_bucket(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    elif "mean_reversion" in formula or "reversal" in formula:
        lines = [
            f"ma_i,t = mean(adj_close_i,t-{int(lookback) - 1 if _safe_float(lookback) else lookback} ... adj_close_i,t)",
            "raw_reversal_i,t = (ma_i,t - adj_close_i,t) / ma_i,t",
            "industry_momentum_i,t = mean(industry_return_i,t-lookback ... industry_return_i,t)",
            "signal_i,t = raw_reversal_i,t with industry momentum hedge and cross-sectional winsorization",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_bucket(rank_i,t), long-only only",
            f"forward_return_i,t = adj_close_i,t+{int(lag) + int(horizon)} / adj_close_i,t+{lag} - 1",
        ]
    else:
        lines = [
            f"signal_i,t = {formula or 'StrategySpec declared signal'}",
            "rank_i,t = cross_sectional_rank(signal_i,t)",
            f"target_i,t+{lag} = top_bucket(rank_i,t), long-only only",
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
        f"<tr><td>Universe</td><td>{escape(_universe_summary(row))}</td><td>当前报告按 A 股 long-only 约束审计；PIT 成分股限制需在后续增强</td></tr>",
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
    wf_scores, _, _ = _walkforward_scores(data)
    rows_data = [
        ("Rank IC", _fmt(metrics.get("rank_ic")), "横截面秩相关；正负号必须与预测方向一致"),
        ("ICIR", _fmt(metrics.get("rank_ic_ir")), "IC 均值 / IC 波动，衡量稳定性"),
        (
            "Rank IC t-stat",
            _fmt(_first_present(metrics, "rank_ic_tstat", "t_stat")),
            "每日 IC 均值对 0 的 t 检验",
        ),
        (
            "p-value",
            _fmt(_first_present(metrics, "rank_ic_p_value", "p_value", "rank_ic_p", "fdr_adjusted_p")),
            "每日 IC 均值 t 检验 p 值",
        ),
        ("FDR adjusted p", _fmt(metrics.get("fdr_adjusted_p")), "多重检验控制；不过则不能给强结论"),
        ("Hit rate", _pct(metrics.get("hit_rate")), "预测方向命中率"),
        ("IC decay", _format_decay(metrics.get("ic_decay")), "看 alpha 半衰期与持有期是否匹配"),
        ("Fama-MacBeth t-stat", _fmt(metrics.get("fama_macbeth_tstat")), "横截面回归显著性"),
        ("Factor exposure", _factor_exposure_text(metrics), "是否只是已知风险因子暴露"),
        ("OOS validation", _signal_oos_text(metrics, wf_scores), "滚动样本外是否保持方向和显著性"),
    ]
    body = "".join(
        f"<tr><td>{escape(metric)}</td><td>{escape(str(value))}</td><td>{escape(note)}</td></tr>"
        for metric, value, note in rows_data
    )
    return _table(["Metric", "数值", "阈值/解释"], body)


def _portfolio_diagnostics_contract_table(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    metrics = row.get("metrics") or {}
    diag = metrics.get("portfolio_diagnostics") or {}
    strict = _strict_backtest_for_report(data, row)
    strict_metrics = strict.get("metrics") or {}
    benchmark = strict.get("benchmark") or {}
    rows_data = [
        (
            "Top bucket long-only",
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
    rows_data = [
        ("回测区间", strict.get("period") or "未记录", "必须与数据覆盖一致"),
        ("初始资金", strict.get("initial_cash") or metrics.get("initial_cash") or "500000 CNY", "A 股示例默认 500000 CNY"),
        ("调仓频率", strict.get("rebalance_frequency") or "daily signal with holding horizon gate", "影响换手"),
        ("滑点", f"{constraints.get('slippage_bps', 5)} bps", "默认配置"),
        ("佣金", _commission_text(constraints.get("commission")), "含最低佣金、印花税等"),
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
        ("CAGR", _pct(metrics.get("cagr")), _pct(benchmark.get("benchmark_cagr")), _pct(benchmark.get("alpha"))),
        ("Total Return", _pct(metrics.get("total_return")), _pct(benchmark.get("benchmark_return")), _pct(_excess(metrics.get("total_return"), benchmark.get("benchmark_return")))),
        ("Sharpe", _fmt(metrics.get("sharpe")), _fmt(benchmark.get("benchmark_sharpe")), "必须成本后"),
        ("Sortino", _fmt(metrics.get("sortino")), _fmt(benchmark.get("benchmark_sortino")), "下行风险调整"),
        ("Max Drawdown", _pct(metrics.get("max_drawdown_pct")), _pct(benchmark.get("benchmark_max_drawdown_pct")), "风险底线"),
        ("Calmar Ratio", _fmt(_first_present(metrics, "calmar_ratio", "calmar")), _fmt(_first_present(benchmark, "benchmark_calmar_ratio", "benchmark_calmar")), "CAGR / |Max Drawdown|"),
        ("Win Rate", _pct(metrics.get("win_rate")), "-", "按交易统计"),
        ("Profit Factor", _fmt(metrics.get("profit_factor")), "-", "总盈利 / 总亏损"),
        ("Total Trades", str(metrics.get("total_trades") or "n/a"), "-", "含拒单和成交诊断"),
        ("Information Ratio", _fmt(benchmark.get("information_ratio")), "-", "相对 000300"),
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
        ("lot_adjusted_trades", str(diagnostics.get("lot_adjusted_trades") or 0), "因 100 股手数调整的交易"),
        ("t1_rejected_sells", str(diagnostics.get("t1_rejected_sells") or 0), "T+1 拒绝卖出"),
        ("limit_rejected_orders", str(diagnostics.get("limit_rejected_orders") or 0), "涨跌停拒单"),
        ("volume_limited_trades", str(diagnostics.get("volume_limited_trades") or 0), "成交量限制"),
        ("risk_skipped_orders", str(diagnostics.get("risk_skipped_orders") or 0), "风控跳过订单"),
        ("final_suspended_holding_nav", _money(diagnostics.get("final_suspended_holding_nav")), suspended_note),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["诊断项", "数值", "解释"], body)


def _walkforward_methodology_contract_table(rows: List[Dict[str, Any]]) -> str:
    row = _primary_row(rows)
    spec = (row.get("evidence") or {}).get("strategy_spec") or {}
    horizon = spec.get("horizon_days") or 5
    lookback = spec.get("lookback_days") or 20
    rows_data = [
        ("train_window", "126 trading days", "用于参数/阈值确认"),
        ("test_window", "21 trading days", "样本外测试区间"),
        ("step", "21 trading days", "滚动步长"),
        ("purge_gap", f"{horizon} trading days", "训练和测试之间剔除重叠信息"),
        ("parameter_grid", f"lookback={lookback}; horizon={horizon}; frozen parameters", "若无参数优化，写明 frozen parameters"),
    ]
    body = "".join(
        f"<tr><td>{escape(item)}</td><td>{escape(value)}</td><td>{escape(note)}</td></tr>"
        for item, value, note in rows_data
    )
    return _table(["项目", "取值", "解释"], body)


def _walkforward_summary_contract_table(data: Dict[str, Any]) -> str:
    scores, reason, _ = _walkforward_scores(data)
    rows_data = [
        ("aggregate_oos_sharpe", _fmt(scores.get("aggregate_oos_sharpe")), "所有 OOS split 聚合表现"),
        ("worst_oos_sharpe", _fmt(scores.get("worst_oos_sharpe")), "最差样本外窗口"),
        ("pct_profitable_splits", _pct(scores.get("pct_profitable_splits")), "赚钱 split 占比"),
        ("deflated_sharpe_ratio", _fmt(scores.get("deflated_sharpe_ratio")), "调整多重试验后的 Sharpe 可靠性"),
        ("regime_breakdown", _cell(scores.get("regime_breakdown") or reason or "未保存分 regime 明细"), "分市场状态稳定性"),
        ("capacity_viability", _cell(scores.get("capacity_viability") or "未通过 Go / No-Go，容量阶段未开启"), "容量和冲击成本是否可接受"),
    ]
    body = "".join(
        f"<tr><td>{escape(metric)}</td><td>{escape(str(value))}</td><td>{escape(note)}</td></tr>"
        for metric, value, note in rows_data
    )
    return _table(["Metric", "数值", "解释"], body)


def _walkforward_split_contract_table(data: Dict[str, Any]) -> str:
    detail = data.get("walkforward_detail") or data.get("oos") or {}
    split_rows = []
    if isinstance(detail, dict):
        raw_splits = detail.get("splits") or detail.get("split_results") or []
        if isinstance(raw_splits, list):
            for idx, split in enumerate(raw_splits[:12], start=1):
                if not isinstance(split, dict):
                    continue
                split_rows.append(
                    (
                        str(split.get("split") or idx),
                        _range_text(split, "train_start", "train_end"),
                        _range_text(split, "test_start", "test_end"),
                        _cell(split.get("params") or split.get("parameters") or "frozen parameters"),
                        _fmt(split.get("oos_sharpe") or split.get("sharpe")),
                        _pct(split.get("max_drawdown") or split.get("maxdd")),
                        _pct(split.get("turnover")),
                        _split_verdict(split),
                    )
                )
    if not split_rows:
        scores, reason, verdict = _walkforward_scores(data)
        split_rows = [
            (
                "汇总",
                "rolling train windows",
                "rolling OOS windows",
                "frozen parameters",
                _fmt(scores.get("aggregate_oos_sharpe")),
                "n/a",
                "n/a",
                verdict or ("fail" if _walkforward_badge_class(scores) == "fail" else "pass"),
            )
        ]
        if reason:
            split_rows.append(("原因", "n/a", "n/a", reason, _fmt(scores.get("worst_oos_sharpe")), "n/a", "n/a", "fail"))
    body = "".join(
        "<tr>"
        + f"<td>{escape(split)}</td><td>{escape(train)}</td><td>{escape(test)}</td><td>{escape(params)}</td>"
        + f"<td>{escape(sharpe)}</td><td>{escape(maxdd)}</td><td>{escape(turnover)}</td><td>{escape(result)}</td></tr>"
        for split, train, test, params, sharpe, maxdd, turnover, result in split_rows
    )
    return _table(["Split", "Train", "Test", "参数", "OOS Sharpe", "MaxDD", "Turnover", "结论"], body)


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
    for path in (_REPORT_TEMPLATE_PATH, _FALLBACK_REPORT_TEMPLATE_PATH):
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            continue
        start = html.find("<style>")
        end = html.find("</style>", start)
        if start >= 0 and end > start:
            return html[start + len("<style>") : end].strip()
    return (
        ":root{color-scheme:light;--bg:#f6f3ec;--panel:#fffdfa;--ink:#18222b;--muted:#66727e;--line:#d8dee3;--soft:#f0ece3;--accent:#0f766e;--good:#166534;--warn:#b45309;--bad:#991b1b}"
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:'Microsoft YaHei','PingFang SC','Noto Sans SC','Source Han Sans SC','Segoe UI',system-ui,sans-serif;line-height:1.62;letter-spacing:0}main{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:40px 0 72px}.panel{margin:18px 0;padding:24px;background:var(--panel);border:1px solid var(--line)}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric,.decision-mark{padding:14px;border:1px solid var(--line);background:#fff}.table-wrap{overflow-x:auto;margin:12px 0 16px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--soft)}.badge{display:inline-block;padding:2px 8px;border:1px solid var(--line);border-radius:999px;font-size:12px;font-weight:800}.pass{color:var(--good);background:#ecfdf5;border-color:#86efac}.warn{color:var(--warn);background:#fff7ed;border-color:#fed7aa}.fail{color:var(--bad);background:#fef2f2;border-color:#fecaca}.formula{padding:16px;margin:10px 0 16px;background:#fbf7ef;border:1px solid var(--line);font-family:'Cascadia Mono',Consolas,monospace;white-space:pre-wrap}.decision{display:grid;grid-template-columns:180px 1fr;gap:16px}"
    )


def _strict_backtest_for_report(data: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    if int(data.get("backtested", 0) or 0) <= 0:
        return {}
    return (row.get("metrics") or {}).get("strict_backtest") or {}


def _not_run_table(headers: List[str], item: str, reason: str) -> str:
    body = "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in (item, "not_run", reason)[: len(headers)]) + "</tr>"
    while body.count("<td>") < len(headers):
        body = body.replace("</tr>", "<td>n/a</td></tr>")
    return _table(headers, [body])


def _stage1_score(data: Dict[str, Any], key: str, fallback: Any) -> Any:
    for entry in data.get("log") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("phase")) != "stage1_spec":
            continue
        scores = entry.get("scores") or {}
        if key in scores:
            return scores.get(key)
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


def _signal_oos_text(metrics: Dict[str, Any], wf_scores: Dict[str, Any]) -> str:
    if metrics.get("oos_validation"):
        return _join_text(metrics.get("oos_validation"))
    aggregate = _safe_float(wf_scores.get("aggregate_oos_sharpe"))
    if aggregate is None:
        return "需结合 purged walk-forward 结果"
    return "pass" if aggregate > 0 else "fail"


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
    sharpe = _safe_float(split.get("oos_sharpe") or split.get("sharpe"))
    return "pass" if sharpe is not None and sharpe > 0 else "fail"


def _walkforward_scores(data: Dict[str, Any]) -> tuple[Dict[str, Any], str, str]:
    fallback: tuple[Dict[str, Any], str, str] | None = None
    for entry in data.get("log") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("phase")) not in {"rigor", "walkforward", "purged_walk_forward"}:
            continue
        scores = entry.get("scores") or {}
        current = (scores, str(entry.get("reason") or ""), str(entry.get("verdict") or ""))
        if scores and any(key in scores for key in ("aggregate_oos_sharpe", "worst_oos_sharpe", "pct_profitable_splits")):
            fallback = current
        elif fallback is None:
            fallback = current
    if fallback is not None:
        return fallback
    detail = data.get("walkforward_detail") or data.get("oos") or {}
    if isinstance(detail, dict):
        return detail, "", ""
    return {}, "", ""


def _signal_badge_class(metrics: Dict[str, Any]) -> str:
    rank_ic = _safe_float(metrics.get("rank_ic"))
    fdr = _safe_float(metrics.get("fdr_adjusted_p"))
    hit = _safe_float(metrics.get("hit_rate"))
    if rank_ic is None:
        return "fail"
    if rank_ic < 0.02:
        return "fail"
    if (fdr is None or fdr <= 0.05) and (hit is None or hit >= 0.5):
        return "pass"
    if rank_ic > 0:
        return "warn"
    return "fail"


def _strict_badge_class(metrics: Dict[str, Any]) -> str:
    sharpe = _safe_float(metrics.get("sharpe"))
    total_return = _safe_float(metrics.get("total_return"))
    cagr = _safe_float(metrics.get("cagr"))
    if sharpe is None:
        return "fail"
    if sharpe >= 1.0 and (cagr is None or cagr > 0.03):
        return "pass"
    if sharpe > 0 and (total_return is None or total_return > 0):
        return "warn"
    return "fail"


def _walkforward_badge_class(scores: Dict[str, Any]) -> str:
    aggregate = _safe_float(scores.get("aggregate_oos_sharpe"))
    worst = _safe_float(scores.get("worst_oos_sharpe"))
    pct_profitable = _safe_float(scores.get("pct_profitable_splits"))
    if aggregate is None and worst is None:
        return "fail"
    if aggregate is not None and aggregate > 0 and (worst is None or worst > 0) and (pct_profitable is None or pct_profitable >= 0.5):
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


def _signal_validation_summary(metrics: Dict[str, Any]) -> str:
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
    wf_scores, wf_reason, _ = _walkforward_scores(data)
    reasons = [
        _signal_validation_summary(metrics),
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
            + "</tr>"
        )
    if not body:
        return "<p>本次没有记录严格 Backtester 结果；这会阻断 Go / No-Go。</p>"
    return _table(["Idea", "区间", "Sharpe", "Sortino", "CAGR", "累计", "MaxDD", "Calmar", "胜率", "PF", "成交数", "手续费", "涨跌停/T+1拒单"], body)


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
        items.append(f"<li>Report: <code>quant/infrastructure/var/research/reports/{escape(strategy_id)}/full_research_report.html</code></li>")
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
