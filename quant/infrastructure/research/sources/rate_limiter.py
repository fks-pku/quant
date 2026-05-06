import random
import threading
import time
from typing import Dict


class RateLimiter:
    def __init__(self, min_interval_seconds: float = 3.0, jitter_seconds: float = 2.0):
        self.min_interval_seconds = min_interval_seconds
        self.jitter_seconds = jitter_seconds
        self._last_seen: Dict[str, float] = {}
        self._lock = threading.RLock()

    def wait(self, source_name: str) -> None:
        with self._lock:
            now = time.monotonic()
            last_seen = self._last_seen.get(source_name)
            delay = 0.0
            if last_seen is not None:
                delay = max(0.0, self.min_interval_seconds - (now - last_seen))
                if delay > 0 and self.jitter_seconds > 0:
                    delay += random.uniform(0.0, self.jitter_seconds)
            self._last_seen[source_name] = now + delay

        if delay > 0:
            time.sleep(delay)
