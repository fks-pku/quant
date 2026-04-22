"""LLM Adapters for CIO news analysis."""

from quant.features.cio.llm_adapters.base import LLMAdapter
from quant.features.cio.llm_adapters.openai_adapter import OpenAIAdapter
from quant.features.cio.llm_adapters.claude_adapter import ClaudeAdapter
from quant.features.cio.llm_adapters.ollama_adapter import OllamaAdapter
from quant.features.cio.llm_adapters.minimax_adapter import MiniMaxAdapter

__all__ = ["LLMAdapter", "OpenAIAdapter", "ClaudeAdapter", "OllamaAdapter", "MiniMaxAdapter"]
