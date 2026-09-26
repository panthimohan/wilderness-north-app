"""Focused tests for deterministic cart assignment."""

from __future__ import annotations

import math
import unittest
from datetime import date

from backend import config
from backend.models import HouseholdOrder, OrderItem, Tote, ToteItem
from backend.services.csv_service import load_stage1_orders, load_stage2_orders
from backend.services.tote_optimizer import optimize_totes
from backend.services.cart_optimizer import optimize_carts


def make_order(order_id: int, item_count: int = 1) -> HouseholdOrder:
    items = [
        OrderItem(
            order_id=order_id,
            household_id=order_id + 500,
            batch_id=1,
            order_date=date(2026, 6, 1),
            destination_community="Webequie",
            product_id=order_id * 10 + index,
            product_name=f"Product {index}",
            length_in=1.0,
            width_in=1.0,
            height_in=1.0,
            weight_lb=1.0,
            order_item_id=f"cart-test:{order_id}:{index}",
        )
        for index in range(item_count)
    ]
    return HouseholdOrder(
        order_id=order_id,
        household_id=order_id + 500,
        batch_id=1,
        order_date=date(2026, 6, 1),
        destination_community="Webequie",
        items=items,
    )


class CartOptimizerTests(unittest.TestCase):
    def test_nine_totes_pack_into_two_default_carts(self):
        totes = [Tote(f"T{index:03d}") for index in range(1, 10)]
        result = optimize_carts(totes, [])
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_carts, 2)
        self.assertEqual(sorted(len(cart.totes) for cart in result.carts), [4, 5])

    def test_38_independent_totes_use_the_minimum_cart_count(self):
        totes = [Tote(f"M{index:03d}") for index in range(1, 39)]
        result = optimize_carts(totes, [])
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.total_totes, 38)
        self.assertEqual(sum(len(cart.totes) for cart in result.carts), 38)
        self.assertTrue(all(len(cart.totes) <= 5 for cart in result.carts))
        self.assertEqual(result.total_carts, math.ceil(38 / 5))

    def test_stage2_generated_totes_report_real_order_group_infeasibility(self):
        orders = load_stage2_orders()
        tote_result = optimize_totes(orders)
        self.assertEqual(tote_result.total_totes, 38)
        result = optimize_carts(tote_result.totes, orders)
        self.assertFalse(result.feasible)
        self.assertEqual(result.unassigned_tote_ids, [f"T{index:03d}" for index in range(1, 39)])
        self.assertTrue(any("group of 6" in error and "capacity 5" in error for error in result.errors))

    def test_split_order_totes_stay_on_one_cart(self):
        order = make_order(10, item_count=3)
        totes = [Tote(f"S{index}", [ToteItem(item)]) for index, item in enumerate(order.items)]
        result = optimize_carts(totes, [order], totes_per_cart=3)
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.total_carts, 1)
        self.assertEqual(result.per_cart_metrics[0].split_order_ids, [10])
        self.assertEqual({tote.cart_id for tote in totes}, {result.carts[0].cart_id})

    def test_orders_sharing_totes_form_one_consistent_assignment_group(self):
        order_a, order_b = make_order(20, 2), make_order(21, 2)
        totes = [
            Tote("shared", [ToteItem(order_a.items[0]), ToteItem(order_b.items[0])]),
            Tote("a-extra", [ToteItem(order_a.items[1])]),
            Tote("b-extra", [ToteItem(order_b.items[1])]),
        ]
        result = optimize_carts(totes, [order_a, order_b], totes_per_cart=3)
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.total_carts, 1)
        self.assertEqual(result.carts[0].order_ids, [20, 21])

    def test_order_larger_than_cart_capacity_is_infeasible(self):
        order = make_order(30, item_count=6)
        totes = [Tote(f"O{index}", [ToteItem(item)]) for index, item in enumerate(order.items)]
        result = optimize_carts(totes, [order], totes_per_cart=5)
        self.assertFalse(result.feasible)
        self.assertTrue(any("Order 30 requires 6 totes" in error for error in result.errors))
        self.assertEqual(result.unassigned_tote_ids, [f"O{index}" for index in range(6)])
        self.assertTrue(all(tote.cart_id is None for tote in totes))

    def test_deterministic_and_idempotent_for_same_tote_objects(self):
        orders = [make_order(40, 2), make_order(41, 3), make_order(42, 1)]
        totes = [Tote("D1", [ToteItem(orders[0].items[0])]), Tote("D2", [ToteItem(orders[0].items[1])])]
        totes.extend(Tote(f"D{index+3}", [ToteItem(item)]) for index, item in enumerate(orders[1].items + orders[2].items))
        first = optimize_carts(totes, orders, totes_per_cart=3)
        first_assignments = sorted((tote.tote_id, tote.cart_id) for tote in totes)
        second = optimize_carts(totes, orders, totes_per_cart=3)
        second_assignments = sorted((tote.tote_id, tote.cart_id) for tote in totes)
        self.assertEqual(first_assignments, second_assignments)
        self.assertEqual(
            [(cart.cart_id, sorted(t.tote_id for t in cart.totes)) for cart in first.carts],
            [(cart.cart_id, sorted(t.tote_id for t in cart.totes)) for cart in second.carts],
        )

    def test_real_stage1_tote_result(self):
        orders = load_stage1_orders()
        tote_result = optimize_totes(orders)
        self.assertEqual(tote_result.total_totes, 9)
        result = optimize_carts(tote_result.totes, orders)
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.total_carts, 2)
        self.assertEqual(sorted(len(cart.totes) for cart in result.carts), [4, 5])

    def test_real_stage2_tote_result(self):
        orders = load_stage2_orders()
        tote_result = optimize_totes(orders)
        result = optimize_carts(tote_result.totes, orders)
        self.assertFalse(result.feasible)
        self.assertEqual(result.total_totes, 38)
        self.assertEqual(result.total_carts, 0)
        self.assertTrue(all("capacity 5" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
