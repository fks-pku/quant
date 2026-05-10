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
        '<p class="eyebrow">Quant Research Pipeline</p>',
        "<h1>Full Research Report</h1>",
        f"<p>Generated {escape(generated)} for run <code>{escape(run_id)}</code>.</p>",
        "</section>",
        '<section class="panel">',
        "<h2>Run Summary</h2>",
        _summary_table(data),
        "</section>",
        '<section class="panel standard">',
        "<h2>Completion Standard</h2>",
        "<p>A strategy research run is not complete until the report separates research diagnostics from deployable portfolio results.</p>",
        "<p>For implemented strategies, final recommendation requires a strict framework backtest with realistic execution, costs, trading constraints, statistical significance, and artifact links.</p>",
        "<p>For A-share research, deployable recommendations must be long-only unless a legal shorting or hedging implementation is explicitly tested.</p>",
        "</section>",
        '<section class="panel">',
        "<h2>Decision Ledger</h2>",
        _ledger_html(rows),
        "</section>",
        '<section class="panel">',
        "<h2>Pipeline Log</h2>",
        _log_table(data.get("log") or []),
        "</section>",
        '<section class="panel standard">',
        "<h2>Required Attachments For Final Strategy Research</h2>",
        _requirements_list(),
        "</section>",
    ]
    return _html_document("Full Research Report", "\n".join(body))


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
            "# Full Research Report",
            "",
            f"Detailed report: [{html_filename}]({html_filename})",
            "",
            f"- Generated: {generated}",
            f"- Run ID: `{run_id}`",
            f"- Discovered: {data.get('discovered', 0)}",
            f"- Evaluated: {data.get('evaluated', 0)}",
            f"- Integrated: {data.get('integrated', 0)}",
            f"- Rejected: {data.get('rejected', 0)}",
            "",
            "Complex research reports are rendered as HTML. Lightweight notes and AGENTS files may remain Markdown.",
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
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            "<style>",
            ":root{color-scheme:light;--ink:#172026;--muted:#66737f;--line:#d7dde2;--paper:#f7f4ed;--panel:#fffdfa;--accent:#0f766e;--warn:#b45309;--bad:#991b1b;--good:#166534;}",
            "*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:'Microsoft YaHei','PingFang SC','Noto Sans SC','Source Han Sans SC','Segoe UI',system-ui,sans-serif;line-height:1.62;font-variant-numeric:tabular-nums lining-nums;font-feature-settings:'tnum' 1,'lnum' 1;letter-spacing:0}main{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:40px 0 64px}.hero{border-bottom:2px solid var(--ink);padding:28px 0 32px;margin-bottom:28px}.eyebrow{font:700 12px/1.2 'Segoe UI',system-ui,sans-serif;text-transform:uppercase;letter-spacing:.05em;color:var(--accent);margin:0 0 16px}.hero h1{font-size:42px;line-height:1.12;margin:0 0 14px;font-weight:750;letter-spacing:0}.hero p{max-width:820px;color:var(--muted);font-size:17px}.panel{background:var(--panel);border:1px solid var(--line);padding:24px;margin:18px 0}.panel h2{font-size:25px;line-height:1.25;margin:0 0 18px;border-bottom:1px solid var(--line);padding-bottom:10px;font-weight:720}.standard p{max-width:920px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}th{font:700 12px/1.2 'Segoe UI',system-ui,sans-serif;text-transform:uppercase;letter-spacing:.04em;color:#34414c;background:#f0ece3}code{font-family:'Cascadia Mono','Consolas','SFMono-Regular',monospace;background:#eee7da;padding:2px 5px;border-radius:4px}.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}.card{border:1px solid var(--line);background:#fff;padding:18px}.card h3{margin:0 0 12px;font-size:19px;line-height:1.3}.meta{display:grid;grid-template-columns:145px 1fr;gap:7px 12px;font-size:14px}.label{color:var(--muted);font-family:'Segoe UI',system-ui,sans-serif;font-size:12px;text-transform:uppercase;letter-spacing:.04em}.badge{display:inline-block;border:1px solid var(--line);padding:2px 8px;border-radius:999px;font:700 12px/1.4 'Segoe UI',system-ui,sans-serif}.badge.candidate,.badge.validated{color:var(--good);border-color:#86efac;background:#ecfdf5}.badge.rejected,.badge.error{color:var(--bad);border-color:#fecaca;background:#fef2f2}.badge.needs_more_validation,.badge.needs_manual_spec{color:var(--warn);border-color:#fed7aa;background:#fff7ed}ul{margin:0;padding-left:22px}a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:3px}@media(max-width:720px){main{width:min(100% - 24px,1180px);padding-top:24px}.hero h1{font-size:32px}.panel{padding:18px}.meta{grid-template-columns:1fr}}",
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
        rows.append(f"<tr><td>{escape(key)}</td><td>{escape(str(data.get(key, 0)))}</td></tr>")
    return '<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


def _ledger_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<p>No hypothesis ledger records were written.</p>"
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
                    '<span class="label">Status</span>' + f'<span><span class="badge {escape(status)}">{escape(status)}</span></span>',
                    _meta("Stage", row.get("stage", "")),
                    _meta("Strategy ID", row.get("strategy_id", "") or "not_integrated", code=True),
                    '<span class="label">Source</span>' + f"<span>{source}</span>",
                    _meta("Decision", row.get("decision_reason", "")),
                    _meta("Thesis", row.get("thesis", "")),
                    _meta("Discovery Quality", _fmt(evidence.get("discovery_quality", {}).get("score"))),
                    _meta("Admission Score", _fmt(metrics.get("admission_score"))),
                    _meta("Signal Quality", _fmt(metrics.get("signal_quality_score"))),
                    _meta("Research Confidence", _fmt(metrics.get("research_confidence_score"))),
                    _meta("Rank IC", _fmt(metrics.get("rank_ic"))),
                    _meta("Rank IC IR", _fmt(metrics.get("rank_ic_ir"))),
                    _meta("FDR p-value", _fmt(metrics.get("fdr_adjusted_p"))),
                    _meta("Hit Rate", _fmt(metrics.get("hit_rate"))),
                    _meta("Risk Flags", ", ".join(risk_flags) if risk_flags else "None"),
                    _meta("Validation Tests", ", ".join(validation_tests) if validation_tests else "None"),
                    "</div>",
                    "</article>",
                ]
            )
        )
    return '<div class="ledger">' + "\n".join(cards) + "</div>"


def _log_table(log_rows: List[Dict[str, Any]]) -> str:
    if not log_rows:
        return "<p>No pipeline log entries were recorded.</p>"
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
    return '<div class="table-wrap"><table><thead><tr><th>Phase</th><th>Verdict</th><th>Title</th><th>Reason</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"


def _requirements_list() -> str:
    items = [
        "Source notes and hypothesis ledger.",
        "Data lineage, universe definition, adjustment policy, and sample coverage.",
        "Signal definition, direction, IC, IC decay, FDR, hit rate, and sensitivity checks.",
        "Portfolio diagnostics clearly marked as non-deployable when they use long-short spreads in A-share.",
        "Strict framework backtest report with Sharpe, Sortino, CAGR, max drawdown, win rate, profit factor, costs, rejects, fills, and trade artifacts.",
        "Benchmark context, regime breakdown, capacity, failure modes, and go/no-go decision.",
        "A-share reports must use 000300 CSI 300 index as the benchmark when present in daily_cn; fallback to 510300 only when 000300 is missing, and state benchmark coverage.",
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


def _cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text[:240] + "..." if len(text) > 240 else text
