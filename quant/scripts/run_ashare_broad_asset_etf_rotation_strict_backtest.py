"""Run a full research report for the A-share broad asset ETF rotation strategy."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
    _strict_execution_cost_model,
)
from quant.domain.models.market import is_cn_symbol
from quant.features.backtest.benchmark import BenchmarkProvider
from quant.features.backtest.engine import Backtester
from quant.features.strategies.ashare_broad_asset_etf_rotation.strategy import (
    AShareBroadAssetEtfRotationStrategy,
    DEFAULT_CATEGORY_SYMBOLS,
    DEFAULT_PIT_SIZE_FIELDS,
)
from quant.features.trading.portfolio import Portfolio
from quant.features.trading.risk import RiskEngine
from quant.features.trading.sub_portfolio import SubPortfolio
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
from quant.infrastructure.research.cn_etf_universe import (
    build_broad_asset_etf_pit_universe,
    flatten_category_symbols,
)
from quant.infrastructure.research.reporting import build_research_full_report_html, build_research_stage_report_html


START = datetime(2016, 1, 1)
END = datetime(2026, 5, 31)
UNIVERSE_AS_OF = None
UNIVERSE_MIN_HISTORY_DAYS_AS_OF = 0
UNIVERSE_MAX_SYMBOLS_PER_CATEGORY = 0
INITIAL_CASH = 10_000.0
STRATEGY_ID = "ashare_broad_asset_etf_rotation"
TITLE = "A-share Broad Asset ETF Rotation"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic"},
}


SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "monthly_126d_vol60_continuous_tilt70_domestic",
        "category_symbols": {key: list(value) for key, value in DEFAULT_CATEGORY_SYMBOLS.items()},
        "momentum_lookback": 126,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 60,
        "volatility_floor": 0.01,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "weight_mode": "continuous_branch_tilt",
        "tilt_strength": 0.70,
        "temperature": 0.75,
        "min_branch_weight": 0.02,
        "max_branch_weight": 0.30,
        "rebalance_threshold": 0.02,
        "trend_penalty": 1.0,
        "target_exposure": 1.0,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
        "enable_risk_exit": True,
        "risk_exit": {"enabled": True, "exit_type": "continuous_weight_rebalance_and_actual_cash_fallback"},
    },
    {
        "name": "monthly_126d_vol60_continuous_tilt50_domestic",
        "category_symbols": {key: list(value) for key, value in DEFAULT_CATEGORY_SYMBOLS.items()},
        "momentum_lookback": 126,
        "momentum_skip": 1,
        "trend_window": 120,
        "volatility_window": 60,
        "volatility_floor": 0.01,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "weight_mode": "continuous_branch_tilt",
        "tilt_strength": 0.50,
        "temperature": 0.75,
        "min_branch_weight": 0.02,
        "max_branch_weight": 0.30,
        "rebalance_threshold": 0.02,
        "trend_penalty": 1.0,
        "target_exposure": 1.0,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
        "enable_risk_exit": True,
        "risk_exit": {"enabled": True, "exit_type": "continuous_weight_rebalance_and_actual_cash_fallback"},
    },
    {
        "name": "monthly_126d_trend160_vol60_continuous_tilt70_domestic",
        "category_symbols": {key: list(value) for key, value in DEFAULT_CATEGORY_SYMBOLS.items()},
        "momentum_lookback": 126,
        "momentum_skip": 1,
        "trend_window": 160,
        "volatility_window": 60,
        "volatility_floor": 0.01,
        "liquidity_window": 20,
        "min_avg_turnover": 20_000_000.0,
        "weight_mode": "continuous_branch_tilt",
        "tilt_strength": 0.70,
        "temperature": 0.75,
        "min_branch_weight": 0.02,
        "max_branch_weight": 0.30,
        "rebalance_threshold": 0.02,
        "trend_penalty": 1.0,
        "target_exposure": 1.0,
        "holding_days": 20,
        "require_pit_size": True,
        "pit_size_fields": list(DEFAULT_PIT_SIZE_FIELDS),
        "enable_risk_exit": True,
        "risk_exit": {"enabled": True, "exit_type": "continuous_weight_rebalance_and_actual_cash_fallback"},
    },
]


def main(argv: List[str] | None = None) -> None:
    args = _parse_args(argv)
    universe = build_broad_asset_etf_pit_universe(
        universe_as_of=UNIVERSE_AS_OF,
        min_history_days_as_of=UNIVERSE_MIN_HISTORY_DAYS_AS_OF,
        max_symbols_per_category=UNIVERSE_MAX_SYMBOLS_PER_CATEGORY,
        universe_start=START,
        universe_end=END,
    )
    _validate_pit_universe(universe)
    scenarios = [_with_pit_universe(scenario, universe) for scenario in SCENARIOS]
    all_symbols = sorted({symbol for scenario in scenarios for symbol in scenario["symbols"]})
    lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_shared_inputs(all_symbols)
    rows: List[Dict[str, Any]] = []
    strict_reports: Dict[str, Dict[str, Any]] = {}
    for scenario in scenarios:
        print(f"Running {scenario['name']} on {len(scenario['symbols'])} ETFs", flush=True)
        strict_report = _run_one(scenario, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit)
        strict_reports[scenario["name"]] = strict_report
        metrics = strict_report.get("metrics") or {}
        capacity = strict_report.get("capacity") or {}
        diagnostics = strict_report.get("diagnostics") or {}
        execution_cost_bps = strict_report.get("execution_cost_bps") or {}
        row = {
            "scenario": scenario["name"],
            "symbols": scenario["symbols"],
            "parameters": _scenario_parameters(scenario),
            "category_symbols": scenario["category_symbols"],
            "missing_pit_categories": scenario.get("missing_pit_categories", []),
            "registered_universe_counts": scenario.get("registered_universe_counts", {}),
            "universe_registry_version": scenario.get("universe_registry_version", "audited_stable_etf_registry_v1"),
            "universe_selection_policy": scenario.get("universe_selection_policy", "audited_stable_etf_registry"),
            "sharpe": metrics.get("sharpe"),
            "cagr": metrics.get("cagr"),
            "total_return": metrics.get("total_return"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "calmar_ratio": metrics.get("calmar_ratio"),
            "total_trades": metrics.get("total_trades"),
            "cost_drag_pct": diagnostics.get("cost_drag_pct"),
            "max_adv_participation": capacity.get("max_adv_participation"),
            "weighted_effective_bps": execution_cost_bps.get("weighted_effective_bps"),
            "median_effective_bps": execution_cost_bps.get("median_effective_bps"),
            "meets_goal": _meets_goal(strict_report),
        }
        rows.append(row)
        print(json.dumps(_compact_row(row), ensure_ascii=False), flush=True)

    best = _select_best(rows)
    report_path, result_path = _write_outputs(rows, strict_reports, best, universe)
    followups = {}
    if args.run_followups:
        followups = _run_default_followup_audits(
            walkforward_workers=args.walkforward_workers,
            stability_workers=args.stability_workers,
        )
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "best": _compact_row(best),
                "report_path": str(report_path),
                "result_path": str(result_path),
                "followups": _compact_followup_summary(followups),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-followups",
        dest="run_followups",
        action="store_false",
        help="Only generate strict backtest and report skeleton; do not run default walk-forward/stability audits.",
    )
    parser.add_argument("--walkforward-workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--stability-workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.set_defaults(run_followups=True)
    return parser.parse_args(argv)


def _run_default_followup_audits(walkforward_workers: int = 4, stability_workers: int = 4) -> Dict[str, Dict[str, Any]]:
    from quant.scripts import run_ashare_broad_asset_etf_rotation_stability as stability_runner
    from quant.scripts import run_ashare_broad_asset_etf_rotation_walkforward as walkforward_runner

    print(f"Running default walk-forward audit with {max(1, walkforward_workers)} workers", flush=True)
    walkforward_payload, walkforward_report_path = walkforward_runner.run_walkforward(
        max_workers=max(1, walkforward_workers)
    )
    print(f"Running default stability audit with {max(1, stability_workers)} workers", flush=True)
    stability_payload, stability_report_path = stability_runner.run_stability(max_workers=max(1, stability_workers))
    return {
        "walkforward": {"payload": walkforward_payload, "report_path": walkforward_report_path},
        "stability": {"payload": stability_payload, "report_path": stability_report_path},
    }


def _compact_followup_summary(followups: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for key, item in followups.items():
        payload = item.get("payload") or {}
        if key == "walkforward":
            walkforward = payload.get("walkforward") or {}
            summary[key] = {
                "verdict": walkforward.get("verdict"),
                "total_splits": walkforward.get("total_splits"),
                "evaluated_splits": walkforward.get("evaluated_splits"),
                "report_path": str(item.get("report_path")),
            }
        elif key == "stability":
            sensitivity = payload.get("parameter_sensitivity") or {}
            summary[key] = {
                "status": sensitivity.get("status"),
                "tested_count": sensitivity.get("tested_count"),
                "report_path": str(item.get("report_path")),
            }
    return summary


def _with_pit_universe(scenario: Dict[str, Any], universe: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(scenario)
    registered = {key: list(value) for key, value in (scenario.get("category_symbols") or {}).items()}
    active = {key: list(value) for key, value in (universe.get("category_symbols") or {}).items()}
    missing = []
    for category in registered:
        active_symbols = active.get(category) or []
        if active_symbols:
            registered[category] = active_symbols
        else:
            missing.append(category)
    result["category_symbols"] = registered
    result["symbols"] = flatten_category_symbols(result["category_symbols"])
    result["missing_pit_categories"] = missing
    result["registered_universe_counts"] = dict(universe.get("registered_universe_counts") or {})
    result["universe_registry_version"] = universe.get("universe_registry_version") or "audited_stable_etf_registry_v1"
    result["universe_selection_policy"] = universe.get("universe_selection_policy") or "audited_stable_etf_registry"
    return result


def _validate_pit_universe(universe: Dict[str, Any]) -> None:
    category_symbols = universe.get("category_symbols") or {}
    if not any(category_symbols.values()):
        raise RuntimeError("Audited ETF registry universe has no active categories with local PIT data")
    symbols = set(flatten_category_symbols(category_symbols))
    blocked = {"513100", "513050", "159920", "510900", "513330", "513180", "513130"}
    overlap = sorted(symbols & blocked)
    if overlap:
        raise RuntimeError(f"Cross-border ETFs are not allowed in this default pool: {', '.join(overlap)}")


def _load_shared_inputs(symbols: List[str]) -> Tuple[Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        lot_sizes = _load_lot_sizes(db_provider, symbols, is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, START, END, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, START, END, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _run_one(
    scenario: Dict[str, Any],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime = START,
    end: datetime = END,
) -> Dict[str, Any]:
    symbols = list(scenario["symbols"])
    execution_cost_model = _strict_execution_cost_model(
        STRATEGY_ID,
        {
            "name": TITLE,
            "description": "CN-listed domestic broad asset ETF risk-adjusted momentum rotation",
            "parameters": dict(scenario),
        },
        True,
    )
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=False,
        include_execution_liquidity_features=True,
    )
    strategy = AShareBroadAssetEtfRotationStrategy(
        category_symbols={key: list(value) for key, value in scenario["category_symbols"].items()},
        momentum_lookback=int(scenario["momentum_lookback"]),
        momentum_skip=int(scenario["momentum_skip"]),
        trend_window=int(scenario["trend_window"]),
        volatility_window=int(scenario["volatility_window"]),
        volatility_floor=float(scenario["volatility_floor"]),
        liquidity_window=int(scenario["liquidity_window"]),
        min_avg_turnover=float(scenario["min_avg_turnover"]),
        weight_mode=str(scenario.get("weight_mode") or "continuous_branch_tilt"),
        tilt_strength=float(scenario["tilt_strength"]),
        temperature=float(scenario["temperature"]),
        min_branch_weight=float(scenario["min_branch_weight"]),
        max_branch_weight=float(scenario["max_branch_weight"]),
        rebalance_threshold=float(scenario["rebalance_threshold"]),
        trend_penalty=float(scenario["trend_penalty"]),
        target_exposure=float(scenario["target_exposure"]),
        holding_days=int(scenario["holding_days"]),
        require_pit_size=bool(scenario["require_pit_size"]),
        pit_size_fields=list(scenario["pit_size_fields"]),
    )
    backtest_config = {"slippage_bps": 5, "execution_cost_model": execution_cost_model}
    bt_config = {
        "backtest": backtest_config,
        "execution": {"commission": COMMISSION_CFG},
        "data": {"default_timeframe": "1d"},
        "risk": {"max_position_pct": 1.0, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 1.0},
    }
    backtester = Backtester(
        bt_config,
        portfolio_class=Portfolio,
        risk_engine_class=RiskEngine,
        sub_portfolio_class=SubPortfolio,
        lot_sizes=lot_sizes,
        benchmark_provider=benchmark_provider,
    )
    try:
        bt_result = backtester.run(
            start=start,
            end=end,
            strategies=[strategy],
            initial_cash=INITIAL_CASH,
            data_provider=data_provider,
            symbols=symbols,
        )
    finally:
        data_provider.close()
    benchmark_equity_curve = benchmark_provider.get_benchmark_equity(start, end, INITIAL_CASH) if benchmark_provider else None
    return _strict_backtest_report(
        bt_result,
        start,
        end,
        INITIAL_CASH,
        symbols,
        benchmark_meta,
        lot_sizes,
        strategy,
        benchmark_equity_curve,
        survivorship_audit,
        {**backtest_config, "commission": COMMISSION_CFG},
    )


def _meets_goal(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    capacity = strict_report.get("capacity") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_drawdown = float(metrics.get("max_drawdown_pct") or 0.0)
    total_trades = int(metrics.get("total_trades") or 0)
    max_adv = float(capacity.get("max_adv_participation") or 0.0)
    drawdown_floor = _drawdown_floor_for_cagr(cagr)
    return drawdown_floor is not None and max_drawdown >= drawdown_floor and total_trades > 50 and max_adv <= 0.05


def _drawdown_floor_for_cagr(cagr: float) -> float | None:
    if cagr >= 0.20:
        return -0.50
    if cagr >= 0.15:
        return -0.30
    if cagr >= 0.10:
        return -0.25
    if cagr >= 0.05:
        return -0.15
    return None


def _select_best(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(rows, key=_score_row)


def _score_row(row: Dict[str, Any]) -> Tuple[int, float, float, float, float]:
    cagr = float(row.get("cagr") or 0.0)
    max_dd = float(row.get("max_drawdown_pct") or 0.0)
    max_adv = float(row.get("max_adv_participation") or 0.0)
    return (
        1 if row.get("meets_goal") is True else 0,
        cagr / max(abs(max_dd), 1e-9),
        float(row.get("sharpe") or 0.0),
        cagr,
        -max_adv,
    )


def _scenario_parameters(scenario: Dict[str, Any]) -> Dict[str, Any]:
    excluded = {
        "name",
        "symbols",
        "registered_universe_counts",
        "universe_registry_version",
        "universe_selection_policy",
        "pit_size_fields",
    }
    return {key: value for key, value in scenario.items() if key not in excluded}


def _parameter_explanations() -> Dict[str, str]:
    return {
        "category_symbols": "人工审计通过的境内 ETF 类别映射；默认包含中证1000，排除纳指、恒生、H 股和中国互联网等跨境 ETF。",
        "momentum_lookback": "计算中期动量的回看窗口；用于衡量 ETF 在跳过最近交易日后的涨跌幅。",
        "momentum_skip": "动量计算时跳过最近几个交易日，用来减少短期反转噪声。",
        "trend_window": "趋势均线窗口；低于该均线的分支会被降分，但不会仅因趋势弱而硬清零。",
        "volatility_window": "计算已实现波动率的窗口；排序时用它惩罚高波动候选。",
        "volatility_floor": "动量除以波动率时的最低分母，避免低波动资产因分母过小被异常放大。",
        "liquidity_window": "平均成交额观察窗口；用于判断 ETF 是否有足够交易流动性。",
        "min_avg_turnover": "最低平均成交额门槛；低于该门槛的 ETF 不进入候选池。",
        "weight_mode": "连续权重模式；每个可交易资产分支都会得到目标权重，不再只硬选前 N 名。",
        "tilt_strength": "信号倾斜强度；越接近 1，强信号分支相对基准等权的加权越明显。",
        "temperature": "softmax 温度；数值越低，权重越集中到高分分支。",
        "min_branch_weight": "单个可交易分支的最低目标权重，避免弱信号分支直接被硬清零。",
        "max_branch_weight": "单个可交易分支的最高目标权重，限制强信号分支过度集中。",
        "rebalance_threshold": "目标权重与当前权重差异低于该阈值时不调仓，降低连续权重带来的换手。",
        "trend_penalty": "低于趋势均线时对分数施加的惩罚；趋势弱化会降权而非直接清仓。",
        "target_exposure": "组合目标总敞口；默认 100% 分配到可交易分支，手数取整后的剩余资金保留为真实现金。",
        "holding_days": "调仓间隔交易日数；到期后重新计算候选、分数和目标持仓。",
        "require_pit_size": "是否要求信号日能看到点时 NAV 或基金规模证据；用于降低 ETF 幸存者和规模偏差。",
        "enable_risk_exit": "报告契约标记，表示本策略的风险退出路径默认启用。",
        "risk_exit": "风险退出来自连续目标权重下降、不可交易分支归零和真实现金回退，不是隐藏的防守 ETF，也不是额外 PnL 止盈止损。",
    }


def _strategy_logic(best: Dict[str, Any]) -> Dict[str, Any]:
    params = best.get("parameters") or {}
    category_text = "; ".join(
        f"{category}: {', '.join(str(symbol) for symbol in symbols)}"
        for category, symbols in (best.get("category_symbols") or {}).items()
    )
    missing = ", ".join(str(category) for category in (best.get("missing_pit_categories") or []))
    return {
        "core_idea": (
            "在更宽的境内 ETF 类别池中做连续权重轮动：每个可交易资产分支都会得到目标权重，"
            "风险调整动量越强、趋势越好的分支权重越高，信号较弱的分支降权但不因未进前 N 名被硬清零。"
            "黄金、现金 ETF 和利率债 ETF 都只是普通分支；中证1000作为用户已批准的小盘代理纳入默认池。"
        ),
        "universe": (
            f"只使用人工审计过的稳定境内 ETF 注册池：{category_text}。"
            "默认池排除纳指、恒生、H 股、中国互联网和其他跨境宽基基金。"
            f"本地 PIT 数据缺口会保留为审计证据；当前缺口类别为：{missing or '无'}。"
        ),
        "entry_filters": [
            "ETF 类别和代表代码必须已经进入人工审计的境内注册池",
            "调仓日必须有当前可交易日线 bar，不能使用未来或陈旧价格",
            f"{params.get('liquidity_window', 20)} 日平均成交额至少达到 {float(params.get('min_avg_turnover') or 0):.0f}",
            "require_pit_size 启用时，信号日必须能看到点时 NAV 或基金规模证据",
            "动量强弱和趋势状态进入分数与权重，不再作为硬性的入选/清仓开关",
        ],
        "ranking_rule": (
            f"每个分支先选择分数最高的可交易代表 ETF。分数等于 {params.get('momentum_lookback', 126)} 日跳空动量"
            f"除以 max({params.get('volatility_window', 60)} 日年化波动率, {params.get('volatility_floor', 0.01)})；"
            f"若价格低于趋势均线，则扣减 {float(params.get('trend_penalty') or 0):.2f} 分。"
        ),
        "portfolio_construction": (
            f"对各分支分数做横截面标准化后用 softmax 转成倾斜权重，再与等权基准混合；"
            f"tilt_strength={float(params.get('tilt_strength') or 0):.2f}，temperature={float(params.get('temperature') or 0):.2f}。"
            f"单分支权重限制在 {float(params.get('min_branch_weight') or 0):.0%} 到 {float(params.get('max_branch_weight') or 0):.0%}，"
            f"目标总敞口 {float(params.get('target_exposure') or 0):.0%}，手数取整后的剩余资金保留为真实现金。"
        ),
        "rebalance_rule": (
            f"每隔 {int(params.get('holding_days') or 20)} 个交易日收盘后重新计算可见性、过滤条件、分数和目标持仓；"
            f"只有目标权重和当前权重差异达到 {float(params.get('rebalance_threshold') or 0):.0%} 才提交调仓，"
            "订单在下一交易日开盘按 T+1 口径执行。"
        ),
        "exit_rule": (
            "每次调仓时把持仓调到新的目标权重；权重下降会减仓，分支失去可交易资格时目标权重归零。"
            "若没有任何分支通过可交易约束，则卖出现有 ETF 并持有真实现金。"
            "本策略没有额外 PnL 止损或止盈包，风险退出主要来自目标权重下降、资格失效和现金回退。"
        ),
        "risk_budget": (
            "风险控制来自更宽的境内审计 ETF 池、连续权重上下限、调仓阈值、趋势惩罚、流动性过滤、波动率下限、"
            "点时 NAV/规模要求、真实现金回退、T+1 执行、手数检查和 5% ADV 参与率上限。"
        ),
        "parameter_explanations": _parameter_explanations(),
    }


def _write_outputs(
    rows: List[Dict[str, Any]],
    strict_reports: Dict[str, Dict[str, Any]],
    best: Dict[str, Any],
    universe: Dict[str, Any],
) -> Tuple[Path, Path]:
    strategy_dir = REPORT_ROOT / STRATEGY_ID
    runs_dir = strategy_dir / "runs"
    latest_dir = REPORT_ROOT / "latest"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "strategy_id": STRATEGY_ID,
        "run_ts": run_ts,
        "start": START.date().isoformat(),
        "end": END.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "rows": rows,
        "best": best,
        "pit_universe": universe,
        "strict_reports": strict_reports,
    }
    result_path = strategy_dir / "grid_result.json"
    last_result_path = strategy_dir / "last_result.json"
    result_text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    last_text = json.dumps(strict_reports[str(best["scenario"])], ensure_ascii=False, indent=2, default=str)
    result_path.write_text(result_text, encoding="utf-8")
    last_result_path.write_text(last_text, encoding="utf-8")
    (runs_dir / f"{run_ts}_grid_result.json").write_text(result_text, encoding="utf-8")
    (runs_dir / f"{run_ts}_result.json").write_text(last_text, encoding="utf-8")

    row = _hypothesis_row(best, strict_reports[str(best["scenario"])])
    _attach_followup_metrics(row, strategy_dir)
    result = {"run_id": f"{STRATEGY_ID}_full_report", "backtested": len(rows), "rejected": 0, "errors": []}
    generated = datetime.now(timezone.utc).isoformat()
    strict_html = build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated)
    full_html = build_research_full_report_html(result, [row], generated_at=generated)
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    walk_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    strict_report_path = strategy_dir / "strict_backtest_report.html"
    full_report_path = strategy_dir / "full_research_report.html"
    strict_report_path.write_text(strict_html, encoding="utf-8")
    full_report_path.write_text(full_html, encoding="utf-8")
    (strategy_dir / "fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (strategy_dir / "walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_strict_backtest_report.html").write_text(strict_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_full_research_report.html").write_text(full_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (runs_dir / f"{run_ts}_walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    (latest_dir / "strict_backtest_report.html").write_text(strict_html, encoding="utf-8")
    (latest_dir / "full_research_report.html").write_text(full_html, encoding="utf-8")
    (latest_dir / "fast_research_report.html").write_text(fast_html, encoding="utf-8")
    (latest_dir / "walkforward_audit_report.html").write_text(walk_html, encoding="utf-8")
    return full_report_path, result_path


def _attach_followup_metrics(row: Dict[str, Any], strategy_dir: Path) -> None:
    metrics = row.setdefault("metrics", {})
    stability_path = strategy_dir / "stability_result.json"
    if stability_path.exists():
        try:
            stability = json.loads(stability_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stability = {}
        parameter_sensitivity = stability.get("parameter_sensitivity") if isinstance(stability, dict) else None
        if isinstance(parameter_sensitivity, dict):
            metrics["parameter_sensitivity"] = parameter_sensitivity
    walkforward_path = strategy_dir / "walkforward_result.json"
    if walkforward_path.exists():
        try:
            walkforward = json.loads(walkforward_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            walkforward = {}
        payload = walkforward.get("walkforward") if isinstance(walkforward, dict) else None
        if isinstance(payload, dict):
            metrics["walkforward"] = payload
            stages = metrics.setdefault("research_stage_conclusions", {})
            stages["walkforward_strict_audit"] = {
                "label": "Walk-forward strict audit",
                "verdict": str(payload.get("verdict") or ("pass" if payload.get("is_viable") else "fail")),
                "conclusion": (
                    f"WF aggregate={payload.get('aggregate_oos_sharpe', 'n/a')}; "
                    f"worst={payload.get('worst_oos_sharpe', 'n/a')}; "
                    f"profitable={payload.get('pct_profitable_splits', 'n/a')}."
                ),
                "method": "Persisted purged walk-forward strict audit loaded from walkforward_result.json.",
            }


def _hypothesis_row(best: Dict[str, Any], strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    verdict = "pass" if _meets_goal(strict_report) else "fail"
    status = "needs_walkforward_validation" if verdict == "pass" else "needs_more_research"
    return {
        "strategy_id": STRATEGY_ID,
        "title": f"{TITLE} - {best['scenario']}",
        "source": "local_strategy",
        "status": status,
        "stage": "backtest",
        "decision_reason": "境内宽资产 ETF 连续权重轮动策略的项目 Backtester 严格回测 full report。",
        "thesis": "相比硬持有少数 Top ETF，连续权重调节应降低单一分支跳变风险，并让弱信号分支以低权重保留组合信息。",
        "metrics": {
            "strict_backtest": strict_report,
            "research_stage_conclusions": {
                "fast_research": {
                    "label": "快研究",
                    "verdict": "n/a",
                    "conclusion": "ETF 连续权重择时/轮动策略；截面 Rank IC 不适用。",
                    "method": "本地用户批准的策略候选，使用人工审计过的境内 ETF 注册池。",
                },
                "strict_backtest": {
                    "label": "严格回测",
                    "verdict": verdict,
                    "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
                    "method": "项目 Backtester，包含 T+1 开盘成交、境内 ETF 佣金、手数、状态/涨跌停检查、ADV 参与率上限和 ETF NAV/规模点时证据。",
                },
                "walkforward_strict_audit": {
                    "label": "Walk-forward 严格审计",
                    "verdict": "not_run",
                    "conclusion": "本次 full report 尚未完成 walk-forward 严格审计。",
                    "method": "等待严格回测报告复核后的后续验证。",
                },
                "final_decision": {
                    "label": "最终结论",
                    "verdict": status,
                    "conclusion": "已按用户批准进入顶层策略区；报告继续保留 walk-forward 与 stability 审计警示。",
                    "method": "决策使用当前指标 checklist 和生成报告中的风险提示。",
                },
            },
        },
        "evidence": {
            "local_strategy": True,
            "metadata": {"source": "local_strategy"},
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "etf_continuous_weight_rotation",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "scenario": best["scenario"],
                "symbols": best["symbols"],
                "universe": best["symbols"],
                "required_fields": [
                    "open",
                    "high",
                    "low",
                    "close",
                    "adj_close",
                    "volume",
                    "turnover",
                    "unit_nav",
                    "adj_nav",
                    "total_netasset",
                    "net_asset",
                ],
                "parameters": best.get("parameters") or {},
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(best),
                "lookback_days": best.get("parameters", {}).get("momentum_lookback"),
                "horizon_days": best.get("parameters", {}).get("holding_days"),
                "execution_lag_days": 1,
                "rebalance_frequency": f"每 {best.get('parameters', {}).get('holding_days', 20)} 个交易日",
                "fallback_symbol": "actual_cash",
                "risk_controls": {
                    "target_exposure": best.get("parameters", {}).get("target_exposure"),
                    "min_adv_value": best.get("parameters", {}).get("min_avg_turnover"),
                    "min_branch_weight": best.get("parameters", {}).get("min_branch_weight"),
                    "max_branch_weight": best.get("parameters", {}).get("max_branch_weight"),
                    "rebalance_threshold": best.get("parameters", {}).get("rebalance_threshold"),
                    "trend_penalty": best.get("parameters", {}).get("trend_penalty"),
                },
                "category_symbols": best.get("category_symbols", {}),
                "missing_pit_categories": best.get("missing_pit_categories", []),
                "pit_universe_enabled": True,
                "universe_selection_policy": best.get("universe_selection_policy", "audited_stable_etf_registry"),
                "universe_registry_version": best.get("universe_registry_version", "audited_stable_etf_registry_v1"),
                "registered_universe_counts": best.get("registered_universe_counts", {}),
                "universe_construction": "使用人工审计过的稳定境内 ETF 注册池；默认排除跨境宽基基金，并明确纳入中证1000。",
                "goal": {
                    "checklist": [
                        "max_adv_participation <= 5% ADV",
                        "total_trades > 50",
                        "CAGR/MaxDD tier gate",
                    ]
                },
            },
        },
    }


def _compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scenario": row.get("scenario"),
        "symbols": row.get("symbols"),
        "cagr": row.get("cagr"),
        "max_drawdown_pct": row.get("max_drawdown_pct"),
        "sharpe": row.get("sharpe"),
        "calmar_ratio": row.get("calmar_ratio"),
        "total_trades": row.get("total_trades"),
        "cost_drag_pct": row.get("cost_drag_pct"),
        "max_adv_participation": row.get("max_adv_participation"),
        "weighted_effective_bps": row.get("weighted_effective_bps"),
        "median_effective_bps": row.get("median_effective_bps"),
        "meets_goal": row.get("meets_goal"),
    }


if __name__ == "__main__":
    main()
