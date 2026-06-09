from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List


INITIAL_SCREENING_COLUMNS = [
    "idea",
    "source",
    "策略解释",
    "策略实现代码文件",
    "rank_ic",
    "sharpe",
    "结论",
]


def normalize_initial_screening_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for row in rows:
        item = {column: _cell(row.get(column, "")) for column in INITIAL_SCREENING_COLUMNS}
        for key, value in dict(row).items():
            if key not in item:
                item[key] = value
        normalized.append(item)
    return normalized


def initial_screening_table_markdown(rows: Iterable[Dict[str, Any]]) -> str:
    normalized = normalize_initial_screening_rows(rows)
    lines = ["# Initial Screening Table", ""]
    lines.append("| " + " | ".join(INITIAL_SCREENING_COLUMNS) + " |")
    lines.append("| " + " | ".join("---" for _ in INITIAL_SCREENING_COLUMNS) + " |")
    for row in normalized:
        lines.append("| " + " | ".join(_markdown_cell(row, column) for column in INITIAL_SCREENING_COLUMNS) + " |")
    lines.append("")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _markdown_cell(row: Dict[str, Any], column: str) -> str:
    text = _markdown_text(row.get(column, ""))
    if column == "idea":
        return _markdown_link(text, row.get("source_url", ""))
    if column == "策略实现代码文件":
        return _markdown_link(text, row.get("strategy_code_url", ""))
    return text


def _markdown_text(value: Any) -> str:
    text = _cell(value).replace("\n", " ").replace("\r", " ")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def _markdown_link(label: str, href: Any) -> str:
    target = _cell(href).strip()
    if not label or not target:
        return label
    target = target.replace("\n", "").replace("\r", "").replace(">", "%3E")
    return f"[{label}](<{target}>)"
