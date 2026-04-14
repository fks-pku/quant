"""Unified symbol format across providers and brokers."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class Market(Enum):
    US = "US"
    HK = "HK"


@dataclass
class SymbolInfo:
    internal: str
    market: Market
    provider_formats: Dict[str, str]


class SymbolRegistry:
    """
    Unified symbol format across providers and brokers.
    
    Internal format (canonical):
    - US: "AAPL", "MSFT", "SPY" (no prefix)
    - HK: "00700", "00005", "HSI" (stock codes without prefix)
    
    Provider-specific formats:
    - Futu US: "US.AAPL"
    - Futu HK: "HK.00700"
    - Yahoo: "AAPL" (standard)
    """

    def __init__(self):
        self._symbols: Dict[str, SymbolInfo] = {}
        self._us_tradable = {
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM", "V", "WMT",
            "SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE", "XLV", "TLT", "AGG",
            "GLD", "SLV", "USO", "UNG", "Corn", " Wheat",
        }
        self._hk_tradable = {
            "00700", "00005", "00006", "00011", "00012", "00016", "00017", "00019",
            "02318", "02319", "02628", "02888", "02899", "06688", "06888", "09688",
            "HSI", "HHI", "MCH",
        }

    def format_for_provider(self, symbol: str, provider: str) -> str:
        """Convert internal symbol to provider-specific format."""
        market = self.detect_market(symbol)
        
        if provider.lower() == "futu":
            if market == Market.US:
                return f"US.{symbol}"
            elif market == Market.HK:
                return f"HK.{symbol}"
        elif provider.lower() == "yahoo":
            return symbol
        elif provider.lower() == "alpha_vantage":
            return symbol
        
        return symbol

    def format_for_broker(self, symbol: str, broker: str) -> str:
        """Convert internal symbol to broker-specific format."""
        return self.format_for_provider(symbol, broker)

    def detect_market(self, symbol: str) -> Market:
        """Detect market from internal symbol format."""
        if symbol.isdigit() and len(symbol) >= 5:
            return Market.HK
        if symbol.startswith(("US.", "HK.", "SPY", "QQQ", "IWM", "DIA")):
            if symbol.startswith("HK."):
                return Market.HK
            return Market.US
        if symbol in self._us_tradable:
            return Market.US
        if symbol in self._hk_tradable:
            return Market.HK
        if symbol.startswith(("ES", "NQ", "CL", "GC", "SI", "NG")):
            return Market.US
        
        return Market.US

    def normalize(self, external_symbol: str, source: str) -> str:
        """Normalize external symbol to internal format."""
        if source.lower() == "futu":
            if external_symbol.startswith("US."):
                return external_symbol[3:]
            elif external_symbol.startswith("HK."):
                return external_symbol[3:]
        elif source.lower() == "yahoo":
            return external_symbol
        elif source.lower() == "alpha_vantage":
            return external_symbol
        
        return external_symbol

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """Get symbol information."""
        return self._symbols.get(symbol)

    def register_symbol(self, symbol: str, market: Market) -> None:
        """Register a symbol with its market."""
        self._symbols[symbol] = SymbolInfo(
            internal=symbol,
            market=market,
            provider_formats={}
        )

    def is_tradable(self, symbol: str, market: Market) -> bool:
        """Check if symbol is tradable in the given market."""
        if market == Market.US:
            return symbol in self._us_tradable
        elif market == Market.HK:
            return symbol in self._hk_tradable
        return False

    def get_market_prefix(self, market: Market) -> str:
        """Get market prefix for broker formatting."""
        return market.value

    def resolve_symbol(self, symbol: str, market: Optional[Market] = None) -> str:
        """
        Resolve symbol to internal format.
        If market is provided, validates symbol belongs to that market.
        """
        if market:
            detected = self.detect_market(symbol)
            if detected != market:
                return symbol
        return self.normalize(symbol, "unknown")
