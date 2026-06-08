"""Run parallel walk-forward audit for the A-share broad asset ETF rotation strategy."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.features.research.models import PurgedWalkForwardResult
from quant.features.research.rigor.backtest_hub import RigorHub
from quant.scripts import run_ashare_broad_asset_etf_rotation_strict_backtest as strict_runner


WALKFORWARD_CONFIG = {
    "purged_walkforward": {
        "train_window_days": 252,
        "test_window_days": 63,
        "step_days": 63,
        "purge_days": 5,
        "embargo_days": 21,
        "min_train_observations": 126,
        "parallel_workers": 4,
        "prefetch_data": False,
    },
    "thresholds": {
        "min_worst_oos_sharpe": 0.3,
        "min_profitable_splits_pct": 0.5,
    },
    "cost_model": {
        "max_adv_pct": 0.05,
    },
}


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args(argv)

    payload, full_report_path = run_walkforward(max_workers=max(1, args.workers))
    walkforward = payload["walkforward"]
    print(
        json.dumps(
            {
                "strategy_id": strict_runner.STRATEGY_ID,
                "verdict": walkforward["verdict"],
                "total_splits": walkforward["total_splits"],
                "evaluated_splits": walkforward["evaluated_splits"],
                "no_trade_splits": walkforward["no_trade_splits"],
                "aggregate_oos_sharpe": walkforward["aggregate_oos_sharpe"],
                "worst_oos_sharpe": walkforward["worst_oos_sharpe"],
                "full_report_path": str(full_report_path),
                "walkforward_result_path": str(payload["walkforward_result_path"]),
                "walkforward_report_path": str(payload["walkforward_report_path"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def run_walkforward(max_workers: int = 4) -> Tuple[Dict[str, Any], Path]:
    universe = strict_runner.build_broad_asset_etf_pit_universe(
        universe_as_of=strict_runner.UNIVERSE_AS_OF,
        min_history_days_as_of=strict_runner.UNIVERSE_MIN_HISTORY_DAYS_AS_OF,
        max_symbols_per_category=strict_runner.UNIVERSE_MAX_SYMBOLS_PER_CATEGORY,
        universe_start=strict_runner.START,
        universe_end=strict_runner.END,
    )
    strict_runner._validate_pit_universe(universe)
    base_scenario = strict_runner._with_pit_universe(strict_runner.SCENARIOS[0], universe)
    symbols = list(base_scenario["symbols"])
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = strict_runner._load_shared_inputs(symbols)

    def split_runner(_: str, request: Dict[str, Any]) -> Dict[str, Any]:
        return _run_split_replay(
            base_scenario,
            request,
            lot_sizes,
            benchmark_provider,
            benchmark_meta,
            survivorship_audit,
        )

    config = _walkforward_config(max_workers=max_workers)
    hub = RigorHub(backtest_runner=split_runner, config=config)
    result = hub.run_walkforward(
        strict_runner.STRATEGY_ID,
        symbols,
        strict_runner.START.date().isoformat(),
        strict_runner.END.date().isoformat(),
        initial_cash=strict_runner.INITIAL_CASH,
    )
    payload = _build_walkforward_payload(result, max_workers=max_workers)
    full_report_path = _write_walkforward_outputs(payload)
    return payload, full_report_path


def _run_split_replay(
    base_scenario: Dict[str, Any],
    request: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: Any,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
) -> Dict[str, Any]:
    test_start = datetime.strptime(str(request["start"]), "%Y-%m-%d")
    end = datetime.strptime(str(request["end"]), "%Y-%m-%d")
    run_start = datetime.strptime(str(request.get("train_start_date") or request["start"]), "%Y-%m-%d")
    scenario = dict(base_scenario)
    scenario["category_symbols"] = {
        category: list(symbols)
        for category, symbols in (base_scenario.get("category_symbols") or {}).items()
    }
    scenario["symbols"] = list(base_scenario.get("symbols") or [])
    scenario["pit_size_fields"] = list(base_scenario.get("pit_size_fields") or [])
    scenario["name"] = f"{base_scenario['name']}__wf_{request['start']}_{request['end']}"
    strict_report = strict_runner._run_one(
        scenario,
        lot_sizes,
        benchmark_provider,
        benchmark_meta,
        survivorship_audit,
        start=run_start,
        end=end,
    )
    all_returns = _returns_from_curve((strict_report.get("equity_curve") or {}).get("strategy") or [])
    oos_returns = _slice_returns(all_returns, test_start, end)
    metrics = _oos_metrics_from_returns(oos_returns)
    max_adv = _safe_float((strict_report.get("capacity") or {}).get("max_adv_participation"))
    total_trades = int(metrics.get("total_trades") or 0)
    trades = _capacity_proxy_trades(total_trades, max_adv)
    return {
        "metrics": metrics,
        "returns": oos_returns,
        "trades": trades,
        "strict_capacity": strict_report.get("capacity") or {},
        "strict_period": strict_report.get("period"),
    }


def _build_walkforward_payload(result: PurgedWalkForwardResult, max_workers: int = 4) -> Dict[str, Any]:
    thresholds = _walkforward_thresholds()
    splits = [_split_payload(idx, split) for idx, split in enumerate(result.splits, start=1)]
    total_splits = int(result.total_splits or len(splits))
    evaluated_splits = int(result.evaluated_splits or sum(1 for split in splits if split.get("has_trades") is not False))
    no_trade_splits = int(result.no_trade_splits or sum(1 for split in splits if split.get("has_trades") is False))
    verdict = _walkforward_verdict(result)
    walkforward = {
        "verdict": verdict,
        "is_viable": bool(result.is_viable),
        "reason": (
            "Frozen-parameter purged walk-forward strict replay. "
            "Zero-trade OOS windows are retained as split rows and excluded from aggregate OOS statistics."
        ),
        "method": (
            "Parallel purged walk-forward over the locked strict scenario; every evaluated split reuses the same "
            "Backtester execution constraints, CN ETF cost model, PIT ETF universe, and initial cash as the full strict run."
        ),
        "thresholds": thresholds,
        "aggregate_oos_sharpe": float(result.aggregate_oos_sharpe),
        "worst_oos_sharpe": float(result.worst_oos_sharpe),
        "deflated_sharpe_ratio": result.deflated_sharpe_ratio,
        "sharpe_degradation": float(result.sharpe_degradation),
        "pct_profitable_splits": float(result.pct_profitable_splits),
        "capacity_ok": bool(result.capacity_ok),
        "regime_breakdown": dict(result.regime_breakdown or {}),
        "bull_only_warning": bool(result.bull_only_warning),
        "total_splits": total_splits,
        "evaluated_splits": evaluated_splits,
        "no_trade_splits": no_trade_splits,
        "workers": max(1, int(max_workers)),
        "splits": splits,
    }
    return {
        "strategy_id": strict_runner.STRATEGY_ID,
        "run_ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "start": strict_runner.START.date().isoformat(),
        "end": strict_runner.END.date().isoformat(),
        "walkforward": walkforward,
    }


def _write_walkforward_outputs(payload: Dict[str, Any]) -> Path:
    strategy_dir = strict_runner.REPORT_ROOT / strict_runner.STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = strict_runner.REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = str(payload.get("run_ts") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    result_path = strategy_dir / "walkforward_result.json"
    report_path = strategy_dir / "walkforward_result_report.html"
    payload["walkforward_result_path"] = result_path
    payload["walkforward_report_path"] = report_path
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    result_path.write_text(text, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_result.json").write_text(text, encoding="utf-8")
    html = _walkforward_report_html(payload)
    report_path.write_text(html, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_result_report.html").write_text(html, encoding="utf-8")
    (latest_dir / "walkforward_result.json").write_text(text, encoding="utf-8")
    (latest_dir / "walkforward_result_report.html").write_text(html, encoding="utf-8")
    return _refresh_full_report_from_strict_grid(strategy_dir)


def _refresh_full_report_from_strict_grid(strategy_dir: Path) -> Path:
    grid_path = strategy_dir / "grid_result.json"
    if not grid_path.exists():
        return strategy_dir / "full_research_report.html"
    payload = json.loads(grid_path.read_text(encoding="utf-8"))
    full_report_path, _ = strict_runner._write_outputs(
        list(payload.get("rows") or []),
        dict(payload.get("strict_reports") or {}),
        dict(payload.get("best") or {}),
        dict(payload.get("pit_universe") or {}),
    )
    return full_report_path


def _walkforward_config(max_workers: int) -> Dict[str, Any]:
    config = {
        "purged_walkforward": dict(WALKFORWARD_CONFIG["purged_walkforward"]),
        "thresholds": dict(WALKFORWARD_CONFIG["thresholds"]),
        "cost_model": dict(WALKFORWARD_CONFIG["cost_model"]),
    }
    config["purged_walkforward"]["parallel_workers"] = max(1, int(max_workers))
    return config


def _walkforward_thresholds() -> Dict[str, Any]:
    config = _walkforward_config(max_workers=1)
    purged = config["purged_walkforward"]
    thresholds = config["thresholds"]
    return {
        "train_window_days": purged["train_window_days"],
        "test_window_days": purged["test_window_days"],
        "step_days": purged["step_days"],
        "purge_days": purged["purge_days"],
        "embargo_days": purged["embargo_days"],
        "min_train_observations": purged["min_train_observations"],
        "min_worst_oos_sharpe": thresholds["min_worst_oos_sharpe"],
        "min_profitable_splits_pct": thresholds["min_profitable_splits_pct"],
        "min_deflated_sharpe_ratio": 0.95,
        "max_adv_pct": config["cost_model"]["max_adv_pct"],
    }


def _walkforward_verdict(result: PurgedWalkForwardResult) -> str:
    if result.is_viable:
        return "pass"
    if result.evaluated_splits > 0 and result.aggregate_oos_sharpe > 0:
        return "warn"
    return "fail"


def _split_payload(idx: int, split: Dict[str, Any]) -> Dict[str, Any]:
    response = split.get("response") if isinstance(split.get("response"), dict) else {}
    metrics = response.get("metrics") if isinstance(response.get("metrics"), dict) else {}
    has_trades = bool(split.get("has_trades", True))
    trade_count = _safe_int(split.get("trade_count"))
    oos_sharpe = _safe_float(split.get("test_sharpe"))
    total_return_value = metrics.get("total_return")
    if total_return_value is None:
        total_return_value = metrics.get("return")
    if total_return_value is None:
        total_return_value = metrics.get("oos_return")
    total_return = _safe_float(total_return_value)
    if has_trades is False or trade_count == 0:
        verdict = "excluded_no_trade"
    elif oos_sharpe is not None and oos_sharpe > 0:
        verdict = "pass"
    else:
        verdict = "fail"
    return {
        "split": idx,
        "train_start": split.get("train_start_date") or split.get("train_start"),
        "train_end": split.get("train_end_date") or split.get("train_end"),
        "test_start": split.get("test_start_date") or split.get("test_start"),
        "test_end": split.get("test_end_date") or split.get("test_end"),
        "parameters": "frozen parameters",
        "oos_sharpe": oos_sharpe,
        "test_sharpe": oos_sharpe,
        "return": total_return,
        "total_return": total_return,
        "cagr": _safe_float(metrics.get("cagr")),
        "max_drawdown": _safe_float(metrics.get("max_drawdown_pct") if metrics.get("max_drawdown_pct") is not None else metrics.get("max_dd")),
        "trade_count": trade_count,
        "has_trades": has_trades,
        "capacity": response.get("strict_capacity") or {},
        "verdict": verdict,
    }


def _returns_from_curve(points: List[Dict[str, Any]]) -> pd.Series:
    rows = []
    for point in points:
        if not isinstance(point, dict):
            continue
        date_text = str(point.get("date") or "")[:10]
        value = _safe_float(point.get("value"))
        if not date_text or value is None:
            continue
        rows.append((pd.Timestamp(date_text), value))
    if len(rows) < 2:
        return pd.Series(dtype=float)
    series = pd.Series({date: value for date, value in rows}).sort_index()
    return series.pct_change(fill_method=None).dropna().astype(float)


def _slice_returns(returns: pd.Series, start: datetime, end: datetime) -> pd.Series:
    if returns.empty:
        return returns
    indexed = returns.copy()
    if not isinstance(indexed.index, pd.DatetimeIndex):
        indexed.index = pd.to_datetime(indexed.index, errors="coerce")
    indexed = indexed.dropna().sort_index()
    return indexed.loc[(indexed.index >= pd.Timestamp(start)) & (indexed.index <= pd.Timestamp(end))].astype(float)


def _oos_metrics_from_returns(returns: pd.Series) -> Dict[str, Any]:
    active_returns = returns.dropna().astype(float)
    if active_returns.empty:
        return {"sharpe": 0.0, "cagr": 0.0, "total_return": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0}
    mean = float(active_returns.mean())
    std = float(active_returns.std(ddof=0))
    sharpe = mean / std * (252.0 ** 0.5) if std > 1e-12 else 0.0
    equity = (1.0 + active_returns).cumprod()
    periods = max(1, len(active_returns))
    final_value = float(equity.iloc[-1])
    cagr = final_value ** (252.0 / periods) - 1.0 if final_value > 0 else -1.0
    drawdown = equity / equity.cummax() - 1.0
    activity_count = int((active_returns.abs() > 1e-12).sum())
    return {
        "sharpe": sharpe,
        "cagr": cagr,
        "total_return": final_value - 1.0,
        "max_drawdown_pct": float(drawdown.min()) if not drawdown.empty else 0.0,
        "total_trades": activity_count,
    }


def _capacity_proxy_trades(total_trades: int, max_adv: float | None) -> List[Dict[str, Any]]:
    if total_trades <= 0:
        return []
    if max_adv is None:
        return [{"trade_value": 0.0, "avg_daily_volume": 1.0, "price": 1.0, "volatility": 0.2}]
    return [{"trade_value": max(0.0, max_adv), "avg_daily_volume": 1.0, "price": 1.0, "volatility": 0.2}]


def _walkforward_report_html(payload: Dict[str, Any]) -> str:
    walkforward = payload["walkforward"]
    summary_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{escape(str(value))}</td></tr>"
        for label, value in [
            ("verdict", walkforward.get("verdict")),
            ("total_splits", walkforward.get("total_splits")),
            ("evaluated_splits", walkforward.get("evaluated_splits")),
            ("no_trade_splits", walkforward.get("no_trade_splits")),
            ("aggregate_oos_sharpe", _fmt_cell(walkforward.get("aggregate_oos_sharpe"))),
            ("worst_oos_sharpe", _fmt_cell(walkforward.get("worst_oos_sharpe"))),
            ("pct_profitable_splits", _pct_cell(walkforward.get("pct_profitable_splits"))),
            ("capacity_ok", walkforward.get("capacity_ok")),
            ("method", walkforward.get("method")),
        ]
    )
    split_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('split')))}</td>"
        f"<td>{escape(str(row.get('train_start')))} - {escape(str(row.get('train_end')))}</td>"
        f"<td>{escape(str(row.get('test_start')))} - {escape(str(row.get('test_end')))}</td>"
        f"<td>{_fmt_cell(row.get('oos_sharpe'))}</td>"
        f"<td>{_pct_cell(row.get('return'))}</td>"
        f"<td>{escape(str(row.get('trade_count')))}</td>"
        f"<td>{escape(str(row.get('verdict')))}</td>"
        "</tr>"
        for row in walkforward.get("splits", [])
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(strict_runner.STRATEGY_ID)} walk-forward report</title>
<style>
body{{margin:0;background:#f6f3ec;color:#18222b;font-family:"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif;line-height:1.6}}
main{{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:36px 0 64px}}
section{{margin:18px 0;padding:22px;background:#fffdfa;border:1px solid #d8dee3}}
h1{{margin:0 0 12px;font-size:34px}}h2{{margin:0 0 12px;font-size:22px}}p{{color:#66727e}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:9px 11px;border-bottom:1px solid #d8dee3;text-align:left;vertical-align:top}}th{{background:#f0ece3}}
</style>
</head>
<body>
<main>
<section>
<h1>{escape(strict_runner.STRATEGY_ID)} Walk-forward Report</h1>
<p>Parallel frozen-parameter purged OOS strict replay. This report is audit evidence and does not re-select production parameters.</p>
</section>
<section>
<h2>Summary</h2>
<div class="table-wrap"><table><tbody>{summary_rows}</tbody></table></div>
</section>
<section>
<h2>Splits</h2>
<div class="table-wrap"><table><thead><tr><th>Split</th><th>Train</th><th>Test</th><th>OOS Sharpe</th><th>Return</th><th>Trades</th><th>Verdict</th></tr></thead><tbody>{split_rows}</tbody></table></div>
</section>
</main>
</body>
</html>
"""


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


def _pct_cell(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.2%}"


def _fmt_cell(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.4f}"


if __name__ == "__main__":
    main()
