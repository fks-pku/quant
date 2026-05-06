"""ZhipuAI GLM LLM adapter."""

import json
import os
import re
from typing import Dict, Any

from quant.features.cio.llm_adapters.base import LLMAdapter


class GLMAdapter(LLMAdapter):
    """ZhipuAI GLM adapter for strategy evaluation."""

    def __init__(
        self,
        model: str = "glm-5.1",
        api_key: str = "",
        base_url: str = "https://api.z.ai/api/coding/paas/v4/",
        temperature: float = 0.3,
        timeout: float = 120.0,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("GLM_API_KEY", "")
        self.base_url = base_url
        self.temperature = temperature
        self.timeout = timeout

    def analyze(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}

        if not self.api_key:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "GLM API key not configured"}

        try:
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
            )
            content = response.choices[0].message.content.strip()
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?```\s*$", "", content)
            return json.loads(content.strip())
        except Exception:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": "LLM analysis unavailable"}
