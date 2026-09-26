"""Focused standard-library tests for the volume-based tote optimizer."""

from __future__ import annotations

import unittest
from datetime import date

from backend import config
from backend.models import HouseholdOrder, OrderItem
from backend.services.csv_service import load_stage1_orders, load_stage2_orders, validate_tote_allocations
from backend.services.tote_optimizer import optimize_totes


def make_order(order_id: int, volumes: list[float], *, source_quantity: int = 1) -> HouseholdOrder:
    items = [
        OrderItem(
            order_id=order_id,
            household_id=order_id + 1000,
            batch_id=1,
            order_date=date(2026, 6, 1),
            destination_community="Webequie",
            product_id=9000 + index,
            product_name=f"Item {index}",
            length_in=volume,
            width_in=1.0,
            height_in=1.0,
            weight_lb=2.0,
            order_item_id=f"synthetic:{order_id}:{index}",
            source_quantity=source_quantity,
        )
        for index, volume in enumerate(volumes)
    ]
    return HouseholdOrder(
        order_id=order_id,
        household_id=order_id + 1000,
        batch_id=1,
        order_date=date(2026, 6, 1),
        destination_community="Webequie",
        items=items,
    )


class ToteOptimizerTests(unittest.TestCase):
    def test_simple_orders_share_tote_and_keep_order_complete(self):
        orders = [make_order(order_id, [900.0]) for order_id in (3, 1, 2)]
        result = optimize_totes(orders)
        self.assertEqual(result.total_totes, 1)
        self.assertEqual(result.complete_orders, 3)
        self.assertEqual(result.split_orders, 0)
        self.assertEqual(result.totes[0].order_ids, [1, 2, 3])

    def test_exact_capacity_is_accepted(self):
        order = make_order(10, [config.TOTE_VOLUME_IN3])
        result = optimize_totes([order])
        self.assertEqual(result.total_totes, 1)
        self.assertAlmostEqual(result.totes[0].volume_in3, config.TOTE_VOLUME_IN3)
        self.assertEqual(result.unallocated_items, [])

    def test_oversized_order_is_split(self):
        order = make_order(20, [2000.0, 1700.0, 1000.0])
        result = optimize_totes([order])
        self.assertGreaterEqual(result.total_totes, 2)
        self.assertEqual(result.split_order_ids, [20])
        self.assertEqual(result.complete_order_ids, [20])

    def test_allocation_traceability_and_conservation(self):
        orders = [make_order(30, [800, 900]), make_order(31, [2200, 1700, 1000])]
        result = optimize_totes(orders)
        validate_tote_allocations(orders, result.totes, require_complete=True)
        seen = [allocation.order_item_id for tote in result.totes for allocation in tote.items]
        self.assertCountEqual(seen, [item.order_item_id for order in orders for item in order.items])

    def test_no_tote_exceeds_volume(self):
        result = optimize_totes([make_order(40, [2200, 1700, 1000])])
        self.assertTrue(all(tote.volume_in3 <= config.TOTE_VOLUME_IN3 for tote in result.totes))

    def test_deterministic_ids_and_allocations(self):
        orders = [make_order(order_id, volumes) for order_id, volumes in ((9, [1000, 800]), (2, [1800]), (5, [2100, 1200, 700]))]
        first, second = optimize_totes(orders), optimize_totes(orders)
        serialize = lambda result: [
            (tote.tote_id, [(item.order_item_id, item.quantity) for item in tote.items])
            for tote in result.totes
        ]
        self.assertEqual(serialize(first), serialize(second))

    def test_individually_oversized_item_is_reported_not_dropped(self):
        order = make_order(50, [config.TOTE_VOLUME_IN3 + 1.0])
        result = optimize_totes([order])
        self.assertEqual(result.total_totes, 0)
        self.assertEqual(len(result.unallocated_items), 1)
        self.assertEqual(result.unallocated_items[0].order_item_id, order.items[0].order_item_id)
        self.assertEqual(result.unallocated_items[0].quantity, 1)

    def test_source_quantity_units_can_be_split_without_fractional_allocation(self):
        order = make_order(60, [2000.0], source_quantity=2)
        result = optimize_totes([order])
        self.assertEqual(result.split_order_ids, [60])
        self.assertEqual(result.complete_order_ids, [60])
        self.assertEqual(sum(a.quantity for t in result.totes for a in t.items), 2)

    def test_real_stage_1_and_stage_2_data(self):
        for orders, expected_order_count in ((load_stage1_orders(), 30), (load_stage2_orders(), 120)):
            result = optimize_totes(orders)
            self.assertEqual(result.total_orders, expected_order_count)
            self.assertEqual(result.unallocated_items, [])
            validate_tote_allocations(orders, result.totes, require_complete=True)
            self.assertTrue(all(tote.volume_in3 <= config.TOTE_VOLUME_IN3 for tote in result.totes))
        stage2 = optimize_totes(load_stage2_orders())
        self.assertEqual(set(stage2.oversized_order_ids), {1454025, 2011265, 2209890, 3186030})


if __name__ == "__main__":
    unittest.main()
