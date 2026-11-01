"""Regression tests: existing behaviour must stay intact."""

from orders.repository import ORDERS, get_order, list_orders
from orders.service import filter_orders, total_amount


def test_status_filter():
    assert [row["order_id"] for row in list_orders(status="paid")] == [1, 3, 5]


def test_customer_filter():
    assert [row["order_id"] for row in list_orders(customer_id=200)] == [3, 4]


def test_combined_filter():
    assert [row["order_id"] for row in list_orders(status="paid", customer_id=200)] == [3]


def test_no_filter_keeps_order():
    assert [row["order_id"] for row in list_orders()] == [row["order_id"] for row in ORDERS]


def test_get_order():
    assert get_order(2)["status"] == "shipped"
    assert get_order(999) is None


def test_total_amount():
    assert total_amount(list_orders(status="paid")) == 23.15


def test_service_filter_matches_repository():
    assert filter_orders(ORDERS, status="cancelled") == list_orders(status="cancelled")
