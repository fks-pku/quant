import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_NAME = "barra_like_cn_style"
FACTOR_NAME_ALIASES = {
    "SMB": "SIZE",
    "HML": "VALUE",
    "BTOP": "VALUE",
    "BOOK_TO_PRICE": "VALUE",
    "Mom": "MOM",
    "MOMENTUM": "MOM",
    "RESVOL": "VOL",
    "RESIDUAL_VOLATILITY": "VOL",
    "VOLATILITY": "VOL",
    "LIQUIDITY": "LIQ",
}


def decompose_alpha(strategy_returns: Any, factor_data: Any, risk_free: float = 0.0) -> Dict[str, float]:
    try:
        returns = _to_series(strategy_returns)
        factors = _to_frame(factor_data)
        if returns is None or factors is None:
            return _zeros()

        joined = pd.concat([returns.rename("strategy_return"), factors], axis=1, join="inner").dropna()
        if len(joined) < 126:
            return _zeros()

        y = joined["strategy_return"].astype(float)
        if "RF" in joined.columns:
            y = y - joined["RF"].astype(float)
        elif risk_free:
            y = y - float(risk_free) / 252.0

        x = joined.drop(columns=["strategy_return", "RF"], errors="ignore").astype(float)
        x = x.loc[:, x.notna().any()]
        if x.shape[1] == 0:
            return _zeros()
        design = np.column_stack([np.ones(len(x)), x.to_numpy()])
        beta, _, _, _ = np.linalg.lstsq(design, y.to_numpy(), rcond=None)
        fitted = design @ beta
        residuals = y.to_numpy() - fitted
        ss_res = float(np.sum(residuals ** 2))
        centered = y.to_numpy() - float(np.mean(y.to_numpy()))
        ss_tot = float(np.sum(centered ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0

        dof = len(y) - design.shape[1]
        tstat = 0.0
        if dof > 0:
            mse = ss_res / dof
            covariance = mse * np.linalg.pinv(design.T @ design)
            alpha_se = math_sqrt(float(covariance[0, 0]))
            if alpha_se > 0.0:
                tstat = float(beta[0] / alpha_se)

        factor_decomposition = _factor_decomposition(
            factor_names=list(x.columns),
            factor_values=x,
            beta=beta,
            covariance=covariance if dof > 0 else None,
            residuals=residuals,
            alpha_daily=float(beta[0]),
            alpha_tstat=float(tstat),
            r2=float(max(0.0, min(1.0, r2))),
            observations=len(y),
        )

        return {
            "alpha_monthly": float(beta[0] * 21.0),
            "tstat": float(tstat),
            "r2": float(max(0.0, min(1.0, r2))),
            "factor_decomposition": factor_decomposition,
        }
    except Exception as e:
        logger.warning(f"FF decomposition failed: {e}")
        return _zeros()


def _to_series(values: Any) -> Any:
    if values is None:
        return None
    if isinstance(values, pd.DataFrame):
        if values.empty:
            return None
        series = values.iloc[:, 0]
    else:
        series = pd.Series(values)
    series.index = pd.to_datetime(series.index)
    return series.dropna().sort_index()


def _to_frame(values: Any) -> Any:
    if values is None:
        return None
    frame = values.copy() if isinstance(values, pd.DataFrame) else pd.DataFrame(values)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date")
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def math_sqrt(value: float) -> float:
    return float(np.sqrt(value)) if value > 0.0 and np.isfinite(value) else 0.0


def _factor_decomposition(
    factor_names: list[str],
    factor_values: pd.DataFrame,
    beta: np.ndarray,
    covariance: Any,
    residuals: np.ndarray,
    alpha_daily: float,
    alpha_tstat: float,
    r2: float,
    observations: int,
) -> Dict[str, Any]:
    factors: Dict[str, Dict[str, float]] = {}
    for idx, name in enumerate(factor_names):
        coef_idx = idx + 1
        beta_value = float(beta[coef_idx])
        se = 0.0
        if covariance is not None:
            se = math_sqrt(float(covariance[coef_idx, coef_idx]))
        tstat = float(beta_value / se) if se > 0.0 else 0.0
        mean_return = float(factor_values[name].mean())
        factors[_canonical_factor_name(name)] = {
            "beta": beta_value,
            "tstat": tstat,
            "mean_return": mean_return,
            "annualized_contribution": float(beta_value * mean_return * 252.0),
        }
    residual_vol = float(np.std(residuals, ddof=1) * np.sqrt(252.0)) if len(residuals) > 1 else 0.0
    alpha_annualized = float(alpha_daily * 252.0)
    residual_sharpe = float(alpha_annualized / residual_vol) if residual_vol > 1e-12 else 0.0
    return {
        "model": MODEL_NAME,
        "status": "computed",
        "observations": int(observations),
        "factor_set": list(factors.keys()),
        "alpha_daily": alpha_daily,
        "alpha_monthly": float(alpha_daily * 21.0),
        "alpha_annualized": alpha_annualized,
        "alpha_tstat": alpha_tstat,
        "r2": r2,
        "residual_vol_annualized": residual_vol,
        "residual_sharpe_annualized": residual_sharpe,
        "factors": factors,
    }


def _canonical_factor_name(name: str) -> str:
    return FACTOR_NAME_ALIASES.get(str(name), str(name))


def _zeros() -> Dict[str, float]:
    return {"alpha_monthly": 0.0, "tstat": 0.0, "r2": 0.0}
