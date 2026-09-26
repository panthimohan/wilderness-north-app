"""Flight assignment tests against synthetic and challenge data."""

from __future__ import annotations

import unittest
from datetime import date

from backend.models import FlightCapacity, HouseholdOrder, OrderItem, Tote, ToteItem
from backend.app_state import build_stage1_flight_capacity
from backend.services.csv_service import (
    load_stage1_orders,
    load_stage2_flight_capacities,
    load_stage2_orders,
)
from backend.services.flight_optimizer import optimize_flights
from backend.services.tote_optimizer import optimize_totes


def make_order(order_id: int, item_count: int = 1, *, order_date: date = date(2026, 6, 1)) -> HouseholdOrder:
    items = [
        OrderItem(
            order_id=order_id,
            household_id=order_id + 1000,
            batch_id=1,
            order_date=order_date,
            destination_community="Webequie",
            product_id=order_id * 100 + index,
            product_name=f"Item {index}",
            length_in=5.0,
            width_in=5.0,
            height_in=5.0,
            weight_lb=2.0,
            order_item_id=f"flight-test:{order_id}:{index}",
        )
        for index in range(item_count)
    ]
    return HouseholdOrder(
        order_id=order_id,
        household_id=order_id + 1000,
        batch_id=1,
        order_date=order_date,
        destination_community="Webequie",
        items=items,
    )


def one_flight(capacity: FlightCapacity) -> list[FlightCapacity]:
    return [capacity]


