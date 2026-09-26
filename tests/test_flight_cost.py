"""Operator-provided per-flight cost API and manifest coverage."""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app_state import initialize_state
from backend.main import create_app
from backend.services.import_service import analyze_upload, detect_type, normalize_capacities
from backend.api.schemas import FlightCostUpdateRequest
from pydantic import ValidationError


class FlightCostAPITests(unittest.TestCase):
    def setUp(self):
        self.state = initialize_state()
        self.client = TestClient(create_app(state=self.state))

    def tearDown(self):
        self.client.close()

    def test_stage1_cost_per_unique_completed_order_and_no_plan_mutation(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        optimized = self.client.post('/api/flights/optimize', json={}).json()
        self.assertTrue(optimized['feasible'])
        plan = self.state.flight_plans[0]
        before_tote_ids = [t.tote_id for t in plan.totes]
        before_assignments = [(t.tote_id, t.flight_id, t.cart_id) for t in self.state.totes]
        before_loaded = dict(plan.loaded_item_quantities)

        response = self.client.patch('/api/flights/1/cost', json={'flight_cost': 1250, 'currency': 'CAD'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['completed_order_count'], 30)
        self.assertAlmostEqual(body['cost_per_order'], 1250 / 30)
        self.assertEqual(body['currency'], 'CAD')
        self.assertEqual([t.tote_id for t in plan.totes], before_tote_ids)
        self.assertEqual([(t.tote_id, t.flight_id, t.cart_id) for t in self.state.totes], before_assignments)
        self.assertEqual(plan.loaded_item_quantities, before_loaded)

        flight = self.client.get('/api/flights/1').json()
        self.assertEqual(flight['flight_cost'], 1250)
        self.assertEqual(flight['completed_order_count'], 30)
        self.assertAlmostEqual(flight['cost_per_order'], 1250 / 30)
        self.assertEqual(len(flight['manifest_totes']), len(before_tote_ids))
        manifest_order_ids = {order['order_id'] for tote in flight['manifest_totes'] for household in tote['households'] for order in household['orders']}
        self.assertEqual(len(manifest_order_ids), 30)

    def test_unknown_cost_and_zero_completed_orders_reasons(self):
        saved = self.client.patch('/api/flights/1/cost', json={'flight_cost': None, 'currency': None})
        self.assertEqual(saved.status_code, 200)
        self.assertIsNone(saved.json()['cost_per_order'])
        self.assertEqual(saved.json()['cost_per_order_unknown_reason'], 'Flight cost not provided.')

        no_orders = initialize_state()
        no_orders.stage1_flight_capacities[0].flight_cost = 50
        client = TestClient(create_app(state=no_orders))
        try:
            result = client.patch('/api/flights/1/cost', json={'flight_cost': 50, 'currency': 'CAD'}).json()
            self.assertEqual(result['completed_order_count'], 0)
            self.assertIsNone(result['cost_per_order'])
            self.assertEqual(result['cost_per_order_unknown_reason'], 'No completed orders carried on this flight.')
        finally:
            client.close()

    def test_stage2_split_orders_are_counted_once_per_flight(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage2'})
        self.client.post('/api/flights/optimize', json={'stage': 'stage2'})
        flights = self.client.get('/api/flights').json()
        split_manifest = next(flight for flight in flights if flight['flight_id'] == 1)
        order_occurrences = [
            order['order_id']
            for tote in split_manifest['manifest_totes']
            for household in tote['households']
            for order in household['orders']
        ]
        self.assertGreater(len(order_occurrences), len(set(order_occurrences)))
        expected_count = len(set(order_occurrences) - set(split_manifest['rollover_order_ids']))
        saved = self.client.patch('/api/flights/1/cost', json={'flight_cost': 2200, 'currency': 'CAD'})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['completed_order_count'], expected_count)
        self.assertAlmostEqual(saved.json()['cost_per_order'], 2200 / expected_count)

    def test_validation_rejects_negative_nonfinite_and_invalid_currency(self):
        for payload in (
            {'flight_cost': -1, 'currency': 'CAD'},
            {'flight_cost': 10, 'currency': ''},
            {'flight_cost': 10, 'currency': 'Canadian Dollars'},
            {'flight_cost': 10, 'currency': None},
            {'flight_cost': True, 'currency': 'CAD'},
            {'flight_cost': '10', 'currency': 'CAD'},
        ):
            response = self.client.patch('/api/flights/1/cost', json=payload)
            self.assertEqual(response.status_code, 422, (payload, response.text))
        for bad_number in (float('inf'), float('-inf'), float('nan')):
            with self.assertRaises(ValidationError):
                FlightCostUpdateRequest(flight_cost=bad_number, currency='CAD')

    def test_zero_cost_and_alternate_currency_are_valid_and_rounding_is_display_only(self):
        self.client.post('/api/picking/optimize-totes', json={'stage': 'stage1'})
        self.client.post('/api/flights/optimize', json={})
        response = self.client.patch('/api/flights/1/cost', json={'flight_cost': 0, 'currency': 'usd'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['currency'], 'USD')
        self.assertEqual(response.json()['cost_per_order'], 0)
        self.assertEqual(self.client.get('/api/flights/1').json()['cost_per_order'], 0)

    def test_capacity_import_supports_cost_alias_and_excludes_cost_per_order(self):
        content = ('Flight No,Departure Date,Maximum Totes,Payload Capacity (lb),Flight Cost,Currency\n'
                   'F901,2026-09-21,10,100,1250.75,CAD\n')
        draft = analyze_upload('capacity.csv', content.encode())
        detected = detect_type(draft.headers, draft.filename, draft.rows)
        self.assertEqual(detected[0], 'flight_capacity')
        mapping = detected[3]
        self.assertEqual(mapping['flight_cost'], 'Flight Cost')
        self.assertEqual(mapping['currency'], 'Currency')
        capacities = normalize_capacities(draft, mapping, set())
        self.assertEqual(capacities[0].flight_cost, 1250.75)
        self.assertEqual(capacities[0].currency, 'CAD')

        headers = ['Flight No', 'Departure Date', 'Maximum Totes', 'Payload Capacity (lb)', 'Cost per Order']
        mapping = detect_type(headers, 'capacity.csv', [dict.fromkeys(headers, '1')])[3]
        self.assertIsNone(mapping['flight_cost'])

    def test_optional_imported_cost_defaults_currency_to_cad_but_blank_cost_stays_unknown(self):
        content = ('Flight No,Departure Date,Maximum Totes,Payload Capacity (lb),Cost\n'
                   'F901,2026-09-21,10,100,125\n')
        draft = analyze_upload('capacity.csv', content.encode())
        mapping = detect_type(draft.headers, draft.filename, draft.rows)[3]
        capacity = normalize_capacities(draft, mapping, set())[0]
        self.assertEqual(capacity.flight_cost, 125)
        self.assertEqual(capacity.currency, 'CAD')

        blank = content.replace(',125\n', ',\n')
        draft = analyze_upload('blank.csv', blank.encode())
        mapping = detect_type(draft.headers, draft.filename, draft.rows)[3]
        capacity = normalize_capacities(draft, mapping, set())[0]
        self.assertIsNone(capacity.flight_cost)
        self.assertIsNone(capacity.currency)


if __name__ == '__main__':
    unittest.main()
