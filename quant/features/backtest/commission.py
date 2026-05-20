"""Commission calculation per market — CN/HK/US fee breakdowns."""

import math
import datetime as dt
from typing import Dict, Any, Optional

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
CN_FUND_COMMISSION_RATE = CN_COMMISSION_RATE
CN_FUND_MIN_COMMISSION = CN_MIN_COMMISSION

US_SEC_FEE_RATE = 0.0000278
US_FINRA_TAF_PER_SHARE = 0.000166

# CN stamp duty: 0.1% before 2023-08-28, halved to 0.05% after
# Source: China Ministry of Finance
CN_STAMP_DUTY_RATE_HISTORY = {
    dt.date(1990, 1, 1): 0.001,     # 0.1% (historical baseline)
    dt.date(2023, 8, 28): 0.0005,   # halved to 0.05%
}

# HK stamp duty: 0.1% before 2021-08-01, raised to 0.13% until 2023-11-16, back to 0.1%
# Source: HK Inland Revenue (Amendment) (Stock Transfers) Order
HK_STAMP_DUTY_RATE_HISTORY = {
    dt.date(1990, 1, 1): 0.001,     # 0.1% (historical baseline)
    dt.date(2021, 8, 1): 0.0013,    # raised to 0.13%
    dt.date(2023, 11, 17): 0.001,   # reduced back to 0.1%
}

VOLUME_PARTICIPATION_LIMIT = 0.05

CN_EXCHANGE_TRADED_FUND_PREFIXES = (
    "15",
    "16",
    "18",
    "50",
    "51",
    "52",
    "56",
    "58",
)


def get_rate_for_date(
    rate_table: Dict[dt.date, float],
    trade_date: Optional[dt.date],
    default_rate: float,
) -> float:
    """Look up the applicable rate for a given trade date.

    Args:
        rate_table: dict of {date: rate} where each key is the effective date
                    of a rate change and the value is the rate from that date onward.
        trade_date: the trade date to look up. If None, returns the most recent rate.
        default_rate: fallback if rate_table is empty.

    Returns:
        The applicable rate as a float.
    """
    if not rate_table:
        return default_rate
    if trade_date is None:
        return rate_table[max(rate_table.keys())]
    applicable = default_rate
    for d in sorted(rate_table.keys()):
        if d <= trade_date:
            applicable = rate_table[d]
        else:
            break
    return applicable


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
    trade_date: Optional[dt.date] = None,
) -> Dict[str, float]:
    trade_value = price * quantity
    market = get_market(symbol)

    if market == "US":
        return _calculate_us_commission(quantity, trade_value, side, commission_config)
    elif market == "CN":
        if _is_cn_exchange_traded_fund_symbol(symbol):
            return _calculate_cn_fund_commission(trade_value, side, commission_config)
        return _calculate_cn_commission(trade_value, side, commission_config, trade_date)
    else:
        return _calculate_hk_commission(trade_value, side, commission_config, trade_date)


def _is_cn_exchange_traded_fund_symbol(symbol: str) -> bool:
    code = str(symbol).strip()
    return code.isdigit() and len(code) == 6 and code.startswith(CN_EXCHANGE_TRADED_FUND_PREFIXES)


def _calculate_us_commission(quantity: float, trade_value: float, side: str, commission_config: Any) -> Dict[str, float]:
    cfg = _get_market_config(commission_config, "US") or {}
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


def _calculate_cn_commission(
    trade_value: float,
    side: str,
    commission_config: Any = None,
    trade_date: Optional[dt.date] = None,
) -> Dict[str, float]:
    cfg = _get_market_config(commission_config, "CN")
    if cfg and cfg.get("type") == "percent":
        commission = max(trade_value * cfg.get("percent", CN_COMMISSION_RATE), cfg.get("min_per_order", CN_MIN_COMMISSION))
    else:
        commission = max(trade_value * CN_COMMISSION_RATE, CN_MIN_COMMISSION)
    stamp_rate = get_rate_for_date(CN_STAMP_DUTY_RATE_HISTORY, trade_date, 0.001)
    stamp_duty = trade_value * stamp_rate if side == 'SELL' else 0.0
    transfer_fee = trade_value * CN_TRANSFER_FEE_RATE
    regulator_fee = trade_value * CN_REGULATOR_FEE_RATE
    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "regulator_fee": regulator_fee,
    }


def _calculate_cn_fund_commission(
    trade_value: float,
    side: str,
    commission_config: Any = None,
) -> Dict[str, float]:
    cfg = _get_market_config(commission_config, "CN") or {}
    fund_rate = cfg.get("fund_percent")
    if fund_rate is None:
        fund_rate = cfg.get("percent", CN_FUND_COMMISSION_RATE) if cfg.get("type") == "percent" else CN_FUND_COMMISSION_RATE
    fund_min = cfg.get("fund_min_per_order")
    if fund_min is None:
        fund_min = cfg.get("min_per_order", CN_FUND_MIN_COMMISSION)
    commission = max(trade_value * float(fund_rate), float(fund_min))
    return {
        "commission": commission,
        "stamp_duty": 0.0,
        "transfer_fee": 0.0,
        "regulator_fee": 0.0,
    }


def _calculate_hk_commission(
    trade_value: float,
    side: str,
    commission_config: Any = None,
    trade_date: Optional[dt.date] = None,
) -> Dict[str, float]:
    cfg = _get_market_config(commission_config, "HK")
    if cfg and cfg.get("type") == "percent":
        commission = max(trade_value * cfg.get("percent", HK_COMMISSION_RATE), cfg.get("min_per_order", HK_MIN_COMMISSION))
    else:
        commission = max(trade_value * HK_COMMISSION_RATE, HK_MIN_COMMISSION)
    sfc_levy = trade_value * HK_SFC_LEVY_RATE
    clearing = trade_value * HK_CLEARING_RATE
    trading_fee = trade_value * HK_TRADING_FEE_RATE
    stamp_rate = get_rate_for_date(HK_STAMP_DUTY_RATE_HISTORY, trade_date, 0.001)
    raw_stamp = trade_value * stamp_rate
    stamp_duty = math.ceil(raw_stamp) if raw_stamp > 0 else 0.0

    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "sfc_levy": sfc_levy,
        "clearing": clearing,
        "trading_fee": trading_fee,
        "system_fee": HK_TRADING_SYSTEM_FEE,
    }
