import time
import random
import logging
from typing import Dict

logger = logging.getLogger(__name__)

_DEFAULT_INTERVALS = {
    "arxiv": 5.0,
    "default": 2.0,
}


class RateLimiter:
    def __init__(self, intervals: Dict[str, float] = None, jitter: float = 2.0):
        self._intervals = intervals or _DEFAULT_INTERVALS
        self._jitter = jitter
        self._last_call: Dict[str, float] = {}

    def wait(self, source_name: str) -> None:
        interval = self._intervals.get(source_name, self._intervals.get("default", 2.0))
        elapsed = time.time() - self._last_call.get(source_name, 0)
        wait_time = max(0, interval - elapsed) + random.uniform(0, self._jitter)
        if wait_time > 0:
            time.sleep(wait_time)
        self._last_call[source_name] = time.time()
