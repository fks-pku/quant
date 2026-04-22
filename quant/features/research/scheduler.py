import threading
import time
import logging
from typing import Optional

from quant.features.research.models import ResearchConfig

logger = logging.getLogger(__name__)


class ResearchScheduler:
    def __init__(self, engine, config: Optional[ResearchConfig] = None):
        self.engine = engine
        self.config = config or ResearchConfig()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                logger.warning("Research scheduler already running")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            logger.info("Research scheduler started")

    def stop(self) -> None:
        with self._lock:
            if not self.is_running:
                return
            self._stop_event.set()
            self._thread = None
            logger.info("Research scheduler stopped")

    def trigger_now(self) -> None:
        logger.info("Manual research trigger")
        try:
            self.engine.run_full_pipeline()
        except Exception as e:
            logger.error(f"Manual research run failed: {e}")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.engine.run_full_pipeline()
            except Exception as e:
                logger.error(f"Scheduled research run failed: {e}")
            interval_seconds = self.config.interval_days * 86400
            if self._stop_event.wait(timeout=interval_seconds):
                break
