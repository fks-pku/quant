import logging
from datetime import datetime
from typing import List, Optional

from quant.features.research.models import ResearchConfig, ResearchResult, RawStrategy
from quant.features.research.scout import StrategyScout
from quant.features.research.evaluator import StrategyEvaluator
from quant.features.research.integrator import StrategyIntegrator
from quant.features.research.pool import CandidatePool

logger = logging.getLogger(__name__)


class ResearchEngine:
    def __init__(
        self,
        config: Optional[ResearchConfig] = None,
        scout: Optional[StrategyScout] = None,
        evaluator: Optional[StrategyEvaluator] = None,
        integrator: Optional[StrategyIntegrator] = None,
        pool: Optional[CandidatePool] = None,
    ):
        self.config = config or ResearchConfig()
        self.scout = scout or StrategyScout()
        self.evaluator = evaluator or StrategyEvaluator()
        self.integrator = integrator or StrategyIntegrator()
        self.pool = pool or CandidatePool()

    def run_full_pipeline(self, sources: Optional[List[str]] = None) -> ResearchResult:
        result = ResearchResult()
        logger.info("Starting research pipeline")

        raw_strategies = self.scout.search(sources=sources, max_results=self.config.max_results_per_source)
        result.discovered = len(raw_strategies)
        logger.info(f"Discovered {result.discovered} strategies")

        integrated_ids = []
        for raw in raw_strategies:
            try:
                report = self.evaluator.evaluate(raw)
                result.evaluated += 1

                passes_filter = report.suitability_score >= self.config.evaluation_threshold
                if report.data_requirement == "high-frequency":
                    passes_filter = passes_filter and report.daily_adaptable

                if not passes_filter:
                    logger.info(f"'{raw.title}' filtered out (suitability={report.suitability_score})")
                    result.rejected += 1
                    continue

                strategy_id = self.integrator.integrate(raw, report)
                if strategy_id:
                    result.integrated += 1
                    integrated_ids.append(strategy_id)
                else:
                    result.errors.append(f"Integration failed for '{raw.title}'")
            except Exception as e:
                logger.error(f"Pipeline error for '{raw.title}': {e}")
                result.errors.append(str(e))

        if self.config.auto_backtest and integrated_ids:
            self._run_backtests(integrated_ids, result)

        logger.info(f"Pipeline complete: {result}")
        return result

    def _run_backtests(self, strategy_ids: List[str], result: ResearchResult) -> None:
        from quant.features.backtest.engine import Backtester
        from quant.features.strategies.registry import StrategyRegistry
        from quant.infrastructure.data.providers.duckdb_provider import DuckDBProvider
        from quant.features.backtest.walkforward import DataFrameProvider
        import pandas as pd

        for sid in strategy_ids:
            try:
                registry = StrategyRegistry()
                strategy_class = registry.get(sid)
                if strategy_class is None:
                    result.errors.append(f"Strategy {sid} not in registry for backtest")
                    continue

                symbols = self.config.default_symbols
                start = datetime.strptime(self.config.default_backtest_start, "%Y-%m-%d")
                end = datetime.strptime(self.config.default_backtest_end, "%Y-%m-%d")

                db_provider = DuckDBProvider()
                db_provider.connect()
                all_data = []
                for sym in symbols:
                    bars = db_provider.get_bars(sym, start, end, "1d")
                    if not bars.empty:
                        all_data.append(bars)
                db_provider.disconnect()

                if not all_data:
                    result.errors.append(f"No data for {sid}")
                    continue

                data_df = pd.concat(all_data, ignore_index=True)
                data_provider = DataFrameProvider(data_df)
                strategy = strategy_class(symbols=symbols)

                config = {
                    "backtest": {"slippage_bps": 5},
                    "execution": {"commission": {"US": {"type": "per_share", "per_share": 0.005, "min_per_order": 1.0}}},
                    "data": {"default_timeframe": "1d"},
                    "risk": {"max_position_pct": 0.20, "max_sector_pct": 1.0, "max_daily_loss_pct": 0.10, "max_leverage": 2.0},
                }

                backtester = Backtester(config)
                bt_result = backtester.run(start=start, end=end, strategies=[strategy], initial_cash=100000, data_provider=data_provider, symbols=symbols)

                from quant.api.state.runtime import AVAILABLE_STRATEGIES
                for name, info in AVAILABLE_STRATEGIES.items():
                    if info["id"] == sid:
                        info["backtest"] = {
                            "sharpe": round(bt_result.sharpe_ratio, 2),
                            "max_dd": round(bt_result.max_drawdown_pct, 2),
                            "cagr": round(bt_result.total_return * 100 / max(1, (end - start).days / 365.25), 2),
                            "win_rate": round(bt_result.win_rate * 100, 2),
                            "period": f"{self.config.default_backtest_start}-{self.config.default_backtest_end}",
                        }
                        meta = info.setdefault("research_meta", {})
                        meta["backtest_result"] = info["backtest"]
                        if bt_result.sharpe_ratio < self.config.backtest_sharpe_threshold:
                            self.pool.reject(sid, reason=f"Backtest Sharpe {bt_result.sharpe_ratio:.2f} below threshold")
                            result.rejected += 1
                        else:
                            result.backtested += 1
                        break
            except Exception as e:
                logger.error(f"Backtest failed for {sid}: {e}")
                result.errors.append(f"Backtest error for {sid}: {e}")
                self.pool.reject(sid, reason=f"Backtest exception: {e}")
                result.rejected += 1
