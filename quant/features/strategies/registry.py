"""Strategy registry with decorator-based registration and directory auto-discovery.

Strategy IDs are case-insensitive — all names are normalized to lowercase.
"""

import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Type

from quant.shared.utils.logger import setup_logger

_registry: Dict[str, Type] = {}


def _key(name: str) -> str:
    return name.lower()


def _is_archived_or_rejected_module(module_name: str) -> bool:
    return (
        ".features.rejected_strategy." in module_name
        or ".features.strategies.reject." in module_name
    )


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
                    if (
                        isinstance(cls, type)
                        and hasattr(cls, "_registry_name")
                        and getattr(cls, "_registry_active", True)
                    ):
                        _registry[_key(cls._registry_name)] = cls
        except Exception:
            _log = setup_logger("strategy.registry")
            _log.warning("Failed to load strategy from %s", strategy_file, exc_info=True)


def strategy(name: str):
    """Decorator to register a strategy class by name (case-insensitive)."""
    def decorator(cls: Type) -> Type:
        cls._registry_name = name
        cls._registry_active = not _is_archived_or_rejected_module(getattr(cls, "__module__", ""))
        if cls._registry_active:
            _registry[_key(name)] = cls
        return cls
    return decorator


class StrategyRegistry:
    @staticmethod
    def register(name: str, cls: Type) -> None:
        _registry[_key(name)] = cls

    @staticmethod
    def get(name: str):
        return _registry.get(_key(name))

    @staticmethod
    def create(name: str, **kwargs: Any):
        cls = _registry.get(_key(name))
        if cls is None:
            registered = [c._registry_name for c in _registry.values()]
            raise ValueError(f"Unknown strategy: {name}. Registered: {registered}")
        return cls(**kwargs)

    @staticmethod
    def list_strategies() -> List[str]:
        return [cls._registry_name for cls in _registry.values()]

    @staticmethod
    def is_registered(name: str) -> bool:
        return _key(name) in _registry


_discover_strategies()
