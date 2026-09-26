"""Picking progress, scanner validation, and nested manifest tests."""
from __future__ import annotations

import unittest
from datetime import date

from fastapi.testclient import TestClient

from backend.app_state import PlanningState
from backend.main import create_app
from backend.models import HouseholdOrder, OrderItem


def make_order(order_id: int, household_id: int, *, quantity: int = 1, length: float = 2.0) -> HouseholdOrder:
    item = OrderItem(
        order_id=order_id, household_id=household_id, batch_id=1,
        order_date=date(2026, 6, 1), destination_community="Webequie",
        product_id="0007", product_name="Apples", length_in=length,
        width_in=10.0, height_in=10.0, weight_lb=1.25,
        order_item_id=f"workflow:{order_id}:apple", source_quantity=quantity,
    )
    return HouseholdOrder(order_id, household_id, 1, date(2026, 6, 1), "Webequie", [item])


class PickingWorkflowAPITests(unittest.TestCase):
    def setUp(self):
        orders = [make_order(501, 801, quantity=3)]
        state = PlanningState(stage1_orders=orders, stage2_orders=[], flight_capacities=[])
        self.state = state
        self.order = orders[0]
        self.item = orders[0].items[0]
        self.client = TestClient(create_app(state=state))

    def tearDown(self):
        self.client.close()

    def optimize(self):
        response = self.client.post('/api/picking/optimize-totes', json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def scan(self, identifier: str | None = None, *, order_id: int = 501, tote_id: str | None = None, quantity: int = 1):
        return self.client.post('/api/picking/scan', json={
            'order_id': order_id,
            'tote_id': tote_id or self.state.totes[0].tote_id,
            'identifier': identifier or self.item.order_item_id,
            'quantity': quantity,
        })

    def test_initial_item_is_not_picked_then_becomes_ready_after_allocation(self):
        progress = self.client.get('/api/picking/progress').json()['orders'][0]
        self.assertEqual(progress['picking_status'], 'ENTERED')
        self.assertEqual(progress['items'][0]['picking_status'], 'NOT_PICKED')
        self.optimize()
        progress = self.client.get('/api/picking/progress').json()['orders'][0]
        self.assertEqual(progress['picking_status'], 'READY_FOR_PICKING')
        self.assertEqual(progress['packing_status'], 'FULLY_ALLOCATED')

    def test_partial_and_complete_quantity_update_canonical_status(self):
        self.optimize()
        response = self.scan(quantity=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['outcome'], 'scanned')
        self.assertEqual(response.json()['progress']['picking_status'], 'IN_PROGRESS')
        self.assertEqual(response.json()['progress']['items'][0]['picking_status'], 'PARTIAL')
        self.assertEqual(self.client.get('/api/orders/501').json()['picking_status'], 'IN_PROGRESS')
        response = self.scan(quantity=2)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['outcome'], 'quantity_complete')
        self.assertEqual(response.json()['progress']['picking_status'], 'PICKED')
        self.assertEqual(response.json()['progress']['remaining_quantity'], 0)
        self.assertEqual(self.client.get('/api/orders/501').json()['picking_status'], 'PICKED')

    def test_picked_quantity_cannot_exceed_source_quantity(self):
        self.optimize()
        overshoot = self.scan(quantity=4)
        self.assertEqual(overshoot.status_code, 422)
        self.assertEqual(self.state.picked_quantities, {})
        self.assertEqual(self.scan(quantity=3).status_code, 200)
        already_complete = self.scan(quantity=1)
        self.assertEqual(already_complete.status_code, 200)
        self.assertEqual(already_complete.json()['outcome'], 'already_complete')
        self.assertEqual(self.state.picked_quantities[self.item.order_item_id], 3)

    def test_unknown_item_wrong_order_and_wrong_tote_feedback(self):
        self.optimize()
        self.assertEqual(self.scan('bad-sku').json()['outcome'], 'unknown_item')
        self.assertEqual(self.scan(order_id=9999).json()['outcome'], 'unknown_order')
        self.assertEqual(self.scan(tote_id='not-a-tote').json()['outcome'], 'wrong_tote')
        other_order = make_order(9998, 9999)
        other_item = other_order.items[0]
        other_item = OrderItem(
            order_id=9998, household_id=9999, batch_id=1, order_date=date(2026, 6, 1),
            destination_community='Webequie', product_id='UNIQUE-OTHER', product_name='Other',
            length_in=2.0, width_in=2.0, height_in=2.0, weight_lb=1.0,
            order_item_id='workflow:other:item',
        )
        other_order.items = [other_item]
        self.state.stage1_orders.append(other_order)
        self.assertEqual(self.scan('workflow:other:item').json()['outcome'], 'wrong_order')

    def test_stable_source_identity_and_string_product_id_survive_scan(self):
        self.optimize()
        order_response = self.client.get('/api/orders/501').json()
        tote_response = self.client.get('/api/picking/totes').json()[0]
        self.assertEqual(order_response['items'][0]['order_item_id'], self.item.order_item_id)
        self.assertEqual(order_response['items'][0]['product_id'], '0007')
        self.assertEqual(tote_response['items'][0]['order_item_id'], self.item.order_item_id)
        scanned = self.scan('0007')
        self.assertEqual(scanned.status_code, 200, scanned.text)
        self.assertEqual(scanned.json()['order_item_id'], self.item.order_item_id)

    def test_household_and_order_traceability_remains_intact(self):
        self.optimize()
        scanned = self.scan().json()
        self.assertEqual(scanned['progress']['household_id'], 801)
        tote_item = self.client.get('/api/picking/totes').json()[0]['items'][0]
        self.assertEqual((tote_item['order_id'], tote_item['household_id']), (501, 801))

    def test_split_source_item_remains_traced_across_totes(self):
        order = make_order(502, 802, quantity=3, length=19.0)
        state = PlanningState(stage1_orders=[order], stage2_orders=[], flight_capacities=[])
        client = TestClient(create_app(state=state))
        try:
            optimized = client.post('/api/picking/optimize-totes', json={}).json()
            self.assertEqual(optimized['total_totes'], 3)
            self.assertEqual(optimized['split_order_ids'], [502])
            progress = client.get('/api/picking/progress').json()['orders'][0]['items'][0]
            self.assertEqual(progress['order_item_id'], order.items[0].order_item_id)
            self.assertEqual(progress['allocated_quantity'], 3)
            self.assertEqual(len(progress['tote_ids']), 3)
            for tote in client.get('/api/picking/totes').json():
                self.assertEqual(tote['items'][0]['order_item_id'], order.items[0].order_item_id)
            flight_result = client.post('/api/flights/optimize', json={}).json()
            self.assertTrue(flight_result['feasible'], flight_result['errors'])
            manifest_totes = flight_result['flight_plans'][0]['manifest_totes']
            self.assertEqual(len(manifest_totes), 3)
            self.assertTrue(all(tote['households'][0]['orders'][0]['items'][0]['source_item_id'] == order.items[0].order_item_id for tote in manifest_totes))
            self.assertEqual(sum(tote['households'][0]['orders'][0]['items'][0]['quantity'] for tote in manifest_totes), 3)
        finally:
            client.close()

    def test_shared_tote_preserves_household_order_boundaries_in_manifest(self):
        first, second = make_order(601, 901), make_order(602, 902)
        state = PlanningState(stage1_orders=[first, second], stage2_orders=[], flight_capacities=[])
        client = TestClient(create_app(state=state))
        try:
            self.assertEqual(client.post('/api/picking/optimize-totes', json={}).status_code, 200)
            flights = client.post('/api/flights/optimize', json={}).json()
            self.assertTrue(flights['feasible'], flights['errors'])
            manifest = flights['flight_plans'][0]['manifest_totes']
            self.assertEqual(len(manifest), 1)
            self.assertEqual({household['household_id'] for household in manifest[0]['households']}, {901, 902})
            order_ids = {order['order_id'] for household in manifest[0]['households'] for order in household['orders']}
            self.assertEqual(order_ids, {601, 602})
            item = manifest[0]['households'][0]['orders'][0]['items'][0]
            self.assertEqual(item['source_item_id'], first.items[0].order_item_id)
            self.assertEqual(item['product_id'], '0007')
            self.assertEqual(item['quantity'], 1)
        finally:
            client.close()

    def test_manifest_reads_do_not_mutate_canonical_optimization_state(self):
        self.optimize()
        self.client.post('/api/flights/optimize', json={})
        tote_snapshot = [(id(tote), tote.cart_id, tote.flight_id) for tote in self.state.totes]
        plan_snapshot = [(plan.flight_id, tuple(t.tote_id for t in plan.totes)) for plan in self.state.flight_plans]
        first = self.client.get('/api/flights').json()
        second = self.client.get('/api/flights').json()
        self.assertEqual(first, second)
        self.assertEqual(tote_snapshot, [(id(tote), tote.cart_id, tote.flight_id) for tote in self.state.totes])
        self.assertEqual(plan_snapshot, [(plan.flight_id, tuple(t.tote_id for t in plan.totes)) for plan in self.state.flight_plans])

    def test_picked_state_survives_tote_and_flight_plan_changes_but_reset_clears_it(self):
        self.optimize()
        self.assertEqual(self.scan(quantity=1).status_code, 200)
        self.assertEqual(self.client.post('/api/picking/optimize-totes', json={}).status_code, 200)
        self.assertEqual(self.client.get('/api/picking/progress').json()['orders'][0]['picked_quantity'], 1)
        self.assertEqual(self.client.post('/api/flights/optimize', json={}).status_code, 200)
        self.assertEqual(self.client.get('/api/picking/progress').json()['orders'][0]['picked_quantity'], 1)
        reset = self.client.post('/api/session/reset')
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.state.picked_quantities, {})


if __name__ == '__main__':
    unittest.main()