class FlightOptimizerTests(unittest.TestCase):
    def test_stage1_real_orders_fit_one_flight(self):
        orders = load_stage1_orders()
        tote_result = optimize_totes(orders)
        capacity = build_stage1_flight_capacity(orders)
        self.assertEqual(capacity.available_totes, 90)
        self.assertEqual(capacity.available_payload_lb, 2877.0)
        self.assertIsNone(capacity.available_volume_cuft)
        result = optimize_flights(tote_result.totes, orders, [capacity], stage="stage1")
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.total_flights, 1)
        self.assertEqual(len(result.assigned_tote_ids), 9)
        self.assertEqual(result.unassigned_tote_ids, [])
        self.assertEqual(len(result.complete_order_ids), 30)
        metric = result.per_flight_metrics[0]
        self.assertLessEqual(metric.used_payload_lb, capacity.available_payload_lb)
        self.assertIsNone(metric.remaining_volume_cuft)
        self.assertIsNone(metric.volume_utilization_percent)
        self.assertAlmostEqual(metric.used_volume_cuft, 16.8231, places=4)
        self.assertEqual(len(metric.complete_order_ids), 30)

    def test_tote_count_capacity_rejection(self):
        orders = [make_order(1), make_order(2)]
        totes = [Tote(f"T{i}", [ToteItem(order.items[0])]) for i, order in enumerate(orders)]
        result = optimize_flights(
            totes,
            orders,
            one_flight(FlightCapacity(1, date(2026, 6, 4), 1, 100.0, 10.0)),
        )
        self.assertFalse(result.feasible)
        self.assertTrue(result.unassigned_tote_ids)
        self.assertTrue(any("tote_count" in warning for warning in result.warnings))

    def test_payload_capacity_rejection(self):
        orders = [make_order(3, 2)]
        totes = [Tote(f"P{i}", [ToteItem(item)]) for i, item in enumerate(orders[0].items)]
        result = optimize_flights(
            totes,
            orders,
            one_flight(FlightCapacity(1, date(2026, 6, 4), 5, 3.0, 10.0)),
        )
        self.assertFalse(result.feasible)
        self.assertTrue(any("payload" in warning for warning in result.warnings))

    def test_volume_capacity_rejection(self):
        orders = [make_order(4, 2)]
        totes = [Tote(f"V{i}", [ToteItem(item)]) for i, item in enumerate(orders[0].items)]
        result = optimize_flights(
            totes,
            orders,
            one_flight(FlightCapacity(1, date(2026, 6, 4), 5, 100.0, 0.1)),
        )
        self.assertFalse(result.feasible)
        self.assertTrue(any("volume" in warning for warning in result.warnings))

    def test_stage2_capacities_and_all_totes_have_unique_flight(self):
        orders = load_stage2_orders()
        totes = optimize_totes(orders).totes
        capacities = load_stage2_flight_capacities()
        self.assertEqual(
            [(c.available_totes, c.available_payload_lb, c.available_volume_cuft) for c in capacities],
            [(22, 703.0, 45.83), (36, 1151.0, 75.0), (90, 2877.0, 187.5)],
        )
        result = optimize_flights(totes, orders, capacities)
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(len(result.assigned_tote_ids), 38)
        self.assertEqual(len(set(result.assigned_tote_ids)), 38)
        self.assertEqual(result.unassigned_tote_ids, [])
        for plan in result.flight_plans:
            self.assertLessEqual(plan.tote_count, plan.capacity.available_totes)
            self.assertLessEqual(plan.total_weight_lb, plan.capacity.available_payload_lb)
            self.assertLessEqual(plan.total_volume_cuft, plan.capacity.available_volume_cuft)
        assigned = [tote.tote_id for plan in result.flight_plans for tote in plan.totes]
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_deterministic_with_fresh_stage2_inputs(self):
        orders1, orders2 = load_stage2_orders(), load_stage2_orders()
        totes1, totes2 = optimize_totes(orders1).totes, optimize_totes(orders2).totes
        capacities1, capacities2 = load_stage2_flight_capacities(), load_stage2_flight_capacities()
        first = optimize_flights(totes1, orders1, capacities1)
        second = optimize_flights(totes2, orders2, capacities2)
        assignments1 = [(plan.flight_id, sorted(t.tote_id for t in plan.totes)) for plan in first.flight_plans]
        assignments2 = [(plan.flight_id, sorted(t.tote_id for t in plan.totes)) for plan in second.flight_plans]
        self.assertEqual(assignments1, assignments2)
        self.assertEqual(first.complete_order_ids, second.complete_order_ids)
        self.assertEqual(first.rollover_order_ids, second.rollover_order_ids)

    def test_rollover_when_order_group_does_not_fit_early_departure(self):
        order = make_order(50, 2, order_date=date(2026, 6, 3))
        totes = [Tote(f"R{i}", [ToteItem(item)]) for i, item in enumerate(order.items)]
        capacities = [
            FlightCapacity(1, date(2026, 6, 4), 1, 100.0, 10.0),
            FlightCapacity(2, date(2026, 6, 5), 2, 100.0, 10.0),
        ]
        result = optimize_flights(totes, [order], capacities)
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.flight_plans[0].totes, ())
        self.assertEqual([t.tote_id for t in result.flight_plans[1].totes], ["R0", "R1"])
        self.assertEqual(result.flight_plans[0].rolled_over_order_ids, [50])
        self.assertEqual(result.complete_order_ids, [50])
        self.assertEqual(result.rollover_order_ids, [])

    def test_multiple_orders_in_one_tote_are_traced_as_one_physical_unit(self):
        first, second = make_order(60), make_order(61)
        tote = Tote("shared", [ToteItem(first.items[0]), ToteItem(second.items[0])])
        capacity = FlightCapacity(1, date(2026, 6, 4), 1, 10.0, 10.0)
        result = optimize_flights([tote], [first, second], [capacity])
        self.assertTrue(result.feasible, result.errors)
        self.assertEqual(result.flight_plans[0].tote_count, 1)
        self.assertEqual(result.flight_plans[0].order_ids, [60, 61])
        self.assertEqual(result.complete_order_ids, [60, 61])

    def test_split_order_is_never_marked_complete_before_all_its_totes_load(self):
        order = make_order(70, 2)
        totes = [Tote(f"S{i}", [ToteItem(item)]) for i, item in enumerate(order.items)]
        result = optimize_flights(
            totes,
            [order],
            [FlightCapacity(1, date(2026, 6, 4), 1, 100.0, 10.0)],
        )
        self.assertFalse(result.feasible)
        self.assertNotIn(70, result.complete_order_ids)
        self.assertIn(70, result.rollover_order_ids)
        self.assertEqual(len({t.tote_id for p in result.flight_plans for t in p.totes}), 0)


if __name__ == "__main__":
    unittest.main()
