"""Performance analytics - Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Tuple
import pandas as pd
import numpy as np

from quant.shared.models.trade import Trade


__all__ = ["Trade", "calculate_sharpe", "calculate_sortino", "calculate_max_drawdown",
           "calculate_performance_metrics", "PerformanceMetrics",
           "calculate_rolling_sharpe", "calculate_rolling_sortino",
           "calculate_statistical_significance"]


def calculate_sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio."""
    if returns.empty or returns.std() == 0:
        return 0.0
    return np.sqrt(periods_per_year) * returns.mean() / returns.std()


def calculate_sortino(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized Sortino ratio (downside deviation)."""
    if returns.empty:
        return 0.0
    
    downside_returns = returns[returns < 0]
    if downside_returns.empty or downside_returns.std() == 0:
        return 0.0
    
    downside_std = downside_returns.std()
    return np.sqrt(periods_per_year) * returns.mean() / downside_std


def calculate_max_drawdown(equity_curve: pd.Series) -> Tuple[float, float, datetime, datetime]:
    """
    Returns (drawdown_value, drawdown_pct, peak_date, trough_date).
    """
    if equity_curve.empty:
        return 0.0, 0.0, datetime.now(), datetime.now()
    
    running_max = equity_curve.expanding().max()
    drawdown = equity_curve - running_max
    drawdown_pct = drawdown / running_max
    
    trough_idx = drawdown.idxmin()
    if pd.isna(trough_idx):
        return 0.0, 0.0, datetime.now(), datetime.now()
    
    trough_date = equity_curve.index[trough_idx] if isinstance(trough_idx, int) else trough_idx
    
    if isinstance(trough_idx, int):
        peak_idx = equity_curve[:trough_idx].idxmax() if trough_idx > 0 else 0
    else:
        trough_pos = equity_curve.index.get_loc(trough_idx)
        if trough_pos > 0:
            peak_idx = equity_curve.iloc[:trough_pos].idxmax()
        else:
            peak_idx = equity_curve.index[0]
    
    peak_date = equity_curve.index[peak_idx] if isinstance(peak_idx, int) else peak_idx
    
    return float(drawdown.min()), float(drawdown_pct.min()), peak_date, trough_date


def calculate_calmar(returns: pd.Series, max_dd: float, periods_per_year: int = 252) -> float:
    """Calmar ratio (annualized return / max drawdown)."""
    if max_dd == 0:
        return 0.0
    annualized_return = returns.mean() * periods_per_year
    return annualized_return / abs(max_dd)


def _round_trip_trades(trades: List[Trade]) -> List[Trade]:
    """Filter to SELL-side trades only — these represent completed round-trips."""
    return [t for t in trades if t.side == "SELL"]


def calculate_win_rate(trades: List[Trade]) -> float:
    """Percentage of profitable round-trip trades."""
    rt = _round_trip_trades(trades)
    if not rt:
        return 0.0
    winning_trades = sum(1 for t in rt if t.pnl > 0)
    return winning_trades / len(rt)


def calculate_profit_factor(trades: List[Trade]) -> float:
    """Gross profit / gross loss (round-trip trades only)."""
    rt = _round_trip_trades(trades)
    if not rt:
        return 0.0
    gross_profit = sum(t.pnl for t in rt if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in rt if t.pnl < 0))
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_avg_trade_duration(trades: List[Trade]) -> timedelta:
    """Average holding period (round-trip trades only)."""
    rt = _round_trip_trades(trades)
    if not rt:
        return timedelta(0)
    durations = []
    for t in rt:
        d = t.exit_time - t.entry_time
        if not isinstance(d, timedelta):
            d = timedelta(seconds=int(d.total_seconds()) if hasattr(d, 'total_seconds') else int(d))
        durations.append(d)
    total_duration = timedelta(0)
    for d in durations:
        total_duration += d
    return total_duration / len(rt)


def calculate_max_adverse_excursion(trades: List[Trade]) -> float:
    """Maximum adverse excursion - largest peak-to-trough loss during trade.

    .. note::
        This is a stub implementation returning 0.0. The Trade model only tracks
        entry_price and exit_price, so intraday high/low data is unavailable.
        To compute true MAE, the trade model would need bar-level or tick-level
        price data for each position's lifetime.
    """
    return 0.0


def calculate_max_favorable_excursion(trades: List[Trade]) -> float:
    """Maximum favorable excursion - largest peak-to-trough gain during trade.

    .. note::
        This is a stub implementation returning 0.0. The Trade model only tracks
        entry_price and exit_price, so intraday high/low data is unavailable.
        To compute true MFE, the trade model would need bar-level or tick-level
        price data for each position's lifetime.
    """
    return 0.0


def calculate_ulcer_index(equity_curve: pd.Series, periods: int = 14) -> float:
    """Ulcer Index - downside risk measure."""
    if equity_curve.empty:
        return 0.0
    
    running_max = equity_curve.expanding().max()
    drawdown_pct = ((equity_curve - running_max) / running_max) * 100
    drawdown_squared = (drawdown_pct ** 2).rolling(periods).mean()
    return float(np.sqrt(drawdown_squared.iloc[-1])) if not drawdown_squared.empty else 0.0


def calculate_downside_deviation(returns: pd.Series) -> float:
    """Downside deviation (Sortino denominator)."""
    downside_returns = returns[returns < 0]
    if downside_returns.empty:
        return 0.0
    return float(downside_returns.std())


def calculate_gain_to_pain_ratio(trades: List[Trade]) -> float:
    """Sum of gains / absolute value of sum of losses (round-trip only)."""
    rt = _round_trip_trades(trades)
    if not rt:
        return 0.0
    total_gain = sum(t.pnl for t in rt if t.pnl > 0)
    total_loss = abs(sum(t.pnl for t in rt if t.pnl < 0))
    if total_loss == 0:
        return float('inf') if total_gain > 0 else 0.0
    return total_gain / total_loss


def calculate_tail_ratio(returns: pd.Series) -> float:
    """Ratio of 95th percentile / 5th percentile of returns."""
    if len(returns) < 20:
        return 1.0
    percentile_95 = returns.quantile(0.95)
    percentile_5 = returns.quantile(0.05)
    if percentile_5 == 0:
        return float('inf') if percentile_95 > 0 else 1.0
    return abs(percentile_95 / percentile_5)


def calculate_recovery_factor(trades: List[Trade], max_dd: float) -> float:
    """Total profit / max drawdown (round-trip only)."""
    rt = _round_trip_trades(trades)
    if not rt:
        return 0.0
    total_profit = sum(t.pnl for t in rt)
    if max_dd == 0:
        return float('inf') if total_profit > 0 else 0.0
    return total_profit / abs(max_dd)


def calculate_payoff_ratio(trades: List[Trade]) -> float:
    """Average win / average loss (round-trip only)."""
    rt = _round_trip_trades(trades)
    winning_trades = [t.pnl for t in rt if t.pnl > 0]
    losing_trades = [t.pnl for t in rt if t.pnl < 0]

    if not winning_trades or not losing_trades:
        return 0.0

    avg_win = sum(winning_trades) / len(winning_trades)
    avg_loss = abs(sum(losing_trades) / len(losing_trades))

    if avg_loss == 0:
        return float('inf') if avg_win > 0 else 0.0
    return avg_win / avg_loss


def calculate_expectancy(trades: List[Trade]) -> float:
    """Expected value per round-trip trade = win_rate * avg_win - loss_rate * avg_loss."""
    rt = _round_trip_trades(trades)
    if not rt:
        return 0.0

    winning_trades = [t.pnl for t in rt if t.pnl > 0]
    losing_trades = [t.pnl for t in rt if t.pnl < 0]

    win_rate = len(winning_trades) / len(rt)
    loss_rate = len(losing_trades) / len(rt)

    avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0
    avg_loss = abs(sum(losing_trades) / len(losing_trades)) if losing_trades else 0

    return win_rate * avg_win - loss_rate * avg_loss


def calculate_rolling_sharpe(returns: pd.Series, window: int = 63, periods_per_year: int = 252) -> pd.Series:
    if returns.empty or len(returns) < window:
        return pd.Series(dtype=float)
    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std()
    return (rolling_mean / rolling_std) * np.sqrt(periods_per_year)


def calculate_rolling_sortino(returns: pd.Series, window: int = 63, periods_per_year: int = 252) -> pd.Series:
    if returns.empty or len(returns) < window:
        return pd.Series(dtype=float)
    rolling_mean = returns.rolling(window).mean()
    rolling_downside = returns.rolling(window).apply(lambda x: x[x < 0].std() if len(x[x < 0]) > 0 else 0, raw=False)
    return (rolling_mean / rolling_downside.replace(0, np.nan)) * np.sqrt(periods_per_year)


def _erf_approx(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x * x)
    return sign * y


def calculate_statistical_significance(returns: pd.Series, benchmark_returns: pd.Series = None) -> dict:
    if returns.empty:
        return {"t_stat": 0.0, "p_value": 1.0, "is_significant": False, "confidence_interval": (0.0, 0.0)}
    mean_ret = returns.mean()
    std_ret = returns.std()
    n = len(returns)
    if std_ret == 0 or n < 2:
        return {"t_stat": 0.0, "p_value": 1.0, "is_significant": False, "confidence_interval": (mean_ret, mean_ret)}
    se = std_ret / np.sqrt(n)
    t_stat = mean_ret / se
    try:
        from scipy import stats as scipy_stats
        p_value = 2 * (1 - scipy_stats.t.cdf(abs(t_stat), df=n - 1))
    except ImportError:
        z = abs(t_stat)
        p_value = max(0.0, 2.0 * (1.0 - 0.5 * (1.0 + _erf_approx(z / np.sqrt(2.0)))))
    ci_95 = (mean_ret - 1.96 * se, mean_ret + 1.96 * se)
    return {"t_stat": float(t_stat), "p_value": float(p_value), "is_significant": p_value < 0.05, "confidence_interval": ci_95}


@dataclass
class PerformanceMetrics:
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    avg_trade_duration: timedelta
    calmar_ratio: float
    payoff_ratio: float
    expectancy: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    equity_curve: pd.Series
    trades: List[Trade]
    rolling_sharpe: pd.Series
    ulcer_index: float
    gain_to_pain_ratio: float
    tail_ratio: float
    recovery_factor: float
    statistical_significance: dict


def calculate_performance_metrics(
    equity_curve: pd.Series,
    trades: List[Trade]
) -> PerformanceMetrics:
    """Calculate all performance metrics from equity curve and trades."""
    if equity_curve.empty:
        return PerformanceMetrics(
            total_return=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_trade_duration=timedelta(0),
            calmar_ratio=0.0,
            payoff_ratio=0.0,
            expectancy=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            equity_curve=equity_curve,
            trades=trades,
            rolling_sharpe=pd.Series(dtype=float),
            ulcer_index=0.0,
            gain_to_pain_ratio=0.0,
            tail_ratio=1.0,
            recovery_factor=0.0,
            statistical_significance={"t_stat": 0.0, "p_value": 1.0, "is_significant": False, "confidence_interval": (0.0, 0.0)}
        )
    
    returns = equity_curve.pct_change().dropna()
    
    total_return = float((equity_curve.iloc[-1] - equity_curve.iloc[0]) / equity_curve.iloc[0])
    sharpe = calculate_sharpe(returns)
    sortino = calculate_sortino(returns)
    max_dd, max_dd_pct, _, _ = calculate_max_drawdown(equity_curve)
    calmar = calculate_calmar(returns, max_dd)
    
    win_rate = calculate_win_rate(trades)
    profit_factor = calculate_profit_factor(trades)
    payoff = calculate_payoff_ratio(trades)
    expectancy = calculate_expectancy(trades)
    avg_duration = calculate_avg_trade_duration(trades)
    
    winning_trades = len([t for t in trades if t.pnl > 0 and t.side == "SELL"])
    losing_trades = len([t for t in trades if t.pnl < 0 and t.side == "SELL"])
    
    rolling_sharpe = calculate_rolling_sharpe(returns)
    ulcer_idx = calculate_ulcer_index(equity_curve)
    gtp_ratio = calculate_gain_to_pain_ratio(trades)
    tail = calculate_tail_ratio(returns)
    recovery = calculate_recovery_factor(trades, max_dd)
    stat_sig = calculate_statistical_significance(returns)
    
    return PerformanceMetrics(
        total_return=total_return,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_trade_duration=avg_duration,
        calmar_ratio=calmar,
        payoff_ratio=payoff,
        expectancy=expectancy,
        total_trades=len(trades),
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        equity_curve=equity_curve,
        trades=trades,
        rolling_sharpe=rolling_sharpe,
        ulcer_index=ulcer_idx,
        gain_to_pain_ratio=gtp_ratio,
        tail_ratio=tail,
        recovery_factor=recovery,
        statistical_significance=stat_sig
    )
