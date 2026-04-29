"""Canonical symbol detection — single source of truth for market identification.

Rules:
  CN (A-share): 6-digit numeric, first digit in {0, 3, 6, 8, 9}.
  HK:           HK. prefix OR 1-5 digit numeric (not 6-digit CN).
  US:           Everything else (alphabetic tickers, ETFs, etc.).

Sub-market classification for CN price limits:
  STAR   (688xxx) → 20%
  ChiNext (300xxx) → 20%
  BSE     (8xxxxx) → 30%   (must check AFTER 688 to avoid mis-classification)
  Main board       → 10%
"""

from typing import Literal

Market = Literal["CN", "HK", "US"]

_CN_FIRST_DIGITS = frozenset(("0", "3", "6", "8", "9"))
_CN_STAR_PREFIX = "688"
_CN_CHINEXT_PREFIX = "300"


def is_cn_symbol(symbol: str) -> bool:
    return (
        symbol.isdigit()
        and len(symbol) == 6
        and symbol[0] in _CN_FIRST_DIGITS
    )


def is_hk_symbol(symbol: str) -> bool:
    if symbol.startswith("HK."):
        return True
    return symbol.isdigit() and len(symbol) <= 5


def detect_market(symbol: str) -> Market:
    if is_cn_symbol(symbol):
        return "CN"
    if is_hk_symbol(symbol):
        return "HK"
    return "US"


def cn_price_limit_pct(symbol: str) -> float:
    if symbol.startswith(_CN_STAR_PREFIX):
        return 0.20
    if symbol.startswith(_CN_CHINEXT_PREFIX):
        return 0.20
    if symbol.startswith("8"):
        return 0.30
    return 0.10


def normalize_symbol_for_backtest(symbol: str) -> str:
    if symbol.startswith("HK.") or symbol.startswith("US."):
        return symbol
    if is_cn_symbol(symbol):
        return symbol
    if symbol.isdigit() and len(symbol) <= 5:
        return f"HK.{symbol.zfill(5)}"
    return f"US.{symbol}"
