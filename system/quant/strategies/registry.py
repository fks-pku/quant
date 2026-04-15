"""Strategy registry with decorator-based registration.

Usage:
    from quant.strategies.registry import StrategyRegistry, strategy

    @strategy("VolatilityRegime")
    class VolatilityRegime(Strategy):
        ...

    # Later:
    cls = StrategyRegistry.get("VolatilityRegime")
    instance = cls(symbols=["AAPL"], **params)
"""

from typing import Any, Callable, Dict, Optional, Type, List


_registry: Dict[str, Type] = {}


def strategy(name: str) -> Callable:
    """Decorator to register a strategy class by name."""
    def decorator(cls: Type) -> Type:
        _registry[name] = cls
        cls._registry_name = name
        return cls
    return decorator


class StrategyRegistry:
    """Central registry for strategy classes."""

    @staticmethod
    def register(name: str, cls: Type) -> None:
        _registry[name] = cls

    @staticmethod
    def get(name: str) -> Optional[Type]:
        return _registry.get(name)

    @staticmethod
    def create(name: str, **kwargs: Any) -> Any:
        cls = _registry.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy: {name}. Registered: {list(_registry.keys())}")
        return cls(**kwargs)

    @staticmethod
    def list_strategies() -> List[str]:
        return list(_registry.keys())

    @staticmethod
    def is_registered(name: str) -> bool:
        return name in _registry
