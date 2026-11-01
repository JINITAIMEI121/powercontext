"""Target functional tests for the date-range task. They must fail before the change."""

import copy

import pytest

from orders.repository import ORDERS, list_orders
from orders.service import filter_orders


def test_filter_orders_supports_since_and_until():
    rows = filter_orders(ORDERS, since="2026-02-01", until="2026-03-31")
    assert [row["order_id"] for row in rows] == [2, 3, 4]


def test_filter_orders_single_bound():
    assert [row["order_id"] for row in filter_orders(ORDERS, since="2026-04-01")] == [5]
    assert [row["order_id"] for row in filter_orders(ORDERS, until="2026-01-31")] == [1]


def test_invalid_range_raises_value_error():
    with pytest.raises(ValueError) as exc:
        filter_orders(ORDERS, since="2026-05-01", until="2026-01-01")
    assert "since must not be after until" in str(exc.value)


def test_input_list_is_not_mutated():
    snapshot = copy.deepcopy(ORDERS)
    rows = filter_orders(ORDERS, status="paid", since="2026-03-01")
    assert ORDERS == snapshot
    assert rows[0] is ORDERS[2]


def test_repository_list_orders_supports_date_range():
    rows = list_orders(status="paid", since="2026-03-01")
    assert [row["order_id"] for row in rows] == [3, 5]
