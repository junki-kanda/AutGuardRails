"""Lambda handlers for AutoGuardRails."""

from .budgets_event import lambda_handler as budgets_handler


__all__ = ["budgets_handler"]
