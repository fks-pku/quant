"""Built-in factor library - Momentum, Mean Reversion, Volatility, Volume factors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
import pandas as pd
import numpy as np


@dataclass
class FactorResult:
    name: str
    value: float
    normalized: float
    timestamp: pd.Timestamp


class Factor(ABC):
    """Abstract base class for factors."""

    def __init__(self, name: str, lookback: int = 20):
        self.name = name
        self.lookback = lookback

    @abstractmethod
    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate factor value from price data."""
        pass

    def normalize(self, value: float, history: pd.Series) -> float:
        """Normalize factor value using z-score."""
        if len(history) < 2 or history.std() == 0:
            return 0.0
        z_score = (value - history.mean()) / history.std()
        return max(-3.0, min(3.0, z_score))


class MomentumFactor(Factor):
    """Momentum factor - trend strength over lookback period."""

    def __init__(self, lookback: int = 20):
        super().__init__("momentum", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate momentum as percentage return over lookback."""
        if len(data) < self.lookback:
            return 0.0
        
        close = data['close']
        current = close.iloc[-1]
        past = close.iloc[-self.lookback]
        
        if past == 0:
            return 0.0
        
        return (current - past) / past * 100


class MeanReversionFactor(Factor):
    """Mean reversion factor - deviation from moving average."""

    def __init__(self, lookback: int = 20):
        super().__init__("mean_reversion", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate as z-score of price deviation from SMA."""
        if len(data) < self.lookback:
            return 0.0
        
        close = data['close']
        sma = close.rolling(self.lookback).mean().iloc[-1]
        current = close.iloc[-1]
        
        if sma == 0:
            return 0.0
        
        deviation = (current - sma) / sma * 100
        return -deviation


class VolatilityFactor(Factor):
    """Volatility factor - realized volatility over lookback."""

    def __init__(self, lookback: int = 20):
        super().__init__("volatility", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate as annualized standard deviation of returns."""
        if len(data) < self.lookback:
            return 0.0
        
        returns = data['close'].pct_change().dropna()
        
        if len(returns) < 2:
            return 0.0
        
        realized_vol = returns.tail(self.lookback).std()
        annualized_vol = realized_vol * np.sqrt(252)
        
        return annualized_vol * 100


class VolumeFactor(Factor):
    """Volume factor - volume trend and anomaly detection."""

    def __init__(self, lookback: int = 20):
        super().__init__("volume", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate as volume deviation from average volume."""
        if len(data) < self.lookback:
            return 0.0
        
        volume = data['volume']
        current_volume = volume.iloc[-1]
        avg_volume = volume.tail(self.lookback).mean()
        
        if avg_volume == 0:
            return 0.0
        
        volume_ratio = current_volume / avg_volume
        return (volume_ratio - 1) * 100


class RSIFactor(Factor):
    """RSI factor - Relative Strength Index."""

    def __init__(self, lookback: int = 14):
        super().__init__("rsi", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate RSI."""
        if len(data) < self.lookback + 1:
            return 50.0
        
        close = data['close']
        delta = close.diff()
        
        gain = delta.where(delta > 0, 0).rolling(self.lookback).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(self.lookback).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi.iloc[-1] if not rsi.empty else 50.0


class MACDFactor(Factor):
    """MACD factor - Moving Average Convergence Divergence."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__("macd", slow)
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate MACD histogram."""
        if len(data) < self.slow:
            return 0.0
        
        close = data['close']
        ema_fast = close.ewm(span=self.fast).mean()
        ema_slow = close.ewm(span=self.slow).mean()
        
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=self.signal).mean()
        
        histogram = (macd - signal_line).iloc[-1]
        return histogram


class BollingerBandFactor(Factor):
    """Bollinger Band factor - position within bands."""

    def __init__(self, lookback: int = 20, num_std: float = 2.0):
        super().__init__("bollinger", lookback)
        self.num_std = num_std

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate position within Bollinger Bands (-1 to +1)."""
        if len(data) < self.lookback:
            return 0.0
        
        close = data['close']
        sma = close.rolling(self.lookback).mean().iloc[-1]
        std = close.rolling(self.lookback).std().iloc[-1]
        
        upper = sma + (self.num_std * std)
        lower = sma - (self.num_std * std)
        
        current = close.iloc[-1]
        
        if std == 0:
            return 0.0
        
        position = (current - lower) / (upper - lower)
        return (position - 0.5) * 2


class ATRFactor(Factor):
    """ATR factor - Average True Range for volatility."""

    def __init__(self, lookback: int = 14):
        super().__init__("atr", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate ATR."""
        if len(data) < self.lookback + 1:
            return 0.0
        
        high = data['high']
        low = data['low']
        close = data['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(self.lookback).mean().iloc[-1]
        
        return atr


class VolatilityRegimeFactor(Factor):
    """Volatility regime factor - detects market regime based on VIX or realized vol."""

    REGIME_BULL = 1.0
    REGIME_CHOP = 0.0
    REGIME_BEAR = -1.0

    def __init__(
        self,
        lookback: int = 20,
        vix_bull_threshold: float = 15.0,
        vix_bear_threshold: float = 25.0,
        use_realized_vol: bool = False,
    ):
        super().__init__("volatility_regime", lookback)
        self.vix_bull_threshold = vix_bull_threshold
        self.vix_bear_threshold = vix_bear_threshold
        self.use_realized_vol = use_realized_vol

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate regime: 1.0 (bull), 0.0 (chop), -1.0 (bear)."""
        if self.use_realized_vol:
            return self._calculate_from_realized_vol(data)
        else:
            return self._calculate_from_vix(data)

    def _calculate_from_realized_vol(self, data: pd.DataFrame) -> float:
        if len(data) < self.lookback:
            return self.REGIME_CHOP

        returns = data['close'].pct_change().dropna()
        if len(returns) < self.lookback:
            return self.REGIME_CHOP

        realized_vol = returns.tail(self.lookback).std() * np.sqrt(252) * 100

        if realized_vol < self.vix_bull_threshold:
            return self.REGIME_BULL
        elif realized_vol > self.vix_bear_threshold:
            return self.REGIME_BEAR
        else:
            return self.REGIME_CHOP

    def _calculate_from_vix(self, data: pd.DataFrame) -> float:
        if 'vix_close' not in data.columns and len(data.columns) == 1:
            return self.REGIME_CHOP

        if 'vix_close' in data.columns:
            vix = data['vix_close'].iloc[-1]
        else:
            return self.REGIME_CHOP

        if len(data) >= self.lookback:
            vix_sma = data['vix_close'].tail(self.lookback).mean()
        else:
            vix_sma = vix

        if vix_sma < self.vix_bull_threshold:
            return self.REGIME_BULL
        elif vix_sma > self.vix_bear_threshold:
            return self.REGIME_BEAR
        else:
            return self.REGIME_CHOP

    def get_regime_label(self, value: float) -> str:
        if value >= 0.5:
            return "bull"
        elif value <= -0.5:
            return "bear"
        else:
            return "chop"


class QualityFactor(Factor):
    """Quality factor - based on ROE and debt-to-equity."""

    def __init__(self, lookback: int = 252):
        super().__init__("quality", lookback)

    def calculate(self, data: pd.DataFrame) -> float:
        """Calculate quality score from fundamental data.

        Requires 'roe' and 'debt_to_equity' in data columns.
        Returns composite quality score: high ROE + low debt = good quality.
        """
        if len(data) < self.lookback:
            return 0.0

        if 'roe' not in data.columns or 'debt_to_equity' not in data.columns:
            return 0.0

        roe = data['roe'].iloc[-1]
        debt_to_equity = data['debt_to_equity'].iloc[-1]

        roe_normalized = max(-2, min(2, (roe - 0.1) / 0.1))
        debt_normalized = max(-2, min(2, (0.5 - debt_to_equity) / 0.5))

        return (roe_normalized + debt_normalized) / 2


class FactorLibrary:
    """Collection of factors with factory methods."""

    def __init__(self):
        self._factors: Dict[str, Factor] = {}

    def get_factor(self, name: str, **kwargs) -> Factor:
        """Get or create a factor by name."""
        if name == "momentum":
            return MomentumFactor(kwargs.get("lookback", 20))
        elif name == "mean_reversion":
            return MeanReversionFactor(kwargs.get("lookback", 20))
        elif name == "volatility":
            return VolatilityFactor(kwargs.get("lookback", 20))
        elif name == "volume":
            return VolumeFactor(kwargs.get("lookback", 20))
        elif name == "rsi":
            return RSIFactor(kwargs.get("lookback", 14))
        elif name == "macd":
            return MACDFactor(
                kwargs.get("fast", 12),
                kwargs.get("slow", 26),
                kwargs.get("signal", 9)
            )
        elif name == "bollinger":
            return BollingerBandFactor(
                kwargs.get("lookback", 20),
                kwargs.get("num_std", 2.0)
            )
        elif name == "atr":
            return ATRFactor(kwargs.get("lookback", 14))
        elif name == "volatility_regime":
            return VolatilityRegimeFactor(
                lookback=kwargs.get("lookback", 20),
                vix_bull_threshold=kwargs.get("vix_bull_threshold", 15.0),
                vix_bear_threshold=kwargs.get("vix_bear_threshold", 25.0),
                use_realized_vol=kwargs.get("use_realized_vol", False),
            )
        elif name == "quality":
            return QualityFactor(kwargs.get("lookback", 252))
        else:
            raise ValueError(f"Unknown factor: {name}")

    def calculate_factor(
        self,
        name: str,
        data: pd.DataFrame,
        **kwargs
    ) -> float:
        """Calculate a factor directly."""
        factor = self.get_factor(name, **kwargs)
        return factor.calculate(data)

    def calculate_multiple(
        self,
        factor_names: list[str],
        data: pd.DataFrame,
        **kwargs
    ) -> Dict[str, float]:
        """Calculate multiple factors at once."""
        results = {}
        for name in factor_names:
            try:
                results[name] = self.calculate_factor(name, data, **kwargs)
            except Exception:
                results[name] = 0.0
        return results


def create_factor_function(name: str, **kwargs) -> Callable[[pd.DataFrame], float]:
    """Create a standalone factor function."""
    def factor_func(data: pd.DataFrame) -> float:
        library = FactorLibrary()
        return library.calculate_factor(name, data, **kwargs)
    return factor_func


MOMENTUM = create_factor_function("momentum", lookback=20)
MEAN_REVERSION = create_factor_function("mean_reversion", lookback=20)
VOLATILITY = create_factor_function("volatility", lookback=20)
RSI = create_factor_function("rsi", lookback=14)
MACD = create_factor_function("macd")
BOLLINGER = create_factor_function("bollinger", lookback=20, num_std=2.0)
ATR = create_factor_function("atr", lookback=14)
