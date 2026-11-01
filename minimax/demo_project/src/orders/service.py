"""Order query helpers."""

from __future__ import annotations


def filter_orders(orders, status=None, customer_id=None):
    """Apply status/customer filters on an arbitrary order list (SPEC-001.1, SPEC-001.2)."""
    selected = list(orders)
    if status is not None:
        selected = [row for row in selected if row["status"] == status]
    if customer_id is not None:
        selected = [row for row in selected if row["customer_id"] == customer_id]
    return selected


def total_amount(orders) -> float:
    """Sum order amounts rounded to two decimals (SPEC-001.1)."""
    return round(sum(float(row["amount"]) for row in orders), 2)
