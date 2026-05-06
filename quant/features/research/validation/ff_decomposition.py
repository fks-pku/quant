from typing import Any, Dict


def empty_factor_decomposition() -> Dict[str, float]:
    return {
        "ff_alpha_monthly": 0.0,
        "ff_alpha_tstat": 0.0,
        "ff_r2": 0.0,
    }


def fama_french_decomposition(returns: Any, factors: Any = None) -> Dict[str, float]:
    return empty_factor_decomposition()
