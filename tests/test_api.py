"""End-to-end tests for the FastAPI integration over challenge CSV data."""
from __future__ import annotations
import unittest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.app_state import initialize_state


class WildernessNorthAPITests(unittest.TestCase):
    def setUp(self):
        self.state = initialize_state()
        self.app = create_app(state=self.state)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def test_health(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_session_and_stage_selection_reset_planning(self):
        response = self.client.get('/api/session')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['active_stage'], 'stage1')
        changed = self.client.post('/api/session/stage', json={'stage': 'stage2'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()['active_stage'], 'stage2')
        self.assertEqual(changed.json()['order_count'], 120)
        self.client.post('/api/picking/optimize-totes', json={})
        changed = self.client.post('/api/session/stage', json={'stage': 'stage1'})
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.json()['picking_completed'])
        self.assertEqual(changed.json()['tote_count'], 0)

    def test_invalid_stage_is_400(self):
        self.assertEqual(self.client.post('/api/session/stage', json={'stage': 'bad'}).status_code, 400)
        self.assertEqual(self.client.get('/api/orders?stage=bad').status_code, 400)

    def test_order_list_filters_and_real_order(self):
        response = self.client.get('/api/orders?stage=stage1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 30)
        first = response.json()[0]
        self.assertEqual(first['order_date'], '2026-06-01')
        fetched = self.client.get(f"/api/orders/{first['order_id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()['order_id'], first['order_id'])
        self.assertEqual(self.client.get('/api/orders/-1').status_code, 404)

    def test_orders_filter_by_date_and_id(self):
        first_id = self.state.stage1_orders[0].order_id
        by_id = self.client.get(f'/api/orders?order_id={first_id}')
        self.assertEqual(len(by_id.json()), 1)
        by_date = self.client.get('/api/orders?order_date=2026-06-01')
        self.assertEqual(len(by_date.json()), 30)

    def test_totes_require_picking_then_stage1_optimize(self):
        self.assertEqual(self.client.get('/api/picking/totes').status_code, 409)
        response = self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total_totes'], 9)
        self.assertEqual(len(self.client.get('/api/picking/totes').json()), 9)

    def test_carts_empty_until_optimization_and_stage1_cart_result(self):
        self.assertEqual(self.client.get('/api/picking/carts').json(), [])
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        response = self.client.post('/api/picking/optimize-carts', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['feasible'])
        self.assertEqual(response.json()['total_carts'], 2)
        self.assertEqual(len(self.client.get('/api/picking/carts').json()), 2)

    def test_stage1_flights(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        response = self.client.post('/api/flights/optimize', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['feasible'])
        self.assertEqual(response.json()['total_flights'], 1)
        self.assertEqual(len(response.json()['complete_order_ids']), 30)
        self.assertEqual(len(self.client.get('/api/flights').json()), 1)
        stage1_capacity = response.json()['flight_plans'][0]['capacity']
        self.assertEqual(stage1_capacity['available_totes'], 90)
        self.assertEqual(stage1_capacity['available_payload_lb'], 2877.0)
        self.assertIsNone(stage1_capacity['available_volume_cuft'])
        self.assertIsNone(response.json()['per_flight_metrics'][0]['volume_utilization_percent'])
        canonical_ids = [id(tote) for tote in self.state.totes]
        rerun = self.client.post('/api/flights/optimize', json={})
        self.assertEqual(rerun.status_code, 200, rerun.text)
        self.assertEqual([id(tote) for tote in self.state.totes], canonical_ids)

    def test_stage2_order_summary_uses_source_data(self):
        summary = self.client.get('/api/orders/summary?stage=stage2')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()['order_count'], 120)
        self.assertEqual(summary.json()['household_count'], 114)
        self.assertEqual(summary.json()['date_min'], '2026-06-02')
        self.assertEqual(summary.json()['date_max'], '2026-06-05')
        self.assertEqual(len(summary.json()['oversized_order_ids']), 4)

    def test_stage2_totes_cart_infeasibility_and_flights(self):
        tote_response = self.client.post('/api/picking/optimize-totes', json={'stage': 'stage2'})
        self.assertEqual(tote_response.status_code, 200, tote_response.text)
        self.assertEqual(tote_response.json()['total_totes'], 38)
        carts = self.client.post('/api/picking/optimize-carts', json={})
        self.assertEqual(carts.status_code, 200, carts.text)
        self.assertFalse(carts.json()['feasible'])
        flights = self.client.post('/api/flights/optimize', json={'stage': 'stage2'})
        self.assertEqual(flights.status_code, 200, flights.text)
        self.assertTrue(flights.json()['feasible'])
        self.assertEqual(len(flights.json()['assigned_tote_ids']), 38)
        self.assertEqual(len(flights.json()['complete_order_ids']), 120)
        stage2_limits = [(p['capacity']['available_totes'], p['capacity']['available_payload_lb'], p['capacity']['available_volume_cuft']) for p in flights.json()['flight_plans']]
        self.assertEqual(stage2_limits, [(22, 703.0, 45.83), (36, 1151.0, 75.0), (90, 2877.0, 187.5)])

    def test_flight_capacities(self):
        response = self.client.get('/api/flights/capacities')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(c['available_totes'], c['available_payload_lb'], c['available_volume_cuft']) for c in response.json()],
                         [(22, 703.0, 45.83), (36, 1151.0, 75.0), (90, 2877.0, 187.5)])

    def test_flight_capacity_endpoint_selects_stage_specific_source(self):
        stage1 = self.client.get('/api/flights/capacities?stage=stage1')
        self.assertEqual(stage1.status_code, 200)
        self.assertEqual(len(stage1.json()), 1)
        self.assertEqual(stage1.json()[0]['available_totes'], 90)
        self.assertEqual(stage1.json()[0]['available_payload_lb'], 2877.0)
        self.assertIsNone(stage1.json()[0]['available_volume_cuft'])
        stage2 = self.client.get('/api/flights/capacities?stage=stage2')
        self.assertEqual(len(stage2.json()), 3)
        self.assertEqual(self.client.get('/api/flights/capacities?stage=bad').status_code, 400)

    def test_flight_detail_and_summary(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage2'})
        self.client.post('/api/flights/optimize', json={})
        summary = self.client.get('/api/flights/summary')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()['assigned_totes'], 38)
        flight_id = self.state.flight_plans[0].flight_id
        detail = self.client.get(f'/api/flights/{flight_id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('capacity', detail.json())
        self.assertEqual(self.client.get('/api/flights/999').status_code, 404)

    def test_reset(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage2'})
        response = self.client.post('/api/session/reset')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['active_stage'], 'stage1')
        self.assertFalse(response.json()['picking_completed'])
        self.assertEqual(response.json()['tote_count'], 0)

    def test_canonical_totes_are_shared_by_cart_and_flight_plans(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        self.client.post('/api/picking/optimize-carts', json={})
        self.client.post('/api/flights/optimize', json={})
        for tote in self.state.totes:
            self.assertTrue(any(tote is member for cart in self.state.carts for member in cart.totes))
            self.assertTrue(any(tote is member for plan in self.state.flight_plans for member in plan.totes))
            self.assertIsNotNone(tote.cart_id)
            self.assertIsNotNone(tote.flight_id)

    def test_manual_cart_assignment_and_removal(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        self.client.post('/api/picking/optimize-carts', json={})
        source = next(t for t in self.state.totes if t.cart_id == 'C001')
        self.assertEqual(self.client.post(f'/api/picking/totes/{source.tote_id}/remove-cart').status_code, 200)
        self.assertFalse(self.client.get('/api/picking/summary').json()['cart_feasible'])
        assigned = self.client.post(f'/api/picking/totes/{source.tote_id}/cart', json={'cart_id': 'C001'})
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()['tote']['cart_id'], 'C001')
        self.assertTrue(self.client.get('/api/picking/summary').json()['cart_feasible'])
        self.assertEqual(self.client.post('/api/picking/totes/unknown/cart', json={'cart_id': 'C002'}).status_code, 404)
        self.assertEqual(self.client.post(f'/api/picking/totes/{source.tote_id}/cart', json={'cart_id': 'missing'}).status_code, 404)

    def test_manual_flight_assignment_capacity_conflict(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        self.client.post('/api/flights/optimize', json={})
        plan = self.state.flight_plans[0]
        plan.capacity.available_totes = plan.tote_count - 1
        tote = plan.totes[0]
        response = self.client.post(f'/api/flights/totes/{tote.tote_id}/flight', json={'flight_id': plan.flight_id})
        self.assertEqual(response.status_code, 409)
        self.assertIn('tote_count', response.json()['detail']['exceeded_constraints'])

    def test_manual_flight_assignment_and_removal(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        self.client.post('/api/flights/optimize', json={})
        plan = self.state.flight_plans[0]
        tote = plan.totes[0]
        response = self.client.post(f'/api/flights/totes/{tote.tote_id}/remove-flight')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['flight_id'])
        response = self.client.post(f'/api/flights/totes/{tote.tote_id}/flight', json={'flight_id': plan.flight_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tote']['flight_id'], plan.flight_id)

    def test_bad_request_bodies_get_validation_response(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        response = self.client.post('/api/picking/optimize-carts', json={'totes_per_cart': 0})
        self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()
