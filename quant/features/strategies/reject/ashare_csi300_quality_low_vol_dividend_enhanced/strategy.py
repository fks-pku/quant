"""CSI300 proxy quality low-volatility dividend enhanced strategy."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies._mid_cap_common import ScoreSpec
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_csi300_quality_low_vol_dividend_enhanced"
DEFAULT_EXCLUDED_BOARD_PREFIXES = ("300", "301", "688", "689")


def _symbol_has_prefix(symbol: str, prefixes: tuple[str, ...]) -> bool:
    return any(str(symbol).startswith(prefix) for prefix in prefixes)


@strategy(STRATEGY_NAME)
class AShareCsi300QualityLowVolDividendEnhancedStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        enable_risk_exit: bool = True,
        risk_exit: Optional[Dict[str, Any]] = None,
        excluded_board_prefixes: Optional[List[str]] = None,
        **kwargs: Any,
    ):
        excluded_prefixes = DEFAULT_EXCLUDED_BOARD_PREFIXES if excluded_board_prefixes is None else tuple(
            str(prefix) for prefix in excluded_board_prefixes if str(prefix)
        )
        trade_symbols = [
            str(symbol)
            for symbol in list(symbols or [])
            if str(symbol) == "000300" or not _symbol_has_prefix(str(symbol), excluded_prefixes)
        ]
        risk_exit_config = dict(risk_exit or {})
        if "enabled" in risk_exit_config:
            enable_risk_exit = self._bool_value(risk_exit_config.get("enabled"), bool(enable_risk_exit))
        stop_loss_pct = risk_exit_config.get("stop_loss_pct", 0.20)
        take_profit_pct = risk_exit_config.get("take_profit_pct", 0.55)
        trailing_stop_pct = risk_exit_config.get("trailing_stop_pct", 0.16)
        if not enable_risk_exit:
            stop_loss_pct = 0.0
            take_profit_pct = 0.0
            trailing_stop_pct = 0.0
        defaults = {
            "holding_days": 20,
            "max_positions": 40,
            "target_weight_slots": 40,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.60,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 200_000.0,
            "use_market_timing": False,
            "symbol_trend_ma": 0,
            "min_long_momentum": -0.40,
            "min_recent_momentum": -0.30,
            "max_volatility": 1.20,
            "min_drawdown": -0.60,
            "max_pb": 30.0,
            "max_ps_ttm": 50.0,
            "min_roe": 0.0,
            "max_debt_to_assets": 0.0,
            "min_dividend_yield": 0.0,
            "score_profile": "csi300_quality_low_vol_dividend_index_enhanced_v2",
            "max_replacements_per_rebalance": 10,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
        }
        defaults.update(kwargs)
        self.enable_risk_exit = bool(enable_risk_exit)
        self.excluded_board_prefixes = tuple(excluded_prefixes)
        super().__init__(STRATEGY_NAME, symbols=trade_symbols, **defaults)

    @property
    def required_fields(self) -> List[str]:
        return [
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ttm",
            "roe",
            "debt_to_assets",
            "grossprofit_margin",
            "adj_close",
        ]

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [
            ("momentum", 0.22, True),
            ("recent_momentum", 0.12, True),
            ("roe", 0.16, True),
            ("volatility", 0.14, False),
            ("pb", 0.12, False),
            ("pe_ttm", 0.10, False),
            ("turnover_rate", 0.08, False),
            ("dv_ttm", 0.06, True),
        ]

    def _candidate_rejection(self, symbol: str, bar: Any) -> str:
        if self._is_permission_excluded_symbol(symbol):
            return "excluded_permission_board"
        return super()._candidate_rejection(symbol, bar)

    def _position_exit_reason(self, symbol: str, bar: Any) -> str:
        if self._is_permission_excluded_symbol(symbol):
            return "excluded_permission_board"
        return super()._position_exit_reason(symbol, bar)

    def _is_permission_excluded_symbol(self, symbol: str) -> bool:
        return _symbol_has_prefix(str(symbol), self.excluded_board_prefixes)

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
                "excluded_board_prefixes": list(self.excluded_board_prefixes),
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
