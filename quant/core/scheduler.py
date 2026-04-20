"""Time-based scheduling for market events and jobs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, List, Optional, Any
import threading
import time

from croniter import croniter

from quant.utils.datetime_utils import (
    get_current_time,
    get_next_market_open,
    get_next_market_close,
    is_market_open,
    is_trading_day,
)


class JobType(Enum):
    """Job trigger types."""
    MARKET_OPEN = "market_open"
    MARKET_CLOSE = "market_close"
    INTRADAY = "intraday"
    SCHEDULED = "scheduled"


@dataclass
class Job:
    """Represents a scheduled job."""
    name: str
    trigger: str
    callback: Callable
    interval_minutes: Optional[int] = None
    offset_minutes: int = 0
    cron_expression: Optional[str] = None
    market: str = "all"


class Scheduler:
    """Time-based job scheduler for live and paper trading."""

    def __init__(self, config: Dict[str, Any], event_bus):
        self.config = config
        self.event_bus = event_bus
        self.jobs: List[Job] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_run: Dict[str, datetime] = {}

    def add_job(
        self,
        name: str,
        trigger: str,
        callback: Callable,
        interval_minutes: Optional[int] = None,
        offset_minutes: int = 0,
        cron_expression: Optional[str] = None,
        market: str = "all",
    ) -> None:
        """Add a job to the scheduler."""
        job = Job(
            name=name,
            trigger=trigger,
            callback=callback,
            interval_minutes=interval_minutes,
            offset_minutes=offset_minutes,
            cron_expression=cron_expression,
            market=market,
        )
        self.jobs.append(job)

    def start(self) -> None:
        """Start the scheduler in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                self._check_jobs()
            except Exception as e:
                print(f"Scheduler error: {e}")
            time.sleep(1)

    def _check_jobs(self) -> None:
        """Check and execute due jobs."""
        markets = self.config.get("markets", {})

        for job in self.jobs:
            if job.market not in ("all", "US", "HK"):
                continue

            if job.market == "all":
                market_names = list(markets.keys())
            else:
                market_names = [job.market]

            for market_name in market_names:
                if market_name not in markets:
                    continue
                market_config = markets[market_name]
                tz = market_config.get("timezone", "America/New_York")
                now = get_current_time(tz)

                if job.trigger == "market_open":
                    self._check_market_open_job(job, now, market_name, market_config)
                elif job.trigger == "market_close":
                    self._check_market_close_job(job, now, market_name, market_config)
                elif job.trigger == "intraday":
                    self._check_intraday_job(job, now, market_config)
                elif job.trigger == "scheduled":
                    self._check_scheduled_job(job, now, tz)

    def _check_market_open_job(self, job: Job, now: datetime, market_name: Optional[str] = None, market_config: Optional[Dict] = None) -> None:
        """Check if a market open job should run."""
        if market_config is None:
            return
        if market_name is None:
            return

        tz = market_config.get("timezone", "America/New_York")
        open_hour = market_config.get("open_hour", 9)
        open_minute = market_config.get("open_minute", 30)

        next_open = get_next_market_open(now, tz, open_hour, open_minute)
        target_time = next_open + timedelta(minutes=job.offset_minutes)

        if self._is_within_one_second(now, target_time):
            if self._should_run_job(job.name, market_name, tz):
                job.callback()
                self._last_run[f"{job.name}_{market_name}"] = now

    def _check_market_close_job(self, job: Job, now: datetime, market_name: Optional[str] = None, market_config: Optional[Dict] = None) -> None:
        """Check if a market close job should run."""
        if market_config is None:
            return
        if market_name is None:
            return

        tz = market_config.get("timezone", "America/New_York")
        close_hour = market_config.get("close_hour", 16)
        close_minute = market_config.get("close_minute", 0)

        next_close = get_next_market_close(now, tz, close_hour, close_minute)
        target_time = next_close - timedelta(minutes=job.offset_minutes)

        if self._is_within_one_second(now, target_time):
            if self._should_run_job(job.name, market_name, tz):
                job.callback()
                self._last_run[f"{job.name}_{market_name}"] = now

    def _check_intraday_job(self, job: Job, now: datetime, market_config: Optional[Dict] = None) -> None:
        """Check if an intraday interval job should run."""
        if not job.interval_minutes:
            return

        if market_config is None:
            market_config = {}

        interval_seconds = job.interval_minutes * 60
        last = self._last_run.get(job.name)

        tz = market_config.get("timezone", "America/New_York")
        open_hour = market_config.get("open_hour", 9)
        open_minute = market_config.get("open_minute", 30)
        close_hour = market_config.get("close_hour", 16)
        close_minute = market_config.get("close_minute", 0)

        if not is_market_open(now, open_hour, open_minute, close_hour, close_minute):
            return

        if last is None:
            self._last_run[job.name] = now
            job.callback()
            return

        elapsed = (now - last).total_seconds()
        if elapsed >= interval_seconds:
            job.callback()
            self._last_run[job.name] = now

    def _check_scheduled_job(self, job: Job, now: datetime, tz: str = "America/New_York") -> None:
        """Check if a cron-scheduled job should run."""
        if not job.cron_expression:
            return

        cron = croniter(job.cron_expression, now)
        prev_run = cron.get_prev(datetime)

        if self._is_within_one_second(now, prev_run):
            if self._should_run_job(job.name, tz=tz):
                job.callback()
                self._last_run[job.name] = now

    def _should_run_job(self, job_name: str, market_name: Optional[str] = None, tz: str = "America/New_York") -> bool:
        """Check if job should run (not already run recently)."""
        key = f"{job_name}_{market_name}" if market_name else job_name
        last = self._last_run.get(key)
        if last is None:
            return True
        return (get_current_time(tz) - last).total_seconds() > 1

    def _is_within_one_second(self, t1: datetime, t2: datetime) -> bool:
        """Check if two times are within 1 second of each other."""
        return abs((t1 - t2).total_seconds()) < 1
