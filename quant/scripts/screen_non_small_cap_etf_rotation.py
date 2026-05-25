"""Screen non-small-cap ETF rotation rules before strict Backtester runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import duckdb
import numpy as np
import pandas as pd


DB_PATH = Path("quant/infrastructure/var/duckdb/live/cn_etf_ohlcv.duckdb")
START = "2016-01-01"
END = "2025-12-31"


CORE_SYMBOLS = [
    "510050",
    "510300",
    "510500",
    "512100",
    "159915",
    "159949",
    "510880",
    "512880",
    "512000",
    "512480",
    "512690",
    "512800",
    "512660",
    "518880",
    "513100",
    "513050",
    "513030",
    "159920",
    "510900",
]

NON_SMALL_ETF_SYMBOLS = [
    "510050",
    "510300",
    "159915",
    "159949",
    "510880",
    "512880",
    "512000",
    "512480",
    "518880",
    "513100",
    "513050",
    "159920",
    "510900",
]


@dataclass(frozen=True)
class Scenario:
    lookback: int
    skip: int
    trend: int
    vol: int
    top_n: int
    min_momentum: float
    score_mode: str
    rebalance_days: int
    target_exposure: float
    universe: str


def main() -> None:
    prices = _load_prices(CORE_SYMBOLS)
    scenarios = list(_scenarios())
    score_cache: Dict[Tuple[str, int], pd.DataFrame] = {}
    rows = []
    for scenario in scenarios:
        daily = _simulate(prices, scenario, score_cache)
        rows.append({"scenario": scenario, **_metrics(daily)})
    result = pd.DataFrame(rows).sort_values(["meets", "cagr", "max_drawdown"], ascending=[False, False, False])
    print(result.head(30).to_string(index=False))


def _load_prices(symbols: List[str]) -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        data = con.execute(
            """
            SELECT timestamp::DATE AS date, symbol, adj_close
            FROM daily_cn_ochl
            WHERE timestamp BETWEEN ? AND ?
              AND symbol IN (SELECT UNNEST(?))
            ORDER BY date, symbol
            """,
            [START, END, symbols],
        ).fetch_df()
    finally:
        con.close()
    prices = data.pivot(index="date", columns="symbol", values="adj_close").sort_index()
    return prices.dropna(axis=1, thresh=int(len(prices) * 0.80)).ffill()


def _scenarios() -> Iterable[Scenario]:
    universes = {
        "core": CORE_SYMBOLS[:10],
        "risk_gold_global": CORE_SYMBOLS,
        "non_small_etf": NON_SMALL_ETF_SYMBOLS,
    }
    for universe in universes:
        for lookback in (42, 63, 126):
            for trend in (60, 120):
                for vol in (20, 60):
                    for top_n in (1, 2, 3):
                        for min_momentum in (0.0, 0.03):
                            for score_mode in ("mom", "mom_vol"):
                                for rebalance_days in (5, 20):
                                    yield Scenario(
                                        lookback=lookback,
                                        skip=1,
                                        trend=trend,
                                        vol=vol,
                                        top_n=top_n,
                                        min_momentum=min_momentum,
                                        score_mode=score_mode,
                                        rebalance_days=rebalance_days,
                                        target_exposure=0.98,
                                        universe=universe,
                                    )


def _simulate(
    prices: pd.DataFrame,
    scenario: Scenario,
    score_cache: Dict[Tuple[str, int], pd.DataFrame],
) -> pd.Series:
    if scenario.universe == "core":
        universe_symbols = CORE_SYMBOLS[:10]
    elif scenario.universe == "non_small_etf":
        universe_symbols = NON_SMALL_ETF_SYMBOLS
    else:
        universe_symbols = CORE_SYMBOLS
    symbols = [symbol for symbol in universe_symbols if symbol in prices.columns]
    px = prices[symbols].copy()
    returns = px.pct_change().fillna(0.0)
    momentum = px.shift(scenario.skip) / px.shift(scenario.lookback + scenario.skip) - 1.0
    trend = px / px.rolling(scenario.trend).mean() - 1.0
    vol = returns.rolling(scenario.vol).std() * np.sqrt(252.0)
    if scenario.score_mode == "mom_vol":
        score = momentum / vol.replace(0.0, np.nan)
    elif scenario.score_mode == "slope_r2":
        cache_key = (scenario.universe, scenario.lookback)
        if cache_key not in score_cache:
            score_cache[cache_key] = _slope_r2_score(px, scenario.lookback)
        score = score_cache[cache_key]
    else:
        score = momentum
    valid = (momentum >= scenario.min_momentum) & (trend > 0.0) & score.replace([np.inf, -np.inf], np.nan).notna()
    weights = np.zeros((len(px.index), len(px.columns)), dtype=float)
    last_weights = np.zeros(len(px.columns), dtype=float)
    score_values = score.to_numpy(dtype=float)
    valid_values = valid.to_numpy(dtype=bool)
    for index, current_date in enumerate(px.index):
        if index < max(scenario.lookback + scenario.skip, scenario.trend, scenario.vol):
            weights[index] = last_weights
            continue
        if index % scenario.rebalance_days != 0:
            weights[index] = last_weights
            continue
        today_scores = np.where(valid_values[index], score_values[index], np.nan)
        valid_indices = np.where(np.isfinite(today_scores))[0]
        if len(valid_indices) == 0:
            last_weights = np.zeros(len(px.columns), dtype=float)
        else:
            ranked = valid_indices[np.argsort(today_scores[valid_indices])[::-1]]
            selected = ranked[: scenario.top_n]
            last_weights = np.zeros(len(px.columns), dtype=float)
            last_weights[selected] = scenario.target_exposure / len(selected)
        weights[index] = last_weights
    shifted = np.vstack([np.zeros((1, weights.shape[1])), weights[:-1]])
    turnover = np.abs(np.diff(shifted, axis=0, prepend=np.zeros((1, shifted.shape[1])))).sum(axis=1)
    daily = (shifted * returns.to_numpy(dtype=float)).sum(axis=1) - turnover * 0.0008
    return pd.Series(daily, index=px.index)


def _slope_r2_score(prices: pd.DataFrame, window: int) -> pd.DataFrame:
    log_prices = np.log(prices.replace(0.0, np.nan))
    x = np.arange(window, dtype=float)
    x = x - x.mean()
    denominator = float((x * x).sum())
    values: Dict[str, pd.Series] = {}
    for symbol in log_prices.columns:
        y = log_prices[symbol]
        slopes = y.rolling(window).apply(lambda arr: float(np.dot(x, arr - arr.mean()) / denominator), raw=True)
        fitted_quality = y.rolling(window).apply(lambda arr: _r2(arr, x), raw=True)
        values[symbol] = (np.exp(slopes * 252.0) - 1.0) * fitted_quality
    return pd.DataFrame(values, index=prices.index)


def _r2(arr: np.ndarray, x: np.ndarray) -> float:
    centered = arr - arr.mean()
    denominator = float((x * x).sum())
    if denominator <= 0:
        return 0.0
    slope = float(np.dot(x, centered) / denominator)
    fitted = slope * x + arr.mean()
    ss_tot = float(((arr - arr.mean()) ** 2).sum())
    if ss_tot <= 0:
        return 0.0
    ss_res = float(((arr - fitted) ** 2).sum())
    return max(0.0, min(1.0, 1.0 - ss_res / ss_tot))


def _metrics(daily_returns: pd.Series) -> Dict[str, float | bool]:
    equity = (1.0 + daily_returns).cumprod()
    years = len(daily_returns) / 252.0
    total_return = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0)
    vol = float(daily_returns.std() * np.sqrt(252.0))
    sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252.0)) if daily_returns.std() > 0 else 0.0
    max_drawdown = float((equity / equity.cummax() - 1.0).min())
    return {
        "cagr": cagr,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "vol": vol,
        "sharpe": sharpe,
        "meets": bool(cagr > 0.10 and max_drawdown > -0.30),
    }


if __name__ == "__main__":
    main()
