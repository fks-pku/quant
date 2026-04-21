"""Abstract LLM adapter interface."""

from abc import ABC, abstractmethod
from typing import Dict, Any


class LLMAdapter(ABC):
    """Abstract base class for LLM-based news analysis."""

    @abstractmethod
    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send prompt + context to LLM, return structured analysis.
        Must return: {"sentiment": str, "confidence": float, "summary": str}
        """
        ...
