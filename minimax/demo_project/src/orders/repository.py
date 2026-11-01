"""In-memory order repository."""

from __future__ import annotations

from datetime import date

ORDERS: list[dict] = [
    {"order_id": 1, "customer_id": 100, "status": "paid", "amount": 12.5, "order_date": "2026-01-05"},
    {"order_id": 2, "customer_id": 100, "status": "shipped", "amount": 40.0, "order_date": "2026-02-11"},
    {"order_id": 3, "customer_id": 200, "status": "paid", "amount": 7.25, "order_date": "2026-03-02"},
    {"order_id": 4, "customer_id": 200, "status": "cancelled", "amount": 99.99, "order_date": "2026-03-19"},
    {"order_id": 5, "customer_id": 300, "status": "paid", "amount": 3.4, "order_date": "2026-04-28"},
]


def _validate_optional_query_value(name, value):
    if isinstance(value, str) and value == "":
        raise ValueError(f"{name} must not be empty (actual value: {value!r})")


def _validate_date_query_value(name, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{name} must be a valid ISO date string (actual value: {value!r})"
        )
    if value == "":
        raise ValueError(f"{name} must not be empty (actual value: {value!r})")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{name} must be a valid ISO date string (actual value: {value!r})"
        ) from None


def _validate_filter_parameters(status, customer_id, since, until):
    _validate_optional_query_value("status", status)
    _validate_optional_query_value("customer_id", customer_id)

    since_date = _validate_date_query_value("since", since)
    until_date = _validate_date_query_value("until", until)

    if (
        since_date is not None
        and until_date is not None
        and since_date > until_date
    ):
        raise ValueError(
            "since must not be after until "
            f"(since={since!r}, until={until!r})"
        )

    return since_date, until_date


def _matches_date_range(row, since, until):
    order_date = date.fromisoformat(row["order_date"])
    return (since is None or order_date >= since) and (
        until is None or order_date <= until
    )


def list_orders(status=None, customer_id=None, since=None, until=None):
    """Return orders filtered by status, customer, and inclusive date range."""
    since_date, until_date = _validate_filter_parameters(
        status, customer_id, since, until
    )

    selected = ORDERS
    if status is not None:
        selected = [row for row in selected if row["status"] == status]
    if customer_id is not None:
        selected = [row for row in selected if row["customer_id"] == customer_id]
    if since_date is not None or until_date is not None:
        selected = [
            row
            for row in selected
            if _matches_date_range(row, since_date, until_date)
        ]
    return selected


def get_order(order_id):
    """Return a single order by primary key."""
    for row in ORDERS:
        if row["order_id"] == order_id:
            return row
    return None
