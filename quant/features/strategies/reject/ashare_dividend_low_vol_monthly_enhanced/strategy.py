"""A-share monthly dividend low-volatility enhanced strategy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies._mid_cap_common import ScoreSpec
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_dividend_low_vol_monthly_enhanced"


@strategy(STRATEGY_NAME)
class AShareDividendLowVolMonthlyEnhancedStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        risk_exit_config = dict(risk_exit or {})
        if "enabled" in risk_exit_config:
            enable_risk_exit = self._bool_value(risk_exit_config.get("enabled"), bool(enable_risk_exit))
        stop_loss_pct = risk_exit_config.get("stop_loss_pct", 0.18)
        take_profit_pct = risk_exit_config.get("take_profit_pct", 0.45)
        trailing_stop_pct = risk_exit_config.get("trailing_stop_pct", 0.16)
        if not enable_risk_exit:
            stop_loss_pct = 0.0
            take_profit_pct = 0.0
            trailing_stop_pct = 0.0
        defaults = {
            "holding_days": 20,
            "max_positions": 30,
            "target_weight_slots": 30,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.20,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 80_000.0,
            "use_market_timing": False,
            "symbol_trend_ma": 0,
            "min_long_momentum": -1.0,
            "min_recent_momentum": -1.0,
            "max_volatility": 0.80,
            "min_drawdown": -0.60,
            "max_pb": 8.0,
            "max_ps_ttm": 20.0,
            "min_roe": 0.0,
            "max_debt_to_assets": 0.0,
            "min_dividend_yield": 2.0,
            "score_profile": STRATEGY_NAME,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
        }
        defaults.update(kwargs)
        self.enable_risk_exit = bool(enable_risk_exit)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)
        self.volatility_lookback = 20
        self.drawdown_lookback = 60

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [
            ("dv_ttm", 0.32, True),
            ("volatility", 0.28, False),
            ("pb", 0.14, False),
            ("drawdown", 0.10, True),
            ("roe", 0.08, True),
            ("debt_to_assets", 0.04, False),
            ("recent_momentum", 0.04, True),
        ]

    def get_guard_diagnostics(self) -> Dict[str, Any]:
        diagnostics = super().get_guard_diagnostics()
        diagnostics["parameters"].update(
            {
                "enable_risk_exit": self.enable_risk_exit,
                "risk_exit": {
                    "enabled": self.enable_risk_exit,
                    "stop_loss_pct": self.stop_loss_pct,
                    "take_profit_pct": self.take_profit_pct,
                    "trailing_stop_pct": self.trailing_stop_pct,
                },
                "volatility_lookback": self.volatility_lookback,
                "drawdown_lookback": self.drawdown_lookback,
            }
        )
        return diagnostics

    @staticmethod
    def _bool_value(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"", "nan", "none", "null"}:
                return default
            if text in {"0", "false", "f", "no", "n"}:
                return False
            if text in {"1", "true", "t", "yes", "y"}:
                return True
        try:
            if value != value:
                return default
        except Exception:
            return default
        return bool(value)
