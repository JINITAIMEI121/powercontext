"""Offline stand-in used only by --provider mock plumbing checks."""

from __future__ import annotations

from datetime import date


def _valid(value) -> bool:
    return isinstance(value, (date, str))


def filter_orders(orders, status=None, customer_id=None, since=None, until=None):
    if since is not None and until is not None and _valid(since) and _valid(until):
        if str(since) > str(until):
            raise ValueError("since must not be after until")
    selected = list(orders)
    if status is not None:
        selected = [row for row in selected if row["status"] == status]
    if customer_id is not None:
        selected = [row for row in selected if row["customer_id"] == customer_id]
    if since is not None:
        selected = [row for row in selected if str(row["order_date"]) >= str(since)]
    if until is not None:
        selected = [row for row in selected if str(row["order_date"]) <= str(until)]
    return selected


def total_amount(orders) -> float:
    return round(sum(float(row["amount"]) for row in orders), 2)
