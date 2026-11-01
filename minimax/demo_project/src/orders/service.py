"""Order query helpers."""

from __future__ import annotations

from datetime import date


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


def filter_orders(orders, status=None, customer_id=None, since=None, until=None):
    """Apply status, customer, and inclusive date-range filters to orders."""
    since_date, until_date = _validate_filter_parameters(
        status, customer_id, since, until
    )

    selected = list(orders)
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


def total_amount(orders) -> float:
    """Sum order amounts rounded to two decimals (SPEC-001.1)."""
    return round(sum(float(row["amount"]) for row in orders), 2)
