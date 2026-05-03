"""Abstract LLM adapter interface."""

from abc import abstractmethod
from typing import Dict, Any

from quant.domain.ports.llm import LLMAdapterLike


class LLMAdapter(LLMAdapterLike):
    """Abstract base class for LLM-based news analysis."""

    @abstractmethod
    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Send prompt + context to LLM, return structured analysis.
        Must return: {"sentiment": str, "confidence": float, "summary": str}
        """
        ...
