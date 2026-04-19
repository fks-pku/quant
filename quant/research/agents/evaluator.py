import uuid
from datetime import datetime, timedelta
from typing import Optional, List

from quant.research.models import StrategyCandidate, ResearchReport
from quant.research.templates.prompts.evaluator_prompt import EVALUATOR_ANALYSIS_PROMPT
from quant.strategies.registry import StrategyRegistry


class EvaluatorAgent:
    def __init__(
        self,
        llm_adapter,
        data_provider=None,
        backtest_config: dict = None,
    ):
        self.llm = llm_adapter
        self.data_provider = data_provider
        self.backtest_config = backtest_config or {}

    def _run_backtest(self, strategy_name: str, symbols: List[str], start, end) -> Optional[dict]:
        registry = StrategyRegistry()
        if strategy_name not in registry.list_strategies():
            return None

        if self.data_provider is None:
            return None

        try:
            from quant.core.backtester import Backtester
            import pandas as pd

            all_data = []
            for symbol in symbols:
                bars = self.data_provider.get_bars(symbol, start, end, "1d")
                if bars is not None and not bars.empty:
                    all_data.append(bars)

            if not all_data:
                return None

            combined = pd.concat(all_data, ignore_index=True)
            from quant.core.backtester import Backtester as _Bt

            backtester = _Bt(self.backtest_config, lot_sizes={})
            strategy = self._instantiate_strategy(strategy_name, symbols)
            if strategy is None:
                return None

            from quant.core.walkforward import _DataFrameProvider
            data_provider = _DataFrameProvider(combined)

            result = backtester.run(
                start=start,
                end=end,
                strategies=[strategy],
                initial_cash=100000,
                data_provider=data_provider,
                symbols=symbols,
            )

            return {
                "final_nav": result.final_nav,
                "total_return": result.total_return,
                "sharpe_ratio": result.sharpe_ratio,
                "sortino_ratio": result.sortino_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "total_trades": result.metrics.total_trades,
            }
        except Exception:
            return None

    def _instantiate_strategy(self, strategy_name: str, symbols: List[str]):
        import importlib
        module_path = f"quant.strategies.{strategy_name.lower()}.strategy"
        try:
            mod = importlib.import_module(module_path)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (isinstance(attr, type)
                        and hasattr(attr, '_registry_name')
                        and attr._registry_name == strategy_name):
                    try:
                        return attr(symbols=symbols)
                    except Exception:
                        try:
                            return attr()
                        except Exception:
                            return None
        except Exception:
            pass
        return None

    async def evaluate(
        self,
        candidate: StrategyCandidate,
        symbols: List[str] = None,
        start=None,
        end=None,
    ) -> ResearchReport:
        symbols = symbols or ["AAPL", "MSFT", "GOOGL"]
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=730)

        metrics = self._run_backtest(candidate.strategy_name, symbols, start, end)

        if metrics is None:
            return ResearchReport(
                id=str(uuid.uuid4()),
                candidate=candidate,
                backtest_metrics={},
                recommendation="reject",
                recommendation_reasoning="Backtest failed - strategy could not be instantiated or no data",
                generated_at=datetime.now(),
            )

        recommendation = "watchlist"
        reasoning = ""

        if metrics.get("sharpe_ratio", 0) > 1.0 and metrics.get("max_drawdown_pct", 1) < 0.2:
            recommendation = "adopt"
            reasoning = f"Sharpe {metrics['sharpe_ratio']:.2f} > 1.0, DD {metrics['max_drawdown_pct']*100:.1f}% < 20%"
        elif metrics.get("sharpe_ratio", 0) < 0:
            recommendation = "reject"
            reasoning = f"Negative Sharpe {metrics['sharpe_ratio']:.2f}"
        else:
            reasoning = f"Sharpe {metrics.get('sharpe_ratio', 0):.2f}, needs monitoring"

        llm_analysis = ""
        if self.llm:
            try:
                prompt = EVALUATOR_ANALYSIS_PROMPT.format(
                    strategy_name=candidate.strategy_name,
                    sharpe=metrics.get("sharpe_ratio", 0),
                    sortino=metrics.get("sortino_ratio", 0),
                    max_dd=metrics.get("max_drawdown_pct", 0) * 100,
                    win_rate=metrics.get("win_rate", 0) * 100,
                    profit_factor=metrics.get("profit_factor", 0),
                    total_trades=metrics.get("total_trades", 0),
                    total_return=metrics.get("total_return", 0) * 100,
                    wf_result="Not run in P0",
                )
                result = self.llm.analyze(prompt, {})
                if isinstance(result, dict):
                    llm_analysis = result.get("llm_analysis", "")
                    if result.get("recommendation") in ("adopt", "watchlist", "reject"):
                        recommendation = result["recommendation"]
                        reasoning = result.get("recommendation_reasoning", reasoning)
            except Exception:
                pass

        return ResearchReport(
            id=str(uuid.uuid4()),
            candidate=candidate,
            backtest_metrics=metrics,
            llm_analysis=llm_analysis,
            recommendation=recommendation,
            recommendation_reasoning=reasoning,
            generated_at=datetime.now(),
        )
