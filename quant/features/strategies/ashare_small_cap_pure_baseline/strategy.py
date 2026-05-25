"""A-share pure small-cap baseline."""

from typing import List, Optional

from quant.features.strategies._small_cap_common import AShareSmallCapRotationBase
from quant.features.strategies.registry import strategy


@strategy("ashare_small_cap_pure_baseline")
class AShareSmallCapPureBaselineStrategy(AShareSmallCapRotationBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        max_positions: int = 20,
        rebalance_interval: int = 10,
        min_price: float = 5.0,
        min_adv_value: float = 20000.0,
        lot_size: int = 100,
        target_exposure: float = 0.55,
        market_timing_symbol: str = "",
        market_trend_window: int = 0,
        market_momentum_lookback: int = 0,
        market_momentum_threshold: float = 0.0,
        market_risk_off_exposure: float = 0.0,
        stock_trend_window: int = 0,
        max_pb: float = 12.0,
        max_ps_ttm: float = 20.0,
        max_pe_ttm: float = 0.0,
        max_turnover_rate_f: float = 35.0,
        max_volume_ratio: float = 5.0,
        require_positive_pe: bool = False,
        require_quality_fields: bool = True,
        broad_index_symbols: Optional[List[str]] = None,
        broad_index_exposure: float = 0.0,
        broad_momentum_lookback: int = 63,
        broad_volatility_window: int = 20,
        broad_volatility_penalty: float = 0.5,
        broad_min_momentum: float = 0.0,
        relative_strength_lookback: int = 20,
        weak_small_cap_exposure: Optional[float] = None,
    ):
        super().__init__(
            "ashare_small_cap_pure_baseline",
            symbols=symbols,
            max_positions=max_positions,
            rebalance_interval=rebalance_interval,
            min_price=min_price,
            min_adv_value=min_adv_value,
            lot_size=lot_size,
            target_exposure=target_exposure,
            market_timing_symbol=market_timing_symbol,
            market_trend_window=market_trend_window,
            market_momentum_lookback=market_momentum_lookback,
            market_momentum_threshold=market_momentum_threshold,
            market_risk_off_exposure=market_risk_off_exposure,
            stock_trend_window=stock_trend_window,
            max_pb=max_pb,
            max_ps_ttm=max_ps_ttm,
            max_pe_ttm=max_pe_ttm,
            max_turnover_rate_f=max_turnover_rate_f,
            max_volume_ratio=max_volume_ratio,
            require_positive_pe=require_positive_pe,
            require_quality_fields=require_quality_fields,
            broad_index_symbols=broad_index_symbols,
            broad_index_exposure=broad_index_exposure,
            broad_momentum_lookback=broad_momentum_lookback,
            broad_volatility_window=broad_volatility_window,
            broad_volatility_penalty=broad_volatility_penalty,
            broad_min_momentum=broad_min_momentum,
            relative_strength_lookback=relative_strength_lookback,
            weak_small_cap_exposure=weak_small_cap_exposure,
        )
