"""Offline stand-in repository used only by --provider mock plumbing checks."""

from __future__ import annotations

ORDERS: list[dict] = [
    {"order_id": 1, "customer_id": 100, "status": "paid", "amount": 12.5, "order_date": "2026-01-05"},
    {"order_id": 2, "customer_id": 100, "status": "shipped", "amount": 40.0, "order_date": "2026-02-11"},
    {"order_id": 3, "customer_id": 200, "status": "paid", "amount": 7.25, "order_date": "2026-03-02"},
    {"order_id": 4, "customer_id": 200, "status": "cancelled", "amount": 99.99, "order_date": "2026-03-19"},
    {"order_id": 5, "customer_id": 300, "status": "paid", "amount": 3.4, "order_date": "2026-04-28"},
]


def _check_range(since, until) -> None:
    if since is not None and until is not None and str(since) > str(until):
        raise ValueError("since must not be after until")


def list_orders(status=None, customer_id=None, since=None, until=None):
    """Return orders filtered by status, customer and an optional closed date range."""
    _check_range(since, until)
    from .service import filter_orders

    return filter_orders(ORDERS, status=status, customer_id=customer_id,
                         since=since, until=until)


def get_order(order_id):
    """Return a single order by primary key."""
    for row in ORDERS:
        if row["order_id"] == order_id:
            return row
    return None
