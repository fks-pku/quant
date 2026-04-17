"""Ollama LLM adapter."""

import json
import urllib.request
from typing import Dict, Any

from quant.cio.llm_adapters.base import LLMAdapter


class OllamaAdapter(LLMAdapter):
    """Ollama local LLM adapter for news analysis."""

    def __init__(self, model="llama3.2", base_url="http://localhost:11434", temperature=0.3):
        self.model = model
        self.base_url = base_url
        self.temperature = temperature

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            url = f"{self.base_url}/api/generate"
            payload = {
                "model": self.model,
                "prompt": prompt,
                "temperature": self.temperature,
                "format": "json",
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return json.loads(result.get("response", "{}"))
        except Exception:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
