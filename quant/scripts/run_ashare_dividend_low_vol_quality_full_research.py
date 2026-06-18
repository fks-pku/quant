"""Run full research report for A-share dividend low-vol quality enhanced."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

_project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_project_root))

from quant.api.research_bp import (  # noqa: E402
    _DuckDBDailyDateProvider,
    _cn_survivorship_audit,
    _load_cn_benchmark_provider,
    _load_lot_sizes,
    _strict_backtest_report,
)
from quant.domain.models.market import is_cn_symbol  # noqa: E402
from quant.features.backtest.benchmark import BenchmarkProvider  # noqa: E402
from quant.features.backtest.engine import Backtester  # noqa: E402
from quant.features.strategies.reject.ashare_dividend_low_vol_quality_enhanced.strategy import (  # noqa: E402
    AShareDividendLowVolQualityEnhancedStrategy,
)
from quant.features.trading.portfolio import Portfolio  # noqa: E402
from quant.features.trading.risk import RiskEngine  # noqa: E402
from quant.features.trading.sub_portfolio import SubPortfolio  # noqa: E402
from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider  # noqa: E402
from quant.infrastructure.research.reporting import (  # noqa: E402
    build_research_full_report_html,
    build_research_stage_report_html,
)


START = datetime(2016, 1, 1)
END = datetime(2025, 12, 31)
INITIAL_CASH = 10_000.0
TIMING_SYMBOL = "000300"
STRATEGY_ID = "ashare_dividend_low_vol_quality_enhanced"
TITLE = "A股红利低波质量增强"
REPORT_ROOT = Path("quant/infrastructure/var/research/reports")
SOURCE_URLS = [
    "https://www.fxbaogao.com/detail/3884054",
    "https://www.spglobal.com/spdji/zh/education/article/talkingpoints-finding-resilience-amid-uncertainty-a-low-volatility-high-dividend-approach-for-the-a-share-market/",
    "https://bigquant.com/square/paper/24851222-5625-4b08-a8c7-763131260b3f",
]
COMMISSION_CFG = {
    "US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0},
    "HK": {"type": "hk_realistic"},
    "CN": {"type": "cn_realistic", "fund_percent": 0.0001, "fund_min_per_order": 0.0},
}
EXECUTION_COST_MODEL = {
    "enabled": True,
    "name": "cn_daily_liquidity_impact",
    "markets": ["CN"],
    "tick_size": 0.01,
    "half_spread_ticks": 0.5,
    "min_slippage_bps": 5,
    "max_participation_rate": 0.02,
    "impact_coefficient": 0.35,
    "volatility_fallback": 0.03,
    "adv_value_field": "adv20_value",
    "volatility_field": "volatility20",
}
STRATEGY_PARAMS: Dict[str, Any] = {
    "holding_days": 60,
    "max_positions": 10,
    "target_weight_slots": 10,
    "max_position_pct": 0.95,
    "cap_percentile_low": 0.50,
    "cap_percentile_high": 1.00,
    "min_price": 5.0,
    "min_turnover": 80_000.0,
    "use_market_timing": True,
    "timing_ma": 200,
    "timing_exit_buffer": 0.95,
    "timing_momentum_lookback": 60,
    "min_timing_momentum": -0.12,
    "max_volatility": 0.60,
    "min_drawdown": -0.45,
    "max_pb": 15.0,
    "max_ps_ttm": 25.0,
    "min_roe": 6.0,
    "max_debt_to_assets": 95.0,
    "min_dividend_yield": 1.0,
    "score_profile": "dividend_low_vol_quality_enhanced",
}

DETAIL_SECTION = """
<h3>策略执行流程</h3>
<div class="table-wrap"><table><thead><tr><th>步骤</th><th>执行逻辑</th><th>信息边界</th></tr></thead><tbody>
<tr><td>1. Universe</td><td>使用历史每日 total_mv 前 800 名的并集作为大盘/中大盘候选池，再用当日 status、价格、成交额和 total_mv 分位过滤。</td><td>不是用当前指数成分回溯；未来新上市股票只有在有当日 bar 和 PIT 字段后才可能入选。</td></tr>
<tr><td>2. 红利</td><td>要求当日 point-in-time dv_ttm 至少 1%，并在横截面中偏好更高股息率。</td><td>估值和股息来自 daily_basic 当日侧表。</td></tr>
<tr><td>3. 低波</td><td>计算 120 日后复权收益波动率和最大回撤，低波动、浅回撤得分更高。</td><td>价格信号只使用当日及以前后复权价格。</td></tr>
<tr><td>4. 质量增强</td><td>要求 ROE 至少 6%，排序偏好高 ROE、高毛利率、低资产负债率。</td><td>财务字段按 ann_date 做 point-in-time asof join。</td></tr>
<tr><td>5. 组合与执行</td><td>每 60 个交易日最多持有 10 只等权股票；沪深300 200 日均线和中期动量作为风险开关。</td><td>严格回测包含 T+1、100 股手数、涨跌停、停牌、佣金税费和 2% ADV 冲击约束。</td></tr>
</tbody></table></div>
"""


def main() -> None:
    args = _parse_args()
    start = _parse_date(args.start, START)
    end = _parse_date(args.end, END)
    output_root = Path(args.output_root) if args.output_root else REPORT_ROOT
    symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit = _load_inputs(
        start,
        end,
        args.historical_large_cap_rank_limit,
        args.top_market_cap_limit,
    )
    symbols = [*symbols, TIMING_SYMBOL]
    print(f"Running {STRATEGY_ID} on {len(symbols)} symbols from {start.date()} to {end.date()}", flush=True)
    strict_report = _run_backtest(symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit, start, end)
    walkforward = _walkforward_from_strict_equity(strict_report)
    report_path, payload_path = _write_reports(output_root, symbols, strict_report, walkforward, start, end)
    metrics = strict_report.get("metrics") or {}
    print(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "full_report_path": str(report_path),
                "payload_path": str(payload_path),
                "sharpe": metrics.get("sharpe"),
                "cagr": metrics.get("cagr"),
                "total_return": metrics.get("total_return"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "total_trades": metrics.get("total_trades"),
                "walkforward": {
                    "aggregate_oos_sharpe": walkforward.get("aggregate_oos_sharpe"),
                    "worst_oos_sharpe": walkforward.get("worst_oos_sharpe"),
                    "pct_profitable_splits": walkforward.get("pct_profitable_splits"),
                    "is_viable": walkforward.get("is_viable"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START.date().isoformat())
    parser.add_argument("--end", default=END.date().isoformat())
    parser.add_argument("--historical-large-cap-rank-limit", type=int, default=800)
    parser.add_argument("--top-market-cap-limit", type=int, default=0)
    parser.add_argument("--output-root", default="")
    return parser.parse_args()


def _parse_date(value: str, fallback: datetime) -> datetime:
    return datetime.strptime(str(value), "%Y-%m-%d") if value else fallback


def _load_inputs(
    start: datetime,
    end: datetime,
    historical_rank_limit: int,
    top_market_cap_limit: int,
) -> Tuple[List[str], Dict[str, int], BenchmarkProvider, Dict[str, Any], Dict[str, Any]]:
    db_provider = DuckDBProvider()
    db_provider.connect()
    try:
        if top_market_cap_limit > 0:
            symbols = _load_latest_top_market_cap_symbols(db_provider, start, end, top_market_cap_limit)
        elif historical_rank_limit > 0:
            symbols = _load_historical_large_cap_symbols(db_provider, start, end, historical_rank_limit)
        else:
            symbols = _load_ashare_symbols(db_provider, start, end)
        lot_sizes = _load_lot_sizes(db_provider, [*symbols, TIMING_SYMBOL], is_cn_symbol)
        benchmark_provider, benchmark_meta = _load_cn_benchmark_provider(db_provider, start, end, BenchmarkProvider)
        survivorship_audit = _cn_survivorship_audit(db_provider, start, end, formula_key=STRATEGY_ID)
    finally:
        db_provider.disconnect()
    return symbols, lot_sizes, benchmark_provider, benchmark_meta, survivorship_audit


def _load_ashare_symbols(db_provider: DuckDBProvider, start: datetime, end: datetime) -> List[str]:
    rows = db_provider.storage.conn.execute(
        """
        SELECT DISTINCT symbol
        FROM daily_cn_ochl
        WHERE CAST(timestamp AS DATE) BETWEEN ? AND ?
          AND regexp_matches(symbol, '^[0236][0-9]{5}$')
          AND NOT starts_with(symbol, '200')
          AND symbol != ?
        ORDER BY symbol
        """,
        [start, end, TIMING_SYMBOL],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _load_historical_large_cap_symbols(
    db_provider: DuckDBProvider,
    start: datetime,
    end: datetime,
    rank_limit: int,
) -> List[str]:
    storage = db_provider.storage
    if not getattr(storage, "_daily_basic_available")():
        raise RuntimeError("daily_basic sidecar unavailable")
    rows = storage.conn.execute(
        """
        WITH ranked AS (
            SELECT
                db.trade_date,
                db.symbol,
                row_number() OVER (
                    PARTITION BY db.trade_date
                    ORDER BY db.total_mv DESC NULLS LAST
                ) AS rank
            FROM daily_basic.cn_daily_basic db
            WHERE db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
              AND db.total_mv IS NOT NULL
              AND regexp_matches(db.symbol, '^[0236][0-9]{5}$')
              AND NOT starts_with(db.symbol, '200')
              AND db.symbol != ?
        )
        SELECT DISTINCT symbol
        FROM ranked
        WHERE rank <= ?
          AND EXISTS (
              SELECT 1
              FROM daily_cn_ochl bars
              WHERE bars.symbol = ranked.symbol
                AND CAST(bars.timestamp AS DATE) BETWEEN ? AND ?
          )
        ORDER BY symbol
        """,
        [start, end, TIMING_SYMBOL, int(rank_limit), start, end],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _load_latest_top_market_cap_symbols(
    db_provider: DuckDBProvider,
    start: datetime,
    end: datetime,
    limit: int,
) -> List[str]:
    storage = db_provider.storage
    if not getattr(storage, "_daily_basic_available")():
        raise RuntimeError("daily_basic sidecar unavailable")
    rows = storage.conn.execute(
        """
        WITH latest AS (
            SELECT max(trade_date) AS trade_date
            FROM daily_basic.cn_daily_basic
        ),
        ranked AS (
            SELECT
                db.symbol,
                row_number() OVER (ORDER BY db.total_mv DESC NULLS LAST) AS rank
            FROM daily_basic.cn_daily_basic db
            JOIN latest ON db.trade_date = latest.trade_date
            WHERE db.total_mv IS NOT NULL
              AND regexp_matches(db.symbol, '^[0236][0-9]{5}$')
              AND NOT starts_with(db.symbol, '200')
              AND db.symbol != ?
              AND EXISTS (
                  SELECT 1
                  FROM daily_cn_ochl bars
                  WHERE bars.symbol = db.symbol
                    AND CAST(bars.timestamp AS DATE) BETWEEN ? AND ?
              )
        )
        SELECT symbol
        FROM ranked
        WHERE rank <= ?
        ORDER BY rank
        """,
        [TIMING_SYMBOL, start, end, int(limit)],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _run_backtest(
    symbols: List[str],
    lot_sizes: Dict[str, int],
    benchmark_provider: BenchmarkProvider,
    benchmark_meta: Dict[str, Any],
    survivorship_audit: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    data_provider = _DuckDBDailyDateProvider(
        symbols,
        start,
        end,
        include_daily_basic=True,
        include_financial_indicators=True,
        include_execution_liquidity_features=True,
    )
    data_provider._chunk_size = max(252, int(getattr(data_provider, "_chunk_size", 63) or 63))
    strategy = AShareDividendLowVolQualityEnhancedStrategy(symbols=symbols, **STRATEGY_PARAMS)
    backtest_config = {"slippage_bps": 5, "execution_cost_model": dict(EXECUTION_COST_MODEL)}
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


def _write_reports(
    output_root: Path,
    symbols: List[str],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Tuple[Path, Path]:
    strategy_dir = output_root / STRATEGY_ID
    run_dir = strategy_dir / "runs"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    row = _hypothesis_row(symbols, strict_report, walkforward, start, end)
    result = _result_payload(row)
    generated = datetime.now(timezone.utc).isoformat()
    fast_html = build_research_stage_report_html("fast_research", result, [row], generated_at=generated)
    strict_html = _insert_detail_section(build_research_stage_report_html("strict_backtest", result, [row], generated_at=generated))
    walkforward_html = build_research_stage_report_html("walkforward_strict_audit", result, [row], generated_at=generated)
    full_html = _insert_detail_section(build_research_full_report_html(result, [row], generated_at=generated))
    payload = {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "initial_cash": INITIAL_CASH,
        "symbols": symbols,
        "parameters": STRATEGY_PARAMS,
        "strict_report": strict_report,
        "walkforward": walkforward,
        "hypothesis": row,
        "result": result,
    }
    payload_path = strategy_dir / "last_result.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (run_dir / f"{run_ts}_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    reports = {
        "fast_research_report.html": fast_html,
        "strict_backtest_report.html": strict_html,
        "walkforward_audit_report.html": walkforward_html,
        "full_research_report.html": full_html,
    }
    for filename, html in reports.items():
        (strategy_dir / filename).write_text(html, encoding="utf-8")
        (run_dir / f"{run_ts}_{filename}").write_text(html, encoding="utf-8")
    latest_dir = output_root / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    for filename, html in reports.items():
        (latest_dir / filename).write_text(html, encoding="utf-8")
    (latest_dir / "last_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    metadata = _latest_report_metadata(output_root, strategy_dir, latest_dir, run_ts, generated)
    (latest_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return strategy_dir / "full_research_report.html", payload_path


def _latest_report_metadata(
    output_root: Path,
    strategy_dir: Path,
    latest_dir: Path,
    run_ts: str,
    generated_at: str,
) -> Dict[str, Any]:
    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(output_root.parent))
        except ValueError:
            return str(path)

    stage_filenames = {
        "fast_research": "fast_research_report.html",
        "strict_backtest": "strict_backtest_report.html",
        "walkforward_strict_audit": "walkforward_audit_report.html",
    }
    return {
        "report_id": STRATEGY_ID,
        "run_name": run_ts,
        "updated_at": generated_at,
        "full_report": {
            "available": True,
            "path": rel(strategy_dir / "full_research_report.html"),
            "latest_path": rel(latest_dir / "full_research_report.html"),
            "filename": "full_research_report.html",
        },
        "stage_reports": {
            stage: {
                "path": rel(strategy_dir / filename),
                "latest_path": rel(latest_dir / filename),
                "filename": filename,
            }
            for stage, filename in stage_filenames.items()
        },
    }


def _hypothesis_row(
    symbols: List[str],
    strict_report: Dict[str, Any],
    walkforward: Dict[str, Any],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    metrics = {
        "strict_backtest": strict_report,
        "walkforward": walkforward,
        "parameter_sensitivity": _parameter_sensitivity(strict_report),
    }
    metrics["research_stage_conclusions"] = _stage_conclusions(strict_report, walkforward)
    status = _row_status(strict_report, walkforward)
    return {
        "strategy_id": STRATEGY_ID,
        "title": TITLE,
        "thesis": "红利提供现金流和价值锚，低波控制回撤，质量因子减少高股息陷阱。",
        "status": status,
        "stage": "full_research",
        "source": "broker_report_review",
        "source_url": SOURCE_URLS[0],
        "decision_reason": _decision_reason(strict_report, walkforward),
        "metrics": metrics,
        "evidence": {
            "source": "broker financial engineering dividend low-vol research summaries",
            "source_urls": SOURCE_URLS,
            "local_strategy": True,
            "discovery_quality": {
                "score": 0.76,
                "source_type": "broker_financial_engineering_report",
                "matched_terms": ["红利低波", "质量增强", "低波动", "股息率", "A股"],
                "risk_flags": ["broker_summary_not_full_pdf", "factor_crowding", "financial_data_lag_required"],
            },
            "strategy_spec": {
                "strategy_id": STRATEGY_ID,
                "strategy_type": "a_share_large_cap_dividend_low_vol_quality",
                "signal_formula_key": STRATEGY_ID,
                "prediction_direction": "higher_is_better",
                "symbols_count": len(symbols),
                "universe": symbols,
                "universe_source": "historical daily top total_mv union; live bars and PIT fields required at each rebalance",
                "lookback_days": 252,
                "horizon_days": int(STRATEGY_PARAMS["holding_days"]),
                "execution_lag_days": 1,
                "rebalance_frequency": f"every {STRATEGY_PARAMS['holding_days']} trading days",
                "required_fields": AShareDividendLowVolQualityEnhancedStrategy(symbols=[]).required_fields,
                "parameters": STRATEGY_PARAMS,
                "parameter_explanations": _parameter_explanations(),
                "strategy_logic": _strategy_logic(symbols, start, end),
                "source_report_urls": SOURCE_URLS,
                "universe_start": start.date().isoformat(),
                "universe_end": end.date().isoformat(),
                "goal": {"cagr_gt": 0.10, "max_drawdown_gte": -0.30, "max_adv_participation_lte": 0.05},
            },
        },
    }


def _stage_conclusions(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metrics = strict_report.get("metrics") or {}
    cagr = float(metrics.get("cagr") or 0.0)
    sharpe = float(metrics.get("sharpe") or 0.0)
    max_dd = float(metrics.get("max_drawdown_pct") or 0.0)
    return {
        "fast_research": {
            "label": "快研究",
            "verdict": "not_run",
            "conclusion": "本轮未单独运行全量 Rank IC；直接进入 strict Backtester 和冻结参数样本外切分。",
            "method": "策略源自红利低波券商金工研究方向，本轮重点验证严格执行约束后的可交易收益。",
        },
        "strict_backtest": {
            "label": "严格回测",
            "verdict": "pass" if _strict_pass(strict_report) else ("warn" if cagr > 0 else "fail"),
            "conclusion": f"严格回测：Sharpe={sharpe:.2f}，CAGR={cagr:.2%}，MaxDD={max_dd:.2%}。",
            "method": "项目 Backtester；T+1、涨跌停、停牌、100 股手数、真实佣金税费、5bps 最小滑点和 cn_daily_liquidity_impact。",
        },
        "walkforward_strict_audit": {
            "label": "Walk-forward strict audit",
            "verdict": str(walkforward.get("verdict") or "fail"),
            "conclusion": (
                f"冻结参数日历 OOS：aggregate={float(walkforward.get('aggregate_oos_sharpe') or 0.0):.2f}，"
                f"worst={float(walkforward.get('worst_oos_sharpe') or 0.0):.2f}，"
                f"盈利 split={float(walkforward.get('pct_profitable_splits') or 0.0):.0%}。"
            ),
            "method": "从严格回测 equity curve 切分 2018-2019、2020-2021、2022-2023、2024-2025 四个冻结参数 OOS 窗口。",
        },
    }


def _strategy_logic(symbols: List[str], start: datetime, end: datetime) -> Dict[str, Any]:
    return {
        "core_idea": "在中大盘股票中同时暴露红利、低波和质量因子，用 ROE/毛利率/低杠杆降低高股息陷阱。",
        "universe": f"历史每日 total_mv 前 800 名并集，回测窗口 {start.date()} 到 {end.date()}，实际取数 {len(symbols)} 个 symbol。",
        "entry_filters": [
            "dv_ttm >= 1.0",
            "roe >= 6.0",
            "debt_to_assets <= 95.0",
            "price >= 5 and average turnover >= 80000",
            "ST/suspended/non-listed/tradable=false rejected",
        ],
        "ranking_rule": "score = 28% dividend yield + 20% low volatility + 16% ROE + 10% gross margin + 10% low debt + 8% low PB + 5% drawdown + 3% recent momentum。",
        "portfolio_construction": "每次调仓最多 10 只，目标总仓位 95%，按 10 个目标槽位等权分配，100 股取整。",
        "rebalance_rule": "每 60 个交易日收盘后重算候选，下一交易日开盘执行。",
        "exit_rule": "持仓触发 ST、停牌、非上市、不可交易、低价、趋势或风控护栏时每日尝试退出；否则调仓跌出目标篮子时卖出。",
        "risk_budget": "A 股 long-only，沪深300 200 日均线风险开关，T+1，2% ADV 最大参与率，真实佣金税费与冲击成本。",
        "parameter_explanations": _parameter_explanations(),
    }


def _parameter_explanations() -> Dict[str, str]:
    return {
        "holding_days": "60 个交易日近似季度调仓，匹配红利低波低换手特征。",
        "max_positions": "最多持有股票数；受 20,000 初始资金和 100 股手数约束，默认不做 30-50 只过度分散。",
        "cap_percentile_low": "按当日 point-in-time total_mv 排名后的下分位过滤，避免微盘暴露。",
        "min_dividend_yield": "股息率门槛，避免低股息股票仅靠质量或低波进入组合。",
        "min_roe": "质量门槛，减少盈利能力弱的高股息陷阱。",
        "max_debt_to_assets": "杠杆质量过滤，过高资产负债率不入选。",
        "max_volatility": "波动率上限，超过后不进入候选。",
        "timing_ma": "沪深300 风险开关均线窗口。",
        "timing_exit_buffer": "指数跌破均线后的退出缓冲。",
        "max_position_pct": "组合目标总仓位，保留少量现金缓冲。",
    }


def _parameter_sensitivity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    metrics = strict_report.get("metrics") or {}
    return {
        "status": "single_frozen_parameter_set",
        "method": "Broker-thesis implementation with one frozen parameter set; no optimization grid was used in this run.",
        "base_params": STRATEGY_PARAMS,
        "selected_params": STRATEGY_PARAMS,
        "best_params": STRATEGY_PARAMS,
        "tested_count": 1,
        "pass_count": 1 if _strict_pass(strict_report) else 0,
        "max_degradation_pct": 0.0,
        "rows": [
            {
                "name": "base_dividend_low_vol_quality",
                "parameters": STRATEGY_PARAMS,
                "cagr": metrics.get("cagr"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "sharpe": metrics.get("sharpe"),
                "verdict": "pass" if _strict_pass(strict_report) else "warn",
            }
        ],
    }


def _walkforward_from_strict_equity(strict_report: Dict[str, Any]) -> Dict[str, Any]:
    points = ((strict_report.get("equity_curve") or {}).get("strategy") or [])
    if not points:
        return _empty_walkforward("strict equity curve missing")
    frame = pd.DataFrame(points)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna().sort_values("date")
    split_ranges = [
        ("2018-01-01", "2019-12-31", "2016-01-01", "2017-12-31"),
        ("2020-01-01", "2021-12-31", "2018-01-01", "2019-12-31"),
        ("2022-01-01", "2023-12-31", "2020-01-01", "2021-12-31"),
        ("2024-01-01", "2025-12-31", "2022-01-01", "2023-12-31"),
    ]
    splits = []
    for index, (test_start, test_end, train_start, train_end) in enumerate(split_ranges, start=1):
        split_frame = frame[(frame["date"] >= pd.Timestamp(test_start)) & (frame["date"] <= pd.Timestamp(test_end))]
        if len(split_frame) < 2:
            continue
        returns = split_frame["value"].pct_change(fill_method=None).dropna()
        has_trades = bool(split_frame["value"].diff().abs().fillna(0.0).gt(1e-9).any())
        sharpe = _sharpe(returns)
        total_return = float(split_frame["value"].iloc[-1] / split_frame["value"].iloc[0] - 1.0)
        splits.append(
            {
                "split": index,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "oos_sharpe": sharpe,
                "test_sharpe": sharpe,
                "max_drawdown": _max_drawdown(split_frame["value"]),
                "trade_count": None if has_trades else 0,
                "has_trades": has_trades,
                "total_return": total_return,
                "verdict": "pass" if has_trades and sharpe > 0 and total_return > 0 else ("fail" if has_trades else "excluded_no_trade"),
                "parameters": "frozen parameters",
            }
        )
    if not splits:
        return _empty_walkforward("no OOS split had enough equity points")
    evaluated_splits = [split for split in splits if split.get("has_trades") is not False]
    if not evaluated_splits:
        return {
            "verdict": "fail",
            "reason": "no OOS split had equity movement; likely no executable trades in the audited windows",
            "is_viable": False,
            "capacity_ok": False,
            "thresholds": _walkforward_thresholds(),
            "aggregate_oos_sharpe": 0.0,
            "worst_oos_sharpe": 0.0,
            "pct_profitable_splits": 0.0,
            "deflated_sharpe_ratio": None,
            "sharpe_degradation": 0.0,
            "regime_breakdown": {},
            "bull_only_warning": False,
            "n_splits": 0,
            "evaluated_splits": 0,
            "total_splits": len(splits),
            "no_trade_splits": len(splits),
            "splits": splits,
        }
    sharpes = [float(split["oos_sharpe"]) for split in evaluated_splits]
    profitable = [1.0 if float(split.get("total_return") or 0.0) > 0 else 0.0 for split in evaluated_splits]
    aggregate = statistics.mean(sharpes)
    worst = min(sharpes)
    pct_profitable = statistics.mean(profitable)
    capacity_ok = _strict_capacity_ok(strict_report)
    is_viable = worst >= 0.3 and pct_profitable >= 0.5 and capacity_ok
    verdict = "pass" if is_viable else ("warn" if aggregate > 0 and pct_profitable >= 0.5 else "fail")
    return {
        "verdict": verdict,
        "reason": "Frozen-parameter calendar OOS audit derived from strict Backtester equity; no parameter refit.",
        "is_viable": is_viable,
        "capacity_ok": capacity_ok,
        "thresholds": _walkforward_thresholds(),
        "aggregate_oos_sharpe": aggregate,
        "worst_oos_sharpe": worst,
        "pct_profitable_splits": pct_profitable,
        "deflated_sharpe_ratio": None,
        "sharpe_degradation": aggregate - worst if aggregate > 0 else 0.0,
        "regime_breakdown": {},
        "bull_only_warning": False,
        "n_splits": len(evaluated_splits),
        "evaluated_splits": len(evaluated_splits),
        "total_splits": len(splits),
        "no_trade_splits": len(splits) - len(evaluated_splits),
        "splits": splits,
    }


def _empty_walkforward(reason: str) -> Dict[str, Any]:
    return {
        "verdict": "fail",
        "reason": reason,
        "is_viable": False,
        "capacity_ok": False,
        "aggregate_oos_sharpe": 0.0,
        "worst_oos_sharpe": 0.0,
        "pct_profitable_splits": 0.0,
        "thresholds": _walkforward_thresholds(),
        "splits": [],
        "n_splits": 0,
        "evaluated_splits": 0,
        "total_splits": 0,
        "no_trade_splits": 0,
    }


def _walkforward_thresholds() -> Dict[str, Any]:
    return {
        "train_window_days": 504,
        "test_window_days": 504,
        "step_days": 504,
        "purge_days": 60,
        "embargo_days": 20,
        "min_train_observations": 252,
        "min_worst_oos_sharpe": 0.3,
        "min_profitable_splits_pct": 0.5,
        "min_deflated_sharpe_ratio": 0.95,
        "max_adv_pct": 0.05,
    }


def _sharpe(returns: pd.Series) -> float:
    returns = returns.dropna()
    if returns.empty:
        return 0.0
    std = float(returns.std())
    return float(returns.mean() / std * math.sqrt(252.0)) if std > 0 else 0.0


def _max_drawdown(equity: Iterable[float]) -> float:
    peak = None
    worst = 0.0
    for value in equity:
        number = float(value)
        peak = number if peak is None else max(peak, number)
        if peak and peak > 0:
            worst = min(worst, number / peak - 1.0)
    return worst


def _result_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    status = str(row.get("status") or "")
    return {
        "run_id": f"{STRATEGY_ID}_full_research",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "discovered": 1,
        "evaluated": 1,
        "specified": 1,
        "validated": 0,
        "validated_passed": 0,
        "integrated": 1,
        "backtested": 1,
        "walkforward_passed": 1 if ((row.get("metrics") or {}).get("walkforward") or {}).get("is_viable") else 0,
        "rejected": 1 if status == "rejected" else 0,
        "errors": [],
    }


def _row_status(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    if _strict_pass(strict_report) and bool(walkforward.get("is_viable")):
        return "needs_fast_validation"
    metrics = strict_report.get("metrics") or {}
    if float(metrics.get("cagr") or 0.0) <= 0 or float(metrics.get("sharpe") or 0.0) <= 0:
        return "rejected"
    return "needs_more_validation"


def _decision_reason(strict_report: Dict[str, Any], walkforward: Dict[str, Any]) -> str:
    metrics = strict_report.get("metrics") or {}
    return (
        f"strict Sharpe={float(metrics.get('sharpe') or 0.0):.2f}, "
        f"CAGR={float(metrics.get('cagr') or 0.0):.2%}, "
        f"MaxDD={float(metrics.get('max_drawdown_pct') or 0.0):.2%}; "
        f"walkforward viable={bool(walkforward.get('is_viable'))}; fast Rank IC not run."
    )


def _strict_pass(strict_report: Dict[str, Any]) -> bool:
    metrics = strict_report.get("metrics") or {}
    return (
        float(metrics.get("cagr") or 0.0) > 0.10
        and float(metrics.get("max_drawdown_pct") or 0.0) >= -0.30
        and int(metrics.get("total_trades") or 0) > 50
        and _strict_capacity_ok(strict_report)
    )


def _strict_capacity_ok(strict_report: Dict[str, Any]) -> bool:
    capacity = strict_report.get("capacity") or {}
    try:
        return float(capacity.get("max_adv_participation")) <= 0.05
    except (TypeError, ValueError):
        return False


def _insert_detail_section(html: str) -> str:
    marker = "<h2>2. 严格回测证据</h2>\n<h3>策略执行逻辑</h3>"
    if marker in html:
        return html.replace(marker, f"<h2>2. 严格回测证据</h2>\n{DETAIL_SECTION}<h3>策略执行逻辑</h3>", 1)
    marker = "<h2>3. Strategy Logic And Core Evidence</h2>\n"
    return html.replace(marker, f"{marker}{DETAIL_SECTION}", 1)


if __name__ == "__main__":
    main()
