"""CIOEngine - Chief Investment Officer engine for market assessment and weight allocation."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from quant.features.cio.market_assessor import MarketAssessor
from quant.features.cio.weight_allocator import WeightAllocator


class CIOEngine:
    def __init__(
        self,
        assessor: Optional[MarketAssessor] = None,
        news_analyzer: Optional[Any] = None,
        allocator: Optional[WeightAllocator] = None,
    ):
        self.assessor = assessor or MarketAssessor()
        self.news_analyzer = news_analyzer
        self.allocator = allocator or WeightAllocator()
        self._cached_assessment: Optional[Dict] = None

    def assess(
        self,
        indicators: Optional[Dict] = None,
        news_text: Optional[str] = None,
        enabled_strategies: Optional[List[str]] = None,
    ) -> Dict:
        """Perform full market assessment and return assessment dict."""
        assessment = self.assessor.assess(indicators)
        regime = assessment["regime"]
        score = assessment["score"]
        weights = self.allocator.allocate(regime, enabled_strategies)
        news_result = self._analyze_news(news_text, assessment)
        self._cached_assessment = {
            "environment": regime,
            "score": score,
            "sentiment": news_result["sentiment"],
            "confidence": news_result["confidence"],
            "weights": weights,
            "indicators": assessment.get("indicators", {}),
            "last_updated": datetime.utcnow().isoformat(),
            "llm_summary": "",
        }
        return self._cached_assessment

    def get_cached(self) -> Optional[Dict]:
        """Return cached assessment or None."""
        return self._cached_assessment

    def _analyze_news(self, news_text: Optional[str], market_result: Dict) -> Dict:
        """Analyze news text and return sentiment and confidence."""
        if not self.news_analyzer or not news_text:
            return {"sentiment": "neutral", "confidence": 0.5, "summary": ""}
        return self.news_analyzer.analyze(news_text, market_result)
