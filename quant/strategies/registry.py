from typing import List


class StrategyRegistry:
    _strategies: List[str] = []

    @staticmethod
    def list_strategies() -> List[str]:
        return list(StrategyRegistry._strategies)

    @staticmethod
    def register(name: str) -> None:
        StrategyRegistry._strategies.append(name)
