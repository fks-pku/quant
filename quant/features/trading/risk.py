"""Risk engine for pre-order checks and position limits."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
import threading
import time

from quant.domain.models.risk_check import RiskCheckResult
from quant.domain.ports.event_publisher import EventPublisher
from quant.features.trading.portfolio import Portfolio
from quant.shared.utils.logger import setup_logger


class RiskEngine:
    """Risk checks run before every order submission."""

    def __init__(self, config: Dict, portfolio: Portfolio, event_bus):
        self.config = config
        self.portfolio = portfolio
        self.event_bus = event_bus
        self.risk_config = config.get("risk", {})
        self.logger = setup_logger("RiskEngine")

        self.max_position_pct = self.risk_config.get("max_position_pct", 0.05)
        self.max_sector_pct = self.risk_config.get("max_sector_pct", 0.25)
        self.max_daily_loss_pct = self.risk_config.get("max_daily_loss_pct", 0.02)
        self.max_leverage = self.risk_config.get("max_leverage", 1.5)
        self.max_orders_per_minute = self.risk_config.get("max_orders_minute", 30)

        self._order_timestamps: List[datetime] = []
        self._lock = threading.RLock()

    def check_order(
        self,
        symbol: str,
        quantity: float,
        price: float,
        order_value: float,
        sector: Optional[str] = None,
        side: Optional[str] = None,
        as_of_date: Optional[date] = None,
    ) -> Tuple[bool, List[RiskCheckResult]]:
        """
        Run all risk checks before order submission.
        Returns (approved, list_of_results).
        """
        results = []
        approved = True

        if side == 'SELL':
            results.append(self._check_cn_t1_settlement(symbol, quantity, as_of_date))
            if not results[-1].passed:
                approved = False

        if side != 'SELL':
            results.append(self._check_position_size(symbol, order_value))
            if not results[-1].passed:
                approved = False

            if sector:
                results.append(self._check_sector_exposure(sector, order_value))
                if not results[-1].passed:
                    approved = False

        results.append(self._check_daily_loss())
        if not results[-1].passed:
            approved = False

        results.append(self._check_leverage())
        if not results[-1].passed:
            approved = False

        results.append(self._check_order_rate())
        if not results[-1].passed:
            approved = False

        return approved, results

    def _check_cn_t1_settlement(self, symbol: str, quantity: float, as_of_date: Optional[date] = None) -> RiskCheckResult:
        if not Portfolio.is_cn_symbol(symbol):
            return RiskCheckResult(
                passed=True, is_hard_limit=False,
                check_name="cn_t1_settlement",
                message="Not a CN symbol, T+1 does not apply",
                current_value=0, limit_value=0,
            )

        today = as_of_date or date.today()
        settled = self.portfolio.settled_quantity(symbol, today)
        pos = self.portfolio.get_position(symbol)
        total_qty = pos.quantity if pos else 0

        passed = quantity <= settled

        return RiskCheckResult(
            passed=passed,
            is_hard_limit=True,
            check_name="cn_t1_settlement",
            message=(
                f"CN T+1: sell {quantity} shares of {symbol} but only "
                f"{settled}/{total_qty} settled (bought before today)"
            ),
            current_value=quantity,
            limit_value=settled,
        )

    def _check_position_size(self, symbol: str, order_value: float) -> RiskCheckResult:
        """Check max position size (5% of NAV per symbol)."""
        nav = self.portfolio.nav
        limit = nav * self.max_position_pct

        existing_pos = self.portfolio.get_position(symbol)
        existing_value = existing_pos.market_value if existing_pos else 0
        total_value = existing_value + order_value

        passed = total_value <= limit

        return RiskCheckResult(
            passed=passed,
            is_hard_limit=True,
            check_name="max_position_size",
            message=f"Position {symbol}: ${total_value:.2f} exceeds limit ${limit:.2f}",
            current_value=total_value,
            limit_value=limit,
        )

    def _check_sector_exposure(self, sector: str, order_value: float) -> RiskCheckResult:
        """Check max sector exposure (25% of NAV per sector)."""
        nav = self.portfolio.nav
        limit = nav * self.max_sector_pct

        sector_exposure = self.portfolio.get_sector_exposure()
        current_sector_pct = sector_exposure.get(sector, 0)
        current_sector_value = current_sector_pct * nav
        total_sector_value = current_sector_value + order_value

        passed = total_sector_value <= limit

        return RiskCheckResult(
            passed=passed,
            is_hard_limit=True,
            check_name="max_sector_exposure",
            message=f"Sector {sector}: ${total_sector_value:.2f} exceeds limit ${limit:.2f}",
            current_value=total_sector_value,
            limit_value=limit,
        )

    def _check_daily_loss(self) -> RiskCheckResult:
        """Check max daily loss (2% of starting NAV)."""
        limit = self.portfolio.starting_nav * self.max_daily_loss_pct
        current_loss = self.portfolio.starting_nav - self.portfolio.nav
        passed = current_loss <= limit

        return RiskCheckResult(
            passed=passed,
            is_hard_limit=True,
            check_name="max_daily_loss",
            message=f"Daily loss ${current_loss:.2f} exceeds limit ${limit:.2f}",
            current_value=current_loss,
            limit_value=limit,
        )

    def _check_leverage(self) -> RiskCheckResult:
        """Check max leverage (1.5x)."""
        nav = self.portfolio.nav
        margin_used = self.portfolio.margin_used
        leverage = margin_used / nav if nav != 0 else 0

        passed = leverage <= self.max_leverage

        return RiskCheckResult(
            passed=passed,
            is_hard_limit=True,
            check_name="max_leverage",
            message=f"Leverage {leverage:.2f}x exceeds limit {self.max_leverage:.2f}x",
            current_value=leverage,
            limit_value=self.max_leverage,
        )

    def _check_order_rate(self) -> RiskCheckResult:
        """Check max orders per minute (30)."""
        now = datetime.now()
        cutoff = now.timestamp() - 60

        with self._lock:
            self._order_timestamps = [
                ts for ts in self._order_timestamps if ts.timestamp() > cutoff
            ]
            order_count = len(self._order_timestamps)
            passed = order_count < self.max_orders_per_minute

        return RiskCheckResult(
            passed=passed,
            is_hard_limit=False,
            check_name="max_order_rate",
            message=f"Order rate {order_count}/min exceeds soft limit {self.max_orders_per_minute}/min",
            current_value=order_count,
            limit_value=self.max_orders_per_minute,
        )

    def record_order(self) -> None:
        """Record an order submission for rate limiting."""
        with self._lock:
            self._order_timestamps.append(datetime.now())

    def log_result(self, results: List[RiskCheckResult]) -> None:
        """Log risk check results."""
        for result in results:
            if not result.passed:
                if result.is_hard_limit:
                    self.logger.critical(f"Risk check '{result.check_name}': {result.message}")
                else:
                    self.logger.warning(f"Risk check '{result.check_name}': {result.message}")
