"""Alpha signal framework for strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
import pandas as pd


@dataclass
class KellyResult:
    full_kelly: float
    half_kelly: float
    quarter_kelly: float
    recommended_fraction: float


def calculate_kelly(
    win_rate: float,
    avg_win: float,
    avg_loss: float
) -> KellyResult:
    """
    Calculate Kelly fractions.
    
    Formula: f* = (p·b - q) / b
    where b = avg_win/avg_loss, q = 1 - p
    """
    p = win_rate
    q = 1 - p
    
    if avg_loss == 0:
        return KellyResult(0.0, 0.0, 0.0, 0.0)
    
    b = avg_win / avg_loss
    
    if b <= 0:
        return KellyResult(0.0, 0.0, 0.0, 0.0)
    
    full_kelly = (p * b - q) / b
    
    if full_kelly <= 0:
        return KellyResult(0.0, 0.0, 0.0, 0.0)
    
    half_kelly = full_kelly / 2
    quarter_kelly = full_kelly / 4
    
    recommended_fraction = quarter_kelly
    
    return KellyResult(
        full_kelly=full_kelly,
        half_kelly=half_kelly,
        quarter_kelly=quarter_kelly,
        recommended_fraction=recommended_fraction
    )


class RiskParityConstructor:
    """Volatility-based position sizing."""
    
    def __init__(
        self,
        lookback_days: int = 20,
        weight_floor: float = 0.02,
        weight_cap: float = 0.20
    ):
        self.lookback_days = lookback_days
        self.weight_floor = weight_floor
        self.weight_cap = weight_cap
    
    def construct(
        self,
        signals: List[Signal],
        prices: pd.DataFrame,
        nav: float
    ) -> Dict[str, float]:
        """
        Inverse volatility weighting with floor/cap.
        
        weight_i = (1/volatility_i) / sum(1/volatility_j)
        """
        if not signals:
            return {}
        
        long_signals = [s for s in signals if s.direction == "long"]
        
        if not long_signals:
            return {}
        
        volatilities = {}
        for signal in long_signals:
            symbol = signal.symbol
            if symbol in prices.columns:
                returns = prices[symbol].pct_change().dropna()
                if len(returns) >= self.lookback_days:
                    vol = returns.tail(self.lookback_days).std()
                else:
                    vol = returns.std()
                
                if vol > 0:
                    volatilities[symbol] = vol
                else:
                    volatilities[symbol] = 0.01
            else:
                volatilities[symbol] = 0.01
        
        inv_vols = {s.symbol: 1.0 / volatilities.get(s.symbol, 0.01) for s in long_signals}
        total_inv_vol = sum(inv_vols.values())
        
        if total_inv_vol == 0:
            return {}
        
        weights = {}
        for signal in long_signals:
            symbol = signal.symbol
            raw_weight = inv_vols[symbol] / total_inv_vol
            
            weight = max(self.weight_floor, min(self.weight_cap, raw_weight))
            weights[symbol] = weight
        
        return weights


@dataclass
class Signal:
    """Alpha signal with score and metadata."""
    symbol: str
    score: float
    timestamp: datetime
    factors: Dict[str, float]
    direction: str


class SignalGenerator:
    """Combines multiple alpha factors into a signal score."""

    def __init__(self, name: str):
        self.name = name
        self.factors: Dict[str, Callable] = {}
        self.weights: Dict[str, float] = {}
        self._factor_cache: Dict[str, pd.DataFrame] = {}

    def add_factor(self, name: str, func: Callable, weight: float = 1.0) -> None:
        """Add an alpha factor with its weight."""
        self.factors[name] = func
        self.weights[name] = weight

    def generate_signal(self, symbol: str, data: pd.DataFrame) -> Signal:
        """Generate a combined signal score for a symbol."""
        if data.empty:
            return Signal(symbol=symbol, score=0, timestamp=datetime.now(), factors={}, direction="neutral")

        factor_scores = {}
        for name, func in self.factors.items():
            try:
                score = func(data)
                factor_scores[name] = score
            except Exception:
                factor_scores[name] = 0

        total_weight = sum(self.weights.values())
        if total_weight == 0:
            total_weight = 1

        weighted_score = sum(
            factor_scores.get(name, 0) * weight
            for name, weight in self.weights.items()
        ) / total_weight

        direction = "long" if weighted_score > 0 else "short" if weighted_score < 0 else "neutral"

        return Signal(
            symbol=symbol,
            score=weighted_score,
            timestamp=datetime.now(),
            factors=factor_scores,
            direction=direction,
        )

    def generate_signals(self, symbols: List[str], data_dict: Dict[str, pd.DataFrame]) -> List[Signal]:
        """Generate signals for multiple symbols."""
        signals = []
        for symbol in symbols:
            data = data_dict.get(symbol, pd.DataFrame())
            signal = self.generate_signal(symbol, data)
            signals.append(signal)
        return signals


class PortfolioConstructor:
    """Converts signals to target weights."""

    def __init__(self, method: str = "equal_weight", kelly_params: Optional[Dict[str, float]] = None):
        self.method = method
        self.kelly_params = kelly_params or {"win_rate": 0.55, "avg_win": 1.0, "avg_loss": 1.0}

    def construct(
        self,
        signals: List[Signal],
        current_prices: Dict[str, float],
        nav: float,
        max_position_pct: float = 0.05,
        price_history: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Tuple[str, float]]:
        """
        Convert signals to target positions.
        Returns dict of {symbol: (direction, quantity)}
        """
        if self.method == "equal_weight":
            return self._equal_weight(signals, current_prices, nav, max_position_pct)
        elif self.method == "kelly":
            return self._kelly(signals, current_prices, nav, max_position_pct)
        elif self.method == "risk_parity":
            return self._risk_parity(signals, current_prices, nav, max_position_pct, price_history)
        else:
            return self._equal_weight(signals, current_prices, nav, max_position_pct)

    def _equal_weight(
        self,
        signals: List[Signal],
        current_prices: Dict[str, float],
        nav: float,
        max_position_pct: float,
    ) -> Dict[str, Tuple[str, float]]:
        """Equal weight portfolio construction."""
        long_signals = [s for s in signals if s.direction == "long"]
        short_signals = [s for s in signals if s.direction == "short"]

        n_long = len(long_signals)
        n_short = len(short_signals)

        positions = {}

        if n_long > 0:
            weight_per_position = min(1.0 / n_long, max_position_pct)
            for signal in long_signals:
                price = current_prices.get(signal.symbol, 100)
                quantity = int((nav * weight_per_position) / price)
                if quantity > 0:
                    positions[signal.symbol] = ("BUY", quantity)

        if n_short > 0:
            weight_per_position = min(1.0 / n_short, max_position_pct)
            for signal in short_signals:
                price = current_prices.get(signal.symbol, 100)
                quantity = int((nav * weight_per_position) / price)
                if quantity > 0:
                    positions[signal.symbol] = ("SELL", quantity)

        return positions

    def _kelly(
        self,
        signals: List[Signal],
        current_prices: Dict[str, float],
        nav: float,
        max_position_pct: float,
    ) -> Dict[str, Tuple[str, float]]:
        """Kelly criterion based portfolio construction."""
        long_signals = [s for s in signals if s.direction == "long"]
        short_signals = [s for s in signals if s.direction == "short"]

        positions = {}

        if long_signals:
            kelly_result = calculate_kelly(
                self.kelly_params.get("win_rate", 0.55),
                self.kelly_params.get("avg_win", 1.0),
                self.kelly_params.get("avg_loss", 1.0)
            )
            kelly_fraction = kelly_result.quarter_kelly
            
            weight_per_position = min(kelly_fraction / len(long_signals), max_position_pct)
            for signal in long_signals:
                price = current_prices.get(signal.symbol, 100)
                quantity = int((nav * weight_per_position) / price)
                if quantity > 0:
                    positions[signal.symbol] = ("BUY", quantity)

        if short_signals:
            weight_per_position = min(0.05, max_position_pct)
            for signal in short_signals:
                price = current_prices.get(signal.symbol, 100)
                quantity = int((nav * weight_per_position) / price)
                if quantity > 0:
                    positions[signal.symbol] = ("SELL", quantity)

        return positions

    def _risk_parity(
        self,
        signals: List[Signal],
        current_prices: Dict[str, float],
        nav: float,
        max_position_pct: float,
        price_history: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Tuple[str, float]]:
        """Risk parity portfolio construction."""
        long_signals = [s for s in signals if s.direction == "long"]
        short_signals = [s for s in signals if s.direction == "short"]

        positions = {}

        if long_signals:
            rp = RiskParityConstructor()
            if price_history is not None and not price_history.empty:
                weights = rp.construct(long_signals, price_history, nav)
            else:
                price_df = pd.DataFrame([current_prices])
                weights = rp.construct(long_signals, price_df, nav)
            
            for symbol, weight in weights.items():
                price = current_prices.get(symbol, 100)
                quantity = int((nav * weight) / price)
                if quantity > 0:
                    positions[symbol] = ("BUY", quantity)

        if short_signals:
            short_weight = max_position_pct / len(short_signals)
            for signal in short_signals:
                price = current_prices.get(signal.symbol, 100)
                quantity = int((nav * short_weight) / price)
                if quantity > 0:
                    positions[signal.symbol] = ("SELL", quantity)

        return positions


class ExecutionScheduler:
    """Splits large orders into child orders using TWAP/VWAP."""

    def __init__(self, method: str = "TWAP", duration_minutes: int = 60):
        self.method = method
        self.duration_minutes = duration_minutes

    def schedule_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        start_time: datetime,
        interval_minutes: int = 5,
    ) -> List[Tuple[datetime, float]]:
        """
        Schedule a large order into smaller child orders.
        Returns list of (execution_time, quantity) tuples.
        """
        if abs(quantity) <= 0:
            return []

        num_child_orders = max(1, self.duration_minutes // interval_minutes)
        base_quantity = abs(quantity) / num_child_orders

        schedule = []
        current_time = start_time

        for i in range(num_child_orders):
            schedule.append((current_time, base_quantity if side == "BUY" else -base_quantity))
            current_time += timedelta(minutes=interval_minutes)

        return schedule


class AlphaEngine:
    """
    Main alpha generation engine combining all components.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.signal_generator = SignalGenerator("default")
        self.portfolio_constructor = PortfolioConstructor(
            method=self.config.get("portfolio_method", "equal_weight")
        )
        self.execution_scheduler = ExecutionScheduler(
            method=self.config.get("execution_method", "TWAP")
        )
        self._signal_history: List[Signal] = []

    def update_signal_generator(self, signal_gen: SignalGenerator) -> None:
        """Update the signal generator."""
        self.signal_generator = signal_gen

    def update_portfolio_constructor(self, portfolio_const: PortfolioConstructor) -> None:
        """Update the portfolio constructor."""
        self.portfolio_constructor = portfolio_const

    def generate_and_construct(
        self,
        symbols: List[str],
        data_dict: Dict[str, pd.DataFrame],
        current_prices: Dict[str, float],
        nav: float,
    ) -> Dict[str, Tuple[str, float]]:
        """Generate signals and construct target portfolio."""
        signals = self.signal_generator.generate_signals(symbols, data_dict)
        self._signal_history.extend(signals)

        positions = self.portfolio_constructor.construct(
            signals, current_prices, nav
        )

        return positions

    def get_signal_history(self, symbol: Optional[str] = None) -> List[Signal]:
        """Get historical signals."""
        if symbol:
            return [s for s in self._signal_history if s.symbol == symbol]
        return self._signal_history.copy()
