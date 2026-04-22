"""Unified symbol format across providers and brokers."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class Market(Enum):
    US = "US"
    HK = "HK"
    CN = "CN"


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
    - CN: "600519", "000001" (6-digit A-share codes without prefix)
    
    Provider-specific formats:
    - Futu US: "US.AAPL"
    - Futu HK: "HK.00700"
    - Yahoo: "AAPL" (standard)
    - Akshare: "600519" (standard)
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
        self._cn_tradable = {
            "600519", "000001", "000858", "002415", "300750", "601318",
            "600036", "000333", "002594", "600276", "601888", "600030",
        }

    def _is_cn_symbol(self, symbol: str) -> bool:
        """Detect A-share symbol: 6-digit numeric starting with 0/3/6/8/9."""
        return (
            symbol.isdigit()
            and len(symbol) == 6
            and symbol[0] in ("0", "3", "6", "8", "9")
        )

    def _is_hk_symbol(self, symbol: str) -> bool:
        """Detect HK symbol: 5-digit numeric, or prefixed with HK."""
        if symbol.startswith("HK."):
            return True
        return symbol.isdigit() and len(symbol) == 5

    def format_for_provider(self, symbol: str, provider: str) -> str:
        """Convert internal symbol to provider-specific format."""
        market = self.detect_market(symbol)
        
        if provider.lower() == "futu":
            if market == Market.US:
                return f"US.{symbol}"
            elif market == Market.HK:
                return f"HK.{symbol}"
            elif market == Market.CN:
                return f"SH.{symbol}" if symbol.startswith(("6", "9")) else f"SZ.{symbol}"
        elif provider.lower() in ("yahoo", "alpha_vantage", "akshare", "tushare"):
            return symbol
        
        return symbol

    def format_for_broker(self, symbol: str, broker: str) -> str:
        """Convert internal symbol to broker-specific format."""
        return self.format_for_provider(symbol, broker)

    def detect_market(self, symbol: str) -> Market:
        """Detect market from internal symbol format."""
        if self._is_cn_symbol(symbol):
            return Market.CN
        if self._is_hk_symbol(symbol):
            return Market.HK
        if symbol.startswith("US.") or symbol in self._us_tradable:
            return Market.US
        if symbol.startswith(("ES", "NQ", "CL", "GC", "SI", "NG")):
            return Market.US
        if symbol in self._hk_tradable:
            return Market.HK
        if symbol in self._cn_tradable:
            return Market.CN
        
        return Market.US

    def normalize(self, external_symbol: str, source: str) -> str:
        """Normalize external symbol to internal format."""
        if source.lower() == "futu":
            if external_symbol.startswith(("US.", "HK.", "SH.", "SZ.")):
                return external_symbol[3:]
        elif source.lower() in ("yahoo", "alpha_vantage", "akshare", "tushare"):
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
        elif market == Market.CN:
            return symbol in self._cn_tradable
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
