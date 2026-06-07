"""Strict CSI1000 internal index-enhanced multifactor strategy."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from quant.features.strategies.reject.ashare_csi300_strict_index_enhanced.strategy import (
    AShareCsi300StrictIndexEnhancedStrategy,
    ScoreSpec,
)
from quant.features.strategies.registry import strategy
from quant.shared.utils.logger import get_logger


STRATEGY_NAME = "ashare_csi1000_strict_index_enhanced"


@strategy(STRATEGY_NAME)
class AShareCsi1000StrictIndexEnhancedStrategy(AShareCsi300StrictIndexEnhancedStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        index_weights: Optional[Iterable[Dict[str, Any]]] = None,
        benchmark_symbol: str = "000852",
        holding_days: int = 20,
        max_positions: int = 120,
        target_exposure: float = 0.98,
        active_tilt: float = 3.40,
        min_weight_multiplier: float = 0.0,
        max_weight_multiplier: float = 8.00,
        max_single_weight: float = 0.055,
        min_price: float = 2.0,
        min_turnover: float = 30_000.0,
        max_volatility: float = 1.50,
        min_drawdown: float = -0.70,
        min_recent_momentum: float = -0.45,
        min_long_momentum: float = -0.55,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        stop_loss_pct: float = 0.45,
        take_profit_pct: float = 1.20,
        trailing_stop_pct: float = 0.35,
        lot_size: int = 100,
    ):
        super().__init__(
            symbols=symbols,
            index_weights=index_weights,
            benchmark_symbol=benchmark_symbol,
            holding_days=holding_days,
            max_positions=max_positions,
            target_exposure=target_exposure,
            active_tilt=active_tilt,
            min_weight_multiplier=min_weight_multiplier,
            max_weight_multiplier=max_weight_multiplier,
            max_single_weight=max_single_weight,
            min_price=min_price,
            min_turnover=min_turnover,
            max_volatility=max_volatility,
            min_drawdown=min_drawdown,
            min_recent_momentum=min_recent_momentum,
            min_long_momentum=min_long_momentum,
            enable_risk_exit=enable_risk_exit,
            risk_exit=risk_exit,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
            lot_size=lot_size,
        )
        self.name = STRATEGY_NAME
        self.logger = get_logger(f"Strategy.{STRATEGY_NAME}")

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [
            ("momentum", 0.28, True),
            ("recent_momentum", 0.18, True),
            ("roe", 0.14, True),
            ("grossprofit_margin", 0.10, True),
            ("volatility", 0.12, False),
            ("pb", 0.08, False),
            ("debt_to_assets", 0.06, False),
            ("index_weight", 0.04, True),
        ]
