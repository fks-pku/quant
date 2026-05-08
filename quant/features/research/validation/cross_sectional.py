from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd


def detect_market(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if value.endswith((".SS", ".SZ")):
        return "cn"
    if value.startswith("HK."):
        return "hk"
    if value.endswith(".HK"):
        return "hk"
    bare = value.split(".")[0]
    if bare.isdigit() and len(bare) == 5:
        return "hk"
    if bare.isdigit() and len(bare) == 6:
        return "cn"
    return "us"


def compute_cross_sectional_ic(
    signals: pd.DataFrame,
    forward_returns: pd.DataFrame,
    min_stocks: int = 20,
) -> pd.Series:
    common_index = signals.index.intersection(forward_returns.index)
    common_columns = signals.columns.intersection(forward_returns.columns)
    values = []
    dates = []
    for date in common_index:
        paired = pd.concat(
            [
                signals.loc[date, common_columns].rename("signal"),
                forward_returns.loc[date, common_columns].rename("return"),
            ],
            axis=1,
        ).dropna()
        if len(paired) < min_stocks:
            continue
        dates.append(date)
        values.append(float(paired["signal"].corr(paired["return"], method="spearman")))
    return pd.Series(values, index=pd.Index(dates, name=signals.index.name), dtype=float)


def compute_icir(daily_ic: pd.Series) -> float:
    valid = daily_ic.dropna()
    if valid.empty:
        return 0.0
    std = valid.std()
    if pd.isna(std) or std == 0:
        return 0.0
    return float(valid.mean() / std)


def compute_ic_decay(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    horizons: Sequence[int],
    execution_lag: int = 1,
    min_stocks: int = 20,
) -> List[Tuple[int, float]]:
    decay = []
    for horizon in horizons:
        forward_returns = prices.pct_change(horizon).shift(-horizon - execution_lag)
        daily_ic = compute_cross_sectional_ic(signals, forward_returns, min_stocks=min_stocks)
        valid = daily_ic.dropna()
        decay.append((int(horizon), float(valid.mean()) if not valid.empty else 0.0))
    return decay


def compute_fama_macbeth_tstat(
    signals: pd.DataFrame,
    forward_returns: pd.DataFrame,
    min_stocks: int = 20,
) -> float:
    common_index = signals.index.intersection(forward_returns.index)
    common_columns = signals.columns.intersection(forward_returns.columns)
    betas = []
    for date in common_index:
        paired = pd.concat(
            [
                signals.loc[date, common_columns].rename("signal"),
                forward_returns.loc[date, common_columns].rename("return"),
            ],
            axis=1,
        ).dropna()
        if len(paired) < min_stocks:
            continue
        x = paired["signal"].to_numpy(dtype=float)
        y = paired["return"].to_numpy(dtype=float)
        var_x = np.var(x)
        if var_x == 0:
            continue
        betas.append(float(np.cov(x, y, ddof=0)[0, 1] / var_x))
    if len(betas) < 100:
        return 0.0
    beta_series = pd.Series(betas, dtype=float)
    std = beta_series.std()
    if pd.isna(std) or std == 0:
        return 0.0
    se = std / np.sqrt(len(beta_series))
    if se == 0:
        return 0.0
    return float(beta_series.mean() / se)
