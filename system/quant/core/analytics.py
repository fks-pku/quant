"""Performance analytics - Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Tuple
import pandas as pd
import numpy as np


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float


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


def calculate_win_rate(trades: List[Trade]) -> float:
    """Percentage of profitable trades."""
    if not trades:
        return 0.0
    winning_trades = sum(1 for t in trades if t.pnl > 0)
    return winning_trades / len(trades)


def calculate_profit_factor(trades: List[Trade]) -> float:
    """Gross profit / gross loss."""
    if not trades:
        return 0.0
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    if gross_loss == 0:
        return float('inf') if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_avg_trade_duration(trades: List[Trade]) -> timedelta:
    """Average holding period."""
    if not trades:
        return timedelta(0)
    durations = [t.exit_time - t.entry_time for t in trades]
    total_duration = timedelta(0)
    for d in durations:
        total_duration += d
    return total_duration / len(trades)


def calculate_max_adverse_excursion(trades: List[Trade]) -> float:
    """Maximum adverse excursion - largest peak-to-trough loss during trade."""
    return 0.0


def calculate_max_favorable_excursion(trades: List[Trade]) -> float:
    """Maximum favorable excursion - largest peak-to-trough gain during trade."""
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
    """Sum of gains / absolute value of sum of losses."""
    if not trades:
        return 0.0
    total_gain = sum(t.pnl for t in trades if t.pnl > 0)
    total_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
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
    """Total profit / max drawdown."""
    if not trades:
        return 0.0
    total_profit = sum(t.pnl for t in trades)
    if max_dd == 0:
        return float('inf') if total_profit > 0 else 0.0
    return total_profit / abs(max_dd)


def calculate_payoff_ratio(trades: List[Trade]) -> float:
    """Average win / average loss."""
    winning_trades = [t.pnl for t in trades if t.pnl > 0]
    losing_trades = [t.pnl for t in trades if t.pnl < 0]
    
    if not winning_trades or not losing_trades:
        return 0.0
    
    avg_win = sum(winning_trades) / len(winning_trades)
    avg_loss = abs(sum(losing_trades) / len(losing_trades))
    
    if avg_loss == 0:
        return float('inf') if avg_win > 0 else 0.0
    return avg_win / avg_loss


def calculate_expectancy(trades: List[Trade]) -> float:
    """Expected value per trade = win_rate * avg_win - loss_rate * avg_loss."""
    if not trades:
        return 0.0
    
    winning_trades = [t.pnl for t in trades if t.pnl > 0]
    losing_trades = [t.pnl for t in trades if t.pnl < 0]
    
    win_rate = len(winning_trades) / len(trades)
    loss_rate = len(losing_trades) / len(trades)
    
    avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0
    avg_loss = abs(sum(losing_trades) / len(losing_trades)) if losing_trades else 0
    
    return win_rate * avg_win - loss_rate * avg_loss


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
            trades=trades
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
    
    winning_trades = len([t for t in trades if t.pnl > 0])
    losing_trades = len([t for t in trades if t.pnl < 0])
    
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
        trades=trades
    )
