"""Port for LLM adapters — used by research and CIO features."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class LLMAdapterLike(ABC):
    """Contract for LLM-based analysis adapters."""

    @abstractmethod
    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Send prompt + context to LLM, return structured analysis."""
        ...
