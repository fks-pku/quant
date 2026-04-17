"""Strategy registry with decorator-based registration and directory auto-discovery."""

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Type

_registry: Dict[str, Type] = {}


def _discover_strategies() -> None:
    """Auto-discover strategies from subdirectories."""
    strategies_dir = Path(__file__).parent

    for item in strategies_dir.iterdir():
        if not item.is_dir() or item.name.startswith("_") or item.name.startswith("."):
            continue

        strategy_file = item / "strategy.py"
        if not strategy_file.exists():
            continue

        try:
            module_name = f"quant.strategies.{item.name}.strategy"
            spec = importlib.util.spec_from_file_location(module_name, strategy_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for attr_name in dir(module):
                    cls = getattr(module, attr_name)
                    if isinstance(cls, type) and hasattr(cls, "_registry_name"):
                        _registry[cls._registry_name] = cls
        except Exception:
            pass


def strategy(name: str):
    """Decorator to register a strategy class by name."""
    def decorator(cls: Type) -> Type:
        _registry[name] = cls
        cls._registry_name = name
        return cls
    return decorator


class StrategyRegistry:
    @staticmethod
    def register(name: str, cls: Type) -> None:
        _registry[name] = cls

    @staticmethod
    def get(name: str):
        return _registry.get(name)

    @staticmethod
    def create(name: str, **kwargs: Any):
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


_discover_strategies()
