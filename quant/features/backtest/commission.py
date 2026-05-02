"""Commission calculation per market — CN/HK/US fee breakdowns."""

import math
from typing import Dict, Any

from quant.features.backtest.market_rules import get_market


HK_COMMISSION_RATE = 0.0003
HK_STAMP_DUTY_RATE = 0.001
HK_SFC_LEVY_RATE = 0.0000278
HK_CLEARING_RATE = 0.00002
HK_TRADING_FEE_RATE = 0.00005
HK_MIN_COMMISSION = 3.0
HK_TRADING_SYSTEM_FEE = 0.50

CN_COMMISSION_RATE = 0.00025
CN_STAMP_DUTY_RATE = 0.0005
CN_TRANSFER_FEE_RATE = 0.00001
CN_REGULATOR_FEE_RATE = 0.00002
CN_MIN_COMMISSION = 5.0

US_SEC_FEE_RATE = 0.0000278
US_FINRA_TAF_PER_SHARE = 0.000166

VOLUME_PARTICIPATION_LIMIT = 0.05


def _get_market_config(commission_config: Any, market: str) -> Any:
    if commission_config is None:
        return None
    if hasattr(commission_config, market):
        return getattr(commission_config, market)
    if isinstance(commission_config, dict):
        return commission_config.get(market)
    return None


def calculate_commission(
    symbol: str,
    price: float,
    quantity: float,
    side: str,
    commission_config: Any,
) -> Dict[str, float]:
    trade_value = price * quantity
    market = get_market(symbol)

    if market == "US":
        return _calculate_us_commission(quantity, trade_value, side, commission_config)
    elif market == "CN":
        return _calculate_cn_commission(trade_value, side, commission_config)
    else:
        return _calculate_hk_commission(trade_value, side, commission_config)


def _calculate_us_commission(quantity: float, trade_value: float, side: str, commission_config: Any) -> Dict[str, float]:
    cfg = commission_config.US if hasattr(commission_config, 'US') else commission_config.get("US", {})
    if cfg.get("type") == "per_share":
        commission = quantity * cfg.get("per_share", 0.005)
        commission = max(commission, cfg.get("min_per_order", 1.0))
    else:
        commission = max(trade_value * cfg.get("percent", 0.001), cfg.get("min_per_order", 1.0))
    result: Dict[str, float] = {"commission": commission}
    if side == 'SELL':
        sec_fee = max(trade_value * US_SEC_FEE_RATE, 0.0)
        finra_taf = quantity * US_FINRA_TAF_PER_SHARE
        result["sec_fee"] = sec_fee
        result["finra_taf"] = finra_taf
    return result


def _calculate_cn_commission(trade_value: float, side: str, commission_config: Any = None) -> Dict[str, float]:
    cfg = _get_market_config(commission_config, "CN")
    if cfg and cfg.get("type") == "percent":
        commission = max(trade_value * cfg.get("percent", CN_COMMISSION_RATE), cfg.get("min_per_order", CN_MIN_COMMISSION))
    else:
        commission = max(trade_value * CN_COMMISSION_RATE, CN_MIN_COMMISSION)
    stamp_duty = trade_value * CN_STAMP_DUTY_RATE if side == 'SELL' else 0.0
    transfer_fee = trade_value * CN_TRANSFER_FEE_RATE
    regulator_fee = trade_value * CN_REGULATOR_FEE_RATE
    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "regulator_fee": regulator_fee,
    }


def _calculate_hk_commission(trade_value: float, side: str, commission_config: Any = None) -> Dict[str, float]:
    cfg = _get_market_config(commission_config, "HK")
    if cfg and cfg.get("type") == "percent":
        commission = max(trade_value * cfg.get("percent", HK_COMMISSION_RATE), cfg.get("min_per_order", HK_MIN_COMMISSION))
    else:
        commission = max(trade_value * HK_COMMISSION_RATE, HK_MIN_COMMISSION)
    sfc_levy = trade_value * HK_SFC_LEVY_RATE
    clearing = trade_value * HK_CLEARING_RATE
    trading_fee = trade_value * HK_TRADING_FEE_RATE
    raw_stamp = trade_value * HK_STAMP_DUTY_RATE
    stamp_duty = math.ceil(raw_stamp) if raw_stamp > 0 else 0.0

    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "sfc_levy": sfc_levy,
        "clearing": clearing,
        "trading_fee": trading_fee,
        "system_fee": HK_TRADING_SYSTEM_FEE,
    }
