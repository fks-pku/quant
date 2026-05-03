"""Backward-compatibility re-exports from domain.exceptions."""

from quant.domain.exceptions import OrderRejectedError, OrderRejectionReason

__all__ = ["OrderRejectedError", "OrderRejectionReason"]
