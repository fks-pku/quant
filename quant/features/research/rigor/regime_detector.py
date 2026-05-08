import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def detect_regimes(
    data: Any,
    benchmark_col: str = "close",
    sma_lookback: int = 200,
) -> List[Dict[str, Any]]:
    if data is None or len(data) < sma_lookback:
        return []
    try:
        import pandas as pd
        close = data[benchmark_col] if isinstance(data, pd.DataFrame) else data
        sma = close.rolling(sma_lookback).mean()
        regimes = []
        current_regime = None
        regime_start = None
        for i in range(len(close)):
            if pd.isna(sma.iloc[i]):
                continue
            regime = "bull" if close.iloc[i] > sma.iloc[i] else "bear"
            if regime != current_regime:
                if current_regime is not None:
                    regimes.append({
                        "regime": current_regime,
                        "start": str(close.index[regime_start]),
                        "end": str(close.index[i - 1]),
                        "confidence": 0.7,
                    })
                current_regime = regime
                regime_start = i
        if current_regime is not None and regime_start is not None:
            regimes.append({
                "regime": current_regime,
                "start": str(close.index[regime_start]),
                "end": str(close.index[-1]),
                "confidence": 0.7,
            })
        return regimes
    except Exception as e:
        logger.warning(f"Regime detection failed: {e}")
        return []
