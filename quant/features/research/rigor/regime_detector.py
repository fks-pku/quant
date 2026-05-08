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


def benchmark_symbol_for_universe(symbols: List[str]) -> str:
    first = str(symbols[0]).strip().upper() if symbols else ""
    bare = first.replace("HK.", "").split(".")[0]
    if first.startswith("HK.") or first.endswith(".HK") or (bare.isdigit() and len(bare) == 5) or first == "HSI":
        return "HSI"
    if first.endswith((".SS", ".SZ")) or (bare.isdigit() and len(bare) == 6):
        return "000300"
    return "SPY"


def label_split_regime(
    split: Dict[str, Any],
    benchmark_data: Any,
    sma_lookback: int = 200,
    benchmark_col: str = "close",
) -> str:
    try:
        import pandas as pd

        close = _close_series(benchmark_data, benchmark_col)
        if close.empty:
            return "unknown"

        sma = close.rolling(sma_lookback).mean()
        frame = pd.DataFrame({"close": close, "sma": sma})
        window = _slice_split_window(frame, split)
        window = window.dropna()
        if not window.empty:
            bull_pct = (window["close"] > window["sma"]).mean()
            return "bull" if bull_pct >= 0.5 else "bear"

        return _label_split_by_rolling_return(close, split)
    except Exception as e:
        logger.warning(f"Split regime labeling failed: {e}")
        return "unknown"


def compute_regime_breakdown(splits: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    groups: Dict[str, List[float]] = {}
    for split in splits:
        regime = str(split.get("regime") or "unknown")
        groups.setdefault(regime, []).append(_safe_float(split.get("test_sharpe", 0.0)))

    breakdown: Dict[str, Dict[str, float]] = {}
    for regime, sharpes in groups.items():
        if not sharpes:
            continue
        breakdown[regime] = {
            "n_splits": float(len(sharpes)),
            "sharpe": float(sum(sharpes) / len(sharpes)),
            "worst_sharpe": float(min(sharpes)),
            "pct_profitable_splits": float(sum(1 for value in sharpes if value > 0) / len(sharpes)),
        }
    return breakdown


def _close_series(data: Any, benchmark_col: str) -> Any:
    import pandas as pd

    if data is None:
        return pd.Series(dtype=float)
    if isinstance(data, pd.DataFrame):
        if benchmark_col in data.columns:
            series = data[benchmark_col].copy()
        elif len(data.columns) == 1:
            series = data.iloc[:, 0].copy()
        else:
            return pd.Series(dtype=float)
        if "date" in data.columns and not isinstance(data.index, pd.DatetimeIndex):
            series.index = pd.to_datetime(data["date"], errors="coerce")
            series = series[series.index.notna()]
    else:
        series = pd.Series(data).copy()

    series = pd.to_numeric(series, errors="coerce").dropna()
    if isinstance(series.index, pd.DatetimeIndex):
        series = series.sort_index()
    return series


def _slice_split_window(frame: Any, split: Dict[str, Any]) -> Any:
    import pandas as pd

    date_start = split.get("test_start_date")
    date_end = split.get("test_end_date")
    if date_start is not None and date_end is not None and isinstance(frame.index, pd.DatetimeIndex):
        return frame.loc[(frame.index >= pd.Timestamp(date_start)) & (frame.index <= pd.Timestamp(date_end))]

    start = split.get("test_start")
    end = split.get("test_end")
    if isinstance(start, int) and isinstance(end, int):
        return frame.iloc[start:end + 1]

    start = split.get("test_start_date", start)
    end = split.get("test_end_date", end)
    if start is None or end is None or not isinstance(frame.index, pd.DatetimeIndex):
        return frame.iloc[0:0]

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    return frame.loc[(frame.index >= start_ts) & (frame.index <= end_ts)]


def _label_split_by_rolling_return(close: Any, split: Dict[str, Any], lookback: int = 63) -> str:
    import pandas as pd

    split_close = _slice_split_window(pd.DataFrame({"close": close}), split)
    if split_close.empty:
        return "unknown"

    context = _slice_context_window(close, split, lookback)
    if len(context) < 2:
        return "unknown"

    period = min(lookback, len(context) - 1)
    rolling_returns = context.pct_change(period).dropna()
    if rolling_returns.empty:
        return "unknown"

    return_frame = pd.DataFrame({"return": rolling_returns})
    split_returns = _slice_split_window(return_frame, split)["return"].dropna()
    values = split_returns if not split_returns.empty else rolling_returns
    return "bull" if float(values.median()) > 0 else "bear"


def _slice_context_window(close: Any, split: Dict[str, Any], lookback: int) -> Any:
    import pandas as pd

    start = split.get("test_start")
    end = split.get("test_end")
    date_start = split.get("test_start_date", start)
    date_end = split.get("test_end_date", end)

    if isinstance(start, int) and isinstance(end, int):
        return close.iloc[max(0, start - lookback):end + 1]

    if date_end is None or not isinstance(close.index, pd.DatetimeIndex):
        return close.iloc[0:0]

    end_ts = pd.Timestamp(date_end)
    end_pos = close.index.searchsorted(end_ts, side="right") - 1
    if end_pos < 0:
        return close.iloc[0:0]

    if date_start is not None:
        start_pos = close.index.searchsorted(pd.Timestamp(date_start), side="left")
    else:
        start_pos = end_pos
    start_pos = max(0, min(start_pos, end_pos) - lookback)
    return close.iloc[start_pos:end_pos + 1]


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
