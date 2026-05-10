"""Performance analytics - Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from quant.domain.models.trade import Trade


__all__ = ["calculate_sharpe", "calculate_sortino", "calculate_max_drawdown",
           "calculate_performance_metrics", "PerformanceMetrics",
           "calculate_rolling_sharpe",
           "calculate_statistical_significance",
           "calculate_alpha", "calculate_beta",
           "calculate_information_ratio", "calculate_tracking_error",
           "calculate_up_down_capture",
           "calculate_calmar", "calculate_win_rate", "calculate_profit_factor",
           "calculate_avg_trade_duration", "calculate_ulcer_index",
           "calculate_gain_to_pain_ratio", "calculate_tail_ratio",
           "calculate_recovery_factor", "calculate_payoff_ratio",
           "calculate_expectancy", "MAX_PROFIT_FACTOR"]


def calculate_sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio."""
    if returns.empty:
        return 0.0
    std = returns.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return np.sqrt(periods_per_year) * returns.mean() / std


def calculate_sortino(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized Sortino ratio (downside deviation)."""
    if returns.empty:
        return 0.0

    downside_sq = np.minimum(returns, 0) ** 2
    downside_dev = np.sqrt(downside_sq.mean())
    if downside_dev == 0:
        return float('inf') if returns.mean() > 0 else 0.0

    return np.sqrt(periods_per_year) * returns.mean() / downside_dev


def calculate_max_drawdown(equity_curve: pd.Series) -> Tuple[float, float, datetime, datetime]:
    """
    Returns (drawdown_value, drawdown_pct, peak_date, trough_date).
    """
    if equity_curve.empty:
        return 0.0, 0.0, datetime.now(), datetime.now()
    
    running_max = equity_curve.expanding().max()
    drawdown = equity_curve - running_max
    drawdown_pct = drawdown / running_max.replace(0, np.nan)
    
    trough_pos = int(drawdown.values.argmin())
    if pd.isna(drawdown.iloc[trough_pos]):
        return 0.0, 0.0, datetime.now(), datetime.now()
    
    trough_date = equity_curve.index[trough_pos]
    
    if trough_pos > 0:
        peak_pos = int(equity_curve.iloc[:trough_pos].values.argmax())
        peak_date = equity_curve.index[peak_pos]
    else:
        peak_date = equity_curve.index[0]
    
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


def _round_trip_pnls(trades: List[Trade]) -> List[float]:
    entry_commission_lots: Dict[Tuple[Optional[str], str], List[List[float]]] = {}
    pnls: List[float] = []

    ordered = sorted(
        trades,
        key=lambda t: (
            t.fill_date or t.exit_time or t.entry_time,
            0 if t.side == "BUY" else 1,
        ),
    )
    for trade in ordered:
        key = (trade.strategy_name, trade.symbol)
        if trade.side == "BUY":
            if trade.quantity > 0 and trade.commission > 0:
                entry_commission_lots.setdefault(key, []).append([
                    float(trade.quantity),
                    float(trade.commission) / float(trade.quantity),
                ])
            continue
        if trade.side != "SELL":
            continue

        remaining = float(trade.quantity)
        entry_commission = 0.0
        lots = entry_commission_lots.get(key, [])
        while remaining > 1e-12 and lots:
            lot_qty, commission_per_share = lots[0]
            take = min(lot_qty, remaining)
            entry_commission += take * commission_per_share
            lot_qty -= take
            remaining -= take
            if lot_qty <= 1e-12:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        pnls.append(float(trade.pnl) - entry_commission)

    return pnls


def calculate_win_rate(trades: List[Trade]) -> float:
    """Percentage of profitable round-trip trades."""
    pnls = _round_trip_pnls(trades)
    if not pnls:
        return 0.0
    winning_trades = sum(1 for pnl in pnls if pnl > 0)
    return winning_trades / len(pnls)


def _gross_profit_loss(trades: List[Trade]) -> Tuple[float, float]:
    pnls = _round_trip_pnls(trades)
    if not pnls:
        return 0.0, 0.0
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    return gross_profit, gross_loss


MAX_PROFIT_FACTOR = 9999.0


def calculate_profit_factor(trades: List[Trade]) -> float:
    """Gross profit / gross loss (round-trip SELL trades only)."""
    gross_profit, gross_loss = _gross_profit_loss(trades)
    if gross_loss == 0:
        return MAX_PROFIT_FACTOR if gross_profit > 0 else 0.0
    return min(gross_profit / gross_loss, MAX_PROFIT_FACTOR)


def calculate_avg_trade_duration(trades: List[Trade]) -> timedelta:
    """Average holding period (round-trip trades only)."""
    rt = _round_trip_trades(trades)
    if not rt:
        return timedelta(0)
    durations = []
    for t in rt:
        d = t.exit_time - t.entry_time
        if isinstance(d, timedelta):
            durations.append(d)
        else:
            seconds = float(d.total_seconds()) if hasattr(d, 'total_seconds') else 0.0
            durations.append(timedelta(seconds=seconds))
    if not durations:
        return timedelta(0)
    total_seconds = sum(d.total_seconds() for d in durations)
    return timedelta(seconds=total_seconds / len(durations))


def calculate_ulcer_index(equity_curve: pd.Series, periods: int = 14) -> float:
    """Ulcer Index - downside risk measure."""
    if equity_curve.empty or len(equity_curve) < periods:
        return 0.0
    
    running_max = equity_curve.expanding().max()
    drawdown_pct = ((equity_curve - running_max) / running_max) * 100
    drawdown_squared = (drawdown_pct ** 2).rolling(periods).mean()
    val = drawdown_squared.iloc[-1]
    if pd.isna(val):
        return 0.0
    return float(np.sqrt(val))


def calculate_gain_to_pain_ratio(trades: List[Trade]) -> float:
    """Sum of gains / absolute value of sum of losses (round-trip trades only)."""
    total_gain, total_loss = _gross_profit_loss(trades)
    if total_loss == 0:
        return MAX_PROFIT_FACTOR if total_gain > 0 else 0.0
    return total_gain / total_loss


def calculate_tail_ratio(returns: pd.Series) -> float:
    """Ratio of 95th percentile / 5th percentile of returns."""
    if len(returns) < 20:
        return 1.0
    percentile_95 = returns.quantile(0.95)
    percentile_5 = returns.quantile(0.05)
    if percentile_5 == 0:
        return 1.0
    return abs(percentile_95 / percentile_5)


def calculate_recovery_factor(trades: List[Trade], max_dd: float) -> float:
    """Total profit / max drawdown (round-trip trades only)."""
    pnls = _round_trip_pnls(trades)
    if not pnls:
        return 0.0
    total_profit = sum(pnls)
    if max_dd == 0:
        return 0.0
    return total_profit / abs(max_dd)


def calculate_payoff_ratio(trades: List[Trade]) -> float:
    """Average win / average loss (round-trip only)."""
    pnls = _round_trip_pnls(trades)
    winning_trades = [pnl for pnl in pnls if pnl > 0]
    losing_trades = [pnl for pnl in pnls if pnl < 0]

    if not winning_trades or not losing_trades:
        return 0.0

    avg_win = sum(winning_trades) / len(winning_trades)
    avg_loss = abs(sum(losing_trades) / len(losing_trades))

    if avg_loss == 0:
        return 0.0
    return avg_win / avg_loss


def calculate_expectancy(trades: List[Trade]) -> float:
    """Expected value per round-trip trade = win_rate * avg_win - loss_rate * avg_loss."""
    pnls = _round_trip_pnls(trades)
    if not pnls:
        return 0.0

    winning_trades = [pnl for pnl in pnls if pnl > 0]
    losing_trades = [pnl for pnl in pnls if pnl < 0]

    win_rate = len(winning_trades) / len(pnls)
    loss_rate = len(losing_trades) / len(pnls)

    avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0
    avg_loss = abs(sum(losing_trades) / len(losing_trades)) if losing_trades else 0

    return win_rate * avg_win - loss_rate * avg_loss


def calculate_rolling_sharpe(returns: pd.Series, window: int = 63, periods_per_year: int = 252) -> pd.Series:
    if returns.empty or len(returns) < window:
        return pd.Series(dtype=float)
    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std()
    result = (rolling_mean / rolling_std.replace(0, np.nan)) * np.sqrt(periods_per_year)
    return result.fillna(0.0)


def _align_returns(returns: pd.Series, benchmark_returns: pd.Series) -> Tuple[pd.Series, pd.Series]:
    aligned = pd.concat([returns, benchmark_returns], axis=1).dropna()
    return aligned.iloc[:, 0], aligned.iloc[:, 1]


def _erf_approx(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x * x)
    return sign * y


def calculate_statistical_significance(returns: pd.Series, benchmark_returns: Optional[pd.Series] = None) -> dict:
    if returns.empty:
        return {"t_stat": 0.0, "p_value": 1.0, "is_significant": False, "confidence_interval": (0.0, 0.0)}

    if benchmark_returns is not None and not benchmark_returns.empty:
        strat_aligned, bench_aligned = _align_returns(returns, benchmark_returns)
        if strat_aligned.empty or len(strat_aligned) < 2:
            return {"t_stat": 0.0, "p_value": 1.0, "is_significant": False, "confidence_interval": (0.0, 0.0)}
        excess = strat_aligned - bench_aligned
        mean_ret = excess.mean()
        std_ret = excess.std()
        n = len(excess)
    else:
        mean_ret = returns.mean()
        std_ret = returns.std()
        n = len(returns)

    if std_ret == 0 or n < 2:
        return {"t_stat": 0.0, "p_value": 1.0, "is_significant": False, "confidence_interval": (mean_ret, mean_ret)}
    se = std_ret / np.sqrt(n)
    t_stat = mean_ret / se
    try:
        from scipy import stats as scipy_stats
        if benchmark_returns is not None and not benchmark_returns.empty:
            p_value = 1.0 - scipy_stats.t.cdf(t_stat, df=n - 1)
        else:
            p_value = 2.0 * (1.0 - scipy_stats.t.cdf(abs(t_stat), df=n - 1))
    except ImportError:
        z = abs(t_stat)
        if benchmark_returns is not None and not benchmark_returns.empty:
            p_value = max(0.0, 1.0 - 0.5 * (1.0 + _erf_approx(z / np.sqrt(2.0))))
        else:
            p_value = max(0.0, 2.0 * (1.0 - 0.5 * (1.0 + _erf_approx(z / np.sqrt(2.0)))))
    ci_95 = (mean_ret - 1.96 * se, mean_ret + 1.96 * se)
    return {"t_stat": float(t_stat), "p_value": float(p_value), "is_significant": p_value < 0.05, "confidence_interval": ci_95}


def calculate_alpha(returns: pd.Series, benchmark_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized alpha: strategy annual return - benchmark annual return."""
    if returns.empty or benchmark_returns.empty:
        return 0.0
    strat_aligned, bench_aligned = _align_returns(returns, benchmark_returns)
    if strat_aligned.empty:
        return 0.0
    strat_annual = strat_aligned.mean() * periods_per_year
    bench_annual = bench_aligned.mean() * periods_per_year
    return float(strat_annual - bench_annual)


def calculate_beta(returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Simple OLS beta: Cov(strategy, benchmark) / Var(benchmark)."""
    if returns.empty or benchmark_returns.empty:
        return 0.0
    strat_aligned, bench_aligned = _align_returns(returns, benchmark_returns)
    if strat_aligned.empty or len(strat_aligned) < 2:
        return 0.0
    x = bench_aligned
    y = strat_aligned
    var_x = x.var()
    if var_x == 0:
        return 0.0
    return float(x.cov(y) / var_x)


def calculate_tracking_error(returns: pd.Series, benchmark_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized tracking error: std of excess returns."""
    if returns.empty or benchmark_returns.empty:
        return 0.0
    strat_aligned, bench_aligned = _align_returns(returns, benchmark_returns)
    if strat_aligned.empty:
        return 0.0
    excess = strat_aligned - bench_aligned
    return float(excess.std() * np.sqrt(periods_per_year))


def calculate_information_ratio(returns: pd.Series, benchmark_returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized information ratio: alpha / tracking error."""
    alpha = calculate_alpha(returns, benchmark_returns, periods_per_year)
    te = calculate_tracking_error(returns, benchmark_returns, periods_per_year)
    if te == 0:
        return 0.
    return alpha / te


def calculate_up_down_capture(returns: pd.Series, benchmark_returns: pd.Series) -> Tuple[float, float]:
    """Up-market and down-market capture ratios.

    Up capture = mean(strategy return in up benchmarks) / mean(benchmark return in up periods)
    Down capture = mean(strategy return in down benchmarks) / mean(benchmark return in down periods)
    """
    if returns.empty or benchmark_returns.empty:
        return 1.0, 1.0
    strat, bench = _align_returns(returns, benchmark_returns)
    if strat.empty:
        return 1.0, 1.0

    up_mask = bench > 0
    down_mask = bench < 0

    if up_mask.sum() == 0 or bench[up_mask].mean() == 0:
        up_capture = 1.0
    else:
        up_capture = float(strat[up_mask].mean() / bench[up_mask].mean())

    if down_mask.sum() == 0 or bench[down_mask].mean() == 0:
        down_capture = 1.0
    else:
        down_capture = float(strat[down_mask].mean() / bench[down_mask].mean())

    return up_capture, down_capture


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
    benchmark_return: Optional[float] = None
    alpha: Optional[float] = None
    beta: Optional[float] = None
    information_ratio: Optional[float] = None
    tracking_error: Optional[float] = None
    up_capture: Optional[float] = None
    down_capture: Optional[float] = None


def calculate_performance_metrics(
    equity_curve: pd.Series,
    trades: List[Trade],
    initial_cash: Optional[float] = None,
    benchmark_returns: Optional[pd.Series] = None,
) -> PerformanceMetrics:
    """Calculate all performance metrics from equity curve and trades.

    If benchmark_returns is provided, also compute benchmark comparison
    metrics (alpha, beta, information ratio, tracking error, up/down capture).
    """
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

    base = initial_cash if initial_cash is not None and initial_cash > 0 else equity_curve.iloc[0]
    total_return = float((equity_curve.iloc[-1] - base) / base)
    sharpe = calculate_sharpe(returns)
    sortino = calculate_sortino(returns)
    max_dd, max_dd_pct, _, _ = calculate_max_drawdown(equity_curve)
    calmar = calculate_calmar(returns, max_dd_pct)

    win_rate = calculate_win_rate(trades)
    profit_factor = calculate_profit_factor(trades)
    payoff = calculate_payoff_ratio(trades)
    expectancy = calculate_expectancy(trades)
    avg_duration = calculate_avg_trade_duration(trades)

    round_trip_pnls = _round_trip_pnls(trades)
    winning_trades = len([pnl for pnl in round_trip_pnls if pnl > 0])
    losing_trades = len([pnl for pnl in round_trip_pnls if pnl <= 0])

    rolling_sharpe = calculate_rolling_sharpe(returns)
    ulcer_idx = calculate_ulcer_index(equity_curve)
    gtp_ratio = calculate_gain_to_pain_ratio(trades)
    tail = calculate_tail_ratio(returns)
    recovery = calculate_recovery_factor(trades, max_dd)
    stat_sig = calculate_statistical_significance(returns, benchmark_returns)

    bench_return = None
    alpha = None
    beta = None
    ir_val = None
    te = None
    up_cap = None
    down_cap = None

    if benchmark_returns is not None and not benchmark_returns.empty:
        bench_return = float(benchmark_returns.mean() * 252)
        alpha = calculate_alpha(returns, benchmark_returns)
        beta = calculate_beta(returns, benchmark_returns)
        ir_val = calculate_information_ratio(returns, benchmark_returns)
        te = calculate_tracking_error(returns, benchmark_returns)
        up_cap, down_cap = calculate_up_down_capture(returns, benchmark_returns)

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
        total_trades=len(_round_trip_trades(trades)),
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        equity_curve=equity_curve,
        trades=trades,
        rolling_sharpe=rolling_sharpe,
        ulcer_index=ulcer_idx,
        gain_to_pain_ratio=gtp_ratio,
        tail_ratio=tail,
        recovery_factor=recovery,
        statistical_significance=stat_sig,
        benchmark_return=bench_return,
        alpha=alpha,
        beta=beta,
        information_ratio=ir_val,
        tracking_error=te,
        up_capture=up_cap,
        down_capture=down_cap,
    )
