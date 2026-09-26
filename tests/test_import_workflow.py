"""Upload detection, validation, commit, and canonical planning workflow tests."""
from __future__ import annotations

import base64
import io
import unittest

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend import config
from backend.app_state import initialize_state
from backend.main import create_app
from backend.services.import_service import analyze_upload, preview_draft, safe_filename

ORDERS = """Order Number,Customer ID,Date,Location,SKU,Description,Weight (lb),Length (in),Width (in),Height (in)
81001,7001,2026-09-20,Webequie,SKU-1,Rice,2.5,10,8,4
81002,7001,2026-09-20,Webequie,SKU-2,Beans,1.5,7,6,3
"""
CAPACITIES = """Flight No,Departure Date,Maximum Totes,Payload Capacity (lb),Cargo Volume (ft3)
F901,2026-09-21,10,100,20
"""


def encoded(filename: str, content: str | bytes) -> dict[str, str]:
    raw = content.encode() if isinstance(content, str) else content
    return {"filename": filename, "content_base64": base64.b64encode(raw).decode("ascii")}


class ImportServiceTests(unittest.TestCase):
    def test_detects_and_validates_order_file_with_synonyms(self):
        draft = analyze_upload("orders.csv", ORDERS.encode())
        self.assertEqual(draft.dataset_type, "orders")
        mapping = __import__("backend.services.import_service", fromlist=["detect_type"]).detect_type(draft.headers)[3]
        result = preview_draft(draft, "orders", mapping)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["valid_row_count"], 2)

    def test_detects_capacity_file_and_keeps_volume_unknown_when_blank(self):
        draft = analyze_upload("aircraft.csv", CAPACITIES.replace("20", "Unknown").encode())
        self.assertEqual(draft.dataset_type, "flight_capacity")
        self.assertEqual(draft.confidence, "high")

    def test_workbook_parsing_and_formula_non_execution(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Order ID", "Household ID", "Order Date", "Destination", "Weight", "Length", "Width", "Height"])
        sheet.append([8, 9, "2026-09-20", "Webequie", 1, 2, 3, 4])
        stream = io.BytesIO()
        workbook.save(stream)
        draft = analyze_upload("orders.xlsx", stream.getvalue())
        self.assertEqual(draft.dataset_type, "orders")
        self.assertEqual(len(draft.rows), 1)


    def test_unit_mismatch_is_not_auto_mapped(self):
        from backend.services.import_service import _suggest_mapping
        headers = ["order_id", "household_id", "order_date", "destination", "weight_kg", "length_cm", "width_cm", "height_cm"]
        mapping, _signals = _suggest_mapping(headers, "orders")
        self.assertIsNone(mapping["weight_lb"])
        self.assertIsNone(mapping["length_in"])
        self.assertIsNone(mapping["width_in"])
        self.assertIsNone(mapping["height_in"])

    def test_file_security_rejects_unsupported_macro_extension(self):
        with self.assertRaises(ValueError):
            safe_filename("bad.xlsm")

    def test_numeric_grouping_is_unambiguous(self):
        draft = analyze_upload("bad.csv", b"order_id,household_id,order_date,destination_community,weight_lb,length_in,width_in,height_in\n1,2,2026-09-20,W,1,1,1,1\n")
        from backend.services.import_service import detect_type
        mapping = detect_type(draft.headers)[3]
        draft.rows[0]["weight_lb"] = "1,2"
        preview = preview_draft(draft, "orders", mapping)
        self.assertFalse(preview["valid"])
        self.assertIn("must be numeric", " ".join(preview["invalid_rows"][0]["issues"]))


class ImportValidationEdgeTests(unittest.TestCase):
    def test_unknown_file_stays_unknown_even_when_filename_says_orders(self):
        draft = analyze_upload("orders.csv", b"alpha,beta\nx,y\n")
        self.assertEqual(draft.dataset_type, "unknown")

    def test_filename_and_sample_are_reported_as_supporting_detection_signals(self):
        draft = analyze_upload("household-orders.csv", ORDERS.encode())
        self.assertEqual(draft.dataset_type, "orders")
        self.assertTrue(any("filename" in signal for signal in draft.signals))
        self.assertTrue(any("sample rows" in signal for signal in draft.signals))

    def test_missing_required_mapping_is_rejected(self):
        draft = analyze_upload("orders.csv", b"Order ID,Date\n1,2026-09-20\n")
        result = preview_draft(draft, "orders", {"order_id": "Order ID"})
        self.assertFalse(result["valid"])
        self.assertTrue(any("required field" in issue for issue in result["mapping_errors"]))

    def test_invalid_date_is_reported_on_its_source_row(self):
        source = ORDERS.replace("2026-09-20", "not-a-date", 1)
        draft = analyze_upload("orders.csv", source.encode())
        from backend.services.import_service import detect_type
        mapping = detect_type(draft.headers, filename=draft.filename, rows=draft.rows)[3]
        result = preview_draft(draft, "orders", mapping)
        self.assertFalse(result["valid"])
        self.assertEqual(result["invalid_rows"][0]["row_number"], 2)
        self.assertIn("date", " ".join(result["invalid_rows"][0]["issues"]).lower())

    def test_duplicate_departure_ids_are_rejected(self):
        source = CAPACITIES + "F901,2026-09-22,10,100,20\n"
        draft = analyze_upload("flights.csv", source.encode())
        from backend.services.import_service import detect_type
        mapping = detect_type(draft.headers, filename=draft.filename, rows=draft.rows)[3]
        result = preview_draft(draft, "flight_capacity", mapping)
        self.assertFalse(result["valid"])
        self.assertEqual(result["invalid_rows"][0]["row_number"], 3)
        self.assertIn("Duplicate", " ".join(result["invalid_rows"][0]["issues"]))


class ImportWorkflowAPITests(unittest.TestCase):
    def setUp(self):
        self.state = initialize_state()
        self.client = TestClient(create_app(state=self.state))

    def tearDown(self):
        self.client.close()

    def analyze(self, filename: str, content: str):
        response = self.client.post("/api/import/analyze", json={"files": [encoded(filename, content)]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()[0]

    def preview_and_commit(self, analyzed: dict, dataset_type: str, mapping: dict | None = None, excluded: list[int] | None = None):
        if mapping is None:
            mapping = analyzed["suggested_mapping"]
        preview = self.client.post("/api/import/preview", json={"draft_id": analyzed["draft_id"], "dataset_type": dataset_type, "mapping": mapping, "excluded_row_numbers": excluded or []})
        self.assertEqual(preview.status_code, 200, preview.text)
        return preview.json()

    def test_analyze_is_non_mutating_and_commit_replaces_only_user_data(self):
        before = self.client.get("/api/session").json()
        analyzed = self.analyze("my-orders.csv", ORDERS)
        self.assertEqual(analyzed["dataset_type"], "orders")
        self.assertEqual(self.client.get("/api/session").json(), before)
        preview = self.preview_and_commit(analyzed, "orders")
        self.assertTrue(preview["valid"], preview)
        committed = self.client.post("/api/import/commit", json={"files": [{"draft_id": analyzed["draft_id"], "dataset_type": "orders", "mapping": analyzed["suggested_mapping"], "excluded_row_numbers": []}]})
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()["order_count"], 2)
        self.assertEqual(committed.json()["household_count"], 1)
        session = self.client.get("/api/session").json()
        self.assertEqual(session["active_mode"], "user")
        self.assertFalse(session["picking_completed"])
        orders = self.client.get("/api/orders").json()
        self.assertEqual([order["order_id"] for order in orders], [81001, 81002])
        self.assertEqual(orders[0]["items"][0]["product_name"], "Rice")
        self.assertEqual(orders[0]["items"][0]["product_id"], "SKU-1")
        # Challenge source remains unchanged and can be selected independently.
        challenge = self.client.post("/api/session/mode", json={"mode": "challenge"}).json()
        self.assertEqual(challenge["order_count"], 30)
        self.assertEqual(self.client.get("/api/orders?stage=stage2").json()[0]["order_id"], self.state.stage2_orders[0].order_id)


    def test_aggregated_item_quantity_is_validated_and_counted_in_weight_and_volume(self):
        source = "Order ID,Household ID,Order Date,Destination,Product ID,Product Name,Quantity,Weight (lb),Length (in),Width (in),Height (in)\n44,55,2026-09-20,Webequie,0007,Apple,3,2,2,3,4\n"
        analyzed = self.analyze("quantity.csv", source)
        mapping = analyzed["suggested_mapping"]
        preview = self.preview_and_commit(analyzed, "orders", mapping)
        self.assertTrue(preview["valid"], preview)
        response = self.client.post("/api/import/commit", json={"files": [{"draft_id": analyzed["draft_id"], "dataset_type": "orders", "mapping": mapping, "excluded_row_numbers": []}]})
        self.assertEqual(response.status_code, 200, response.text)
        order = self.client.get("/api/orders/44").json()
        self.assertEqual(order["items"][0]["quantity"], 3)
        self.assertEqual(order["items"][0]["product_id"], "0007")
        self.assertEqual(order["total_weight_lb"], 6)
        self.assertEqual(order["total_volume_ft3"], 72 / 1728)


    def test_cancel_discards_draft_without_changing_active_dataset(self):
        before = self.client.get("/api/session").json()
        analyzed = self.analyze("cancel.csv", ORDERS)
        deleted = self.client.delete(f"/api/import/drafts/{analyzed['draft_id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/session").json(), before)
        preview = self.client.post("/api/import/preview", json={"draft_id": analyzed["draft_id"], "dataset_type": "orders", "mapping": analyzed["suggested_mapping"]})
        self.assertEqual(preview.status_code, 404)

    def test_invalid_rows_require_explicit_exclusion_and_invalid_commit_is_rejected(self):
        content = ORDERS.replace("81002,7001,2026-09-20,Webequie,SKU-2,Beans,1.5", "81002,7001,2026-09-20,Webequie,SKU-2,Beans,broken")
        analyzed = self.analyze("partly-bad.csv", content)
        mapping = analyzed["suggested_mapping"]
        invalid_preview = self.preview_and_commit(analyzed, "orders", mapping)
        self.assertFalse(invalid_preview["valid"])
        self.assertEqual(invalid_preview["invalid_rows"][0]["row_number"], 3)
        rejected = self.client.post("/api/import/commit", json={"files": [{"draft_id": analyzed["draft_id"], "dataset_type": "orders", "mapping": mapping, "excluded_row_numbers": []}]})
        self.assertEqual(rejected.status_code, 422)
        excluded_preview = self.preview_and_commit(analyzed, "orders", mapping, [3])
        self.assertTrue(excluded_preview["valid"], excluded_preview)
        committed = self.client.post("/api/import/commit", json={"files": [{"draft_id": analyzed["draft_id"], "dataset_type": "orders", "mapping": mapping, "excluded_row_numbers": [3]}]})
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()["order_count"], 1)

    def test_user_capacity_is_used_without_reusing_challenge_capacities(self):
        order_file = self.analyze("orders.csv", ORDERS)
        capacity_file = self.analyze("capacity.csv", CAPACITIES)
        entries = [
            {"draft_id": order_file["draft_id"], "dataset_type": "orders", "mapping": order_file["suggested_mapping"], "excluded_row_numbers": []},
            {"draft_id": capacity_file["draft_id"], "dataset_type": "flight_capacity", "mapping": capacity_file["suggested_mapping"], "excluded_row_numbers": []},
        ]
        committed = self.client.post("/api/import/commit", json={"files": entries})
        self.assertEqual(committed.status_code, 200, committed.text)
        caps = self.client.get("/api/flights/capacities?stage=stage2").json()
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["departure_id"], 901)
        self.assertEqual(caps[0]["available_volume_cuft"], 20.0)
        self.assertEqual(len(self.state.flight_capacities), 3)
        self.assertEqual(self.client.post("/api/picking/optimize-totes", json={}).status_code, 200)
        carts = self.client.post("/api/picking/optimize-carts", json={})
        self.assertEqual(carts.status_code, 200, carts.text)
        flights = self.client.post("/api/flights/optimize", json={})
        self.assertEqual(flights.status_code, 200, flights.text)
        self.assertTrue(flights.json()["feasible"], flights.text)
        self.assertEqual(flights.json()["complete_order_ids"], [81001, 81002])
        plan_totes = [t.tote_id for t in self.state.flight_plans[0].totes]
        cost = self.client.patch("/api/flights/901/cost", json={"flight_cost": 800, "currency": "CAD"})
        self.assertEqual(cost.status_code, 200, cost.text)
        self.assertEqual(cost.json()["completed_order_count"], 2)
        self.assertEqual(cost.json()["cost_per_order"], 400)
        manifest = self.client.get("/api/flights/901").json()
        self.assertEqual([t["tote_id"] for t in manifest["manifest_totes"]], sorted(plan_totes))
        self.assertEqual(manifest["flight_cost"], 800)
        self.assertEqual(manifest["cost_per_order"], 400)

    def test_stage1_and_stage2_challenge_capacities_remain_isolated(self):
        self.assertEqual(len(self.client.get("/api/flights/capacities?stage=stage1").json()), 1)
        self.assertEqual(len(self.client.get("/api/flights/capacities?stage=stage2").json()), 3)
        capacity_file = self.analyze("capacity.csv", CAPACITIES)
        entry = {"draft_id": capacity_file["draft_id"], "dataset_type": "flight_capacity", "mapping": capacity_file["suggested_mapping"], "excluded_row_numbers": []}
        self.assertEqual(self.client.post("/api/import/commit", json={"files": [entry]}).status_code, 200)
        self.client.post("/api/session/mode", json={"mode": "challenge"})
        self.assertEqual(len(self.client.get("/api/flights/capacities?stage=stage1").json()), 1)
        self.assertEqual(len(self.client.get("/api/flights/capacities?stage=stage2").json()), 3)


    def test_original_stage1_csv_can_be_uploaded_and_picked_without_changing_challenge_data(self):
        payload = encoded(config.STAGE1_ORDERS_FILE.name, config.STAGE1_ORDERS_FILE.read_bytes())
        analyzed = self.client.post("/api/import/analyze", json={"files": [payload]})
        self.assertEqual(analyzed.status_code, 200, analyzed.text)
        info = analyzed.json()[0]
        self.assertEqual(info["dataset_type"], "orders")
        mapping = info["suggested_mapping"]
        preview = self.client.post("/api/import/preview", json={"draft_id": info["draft_id"], "dataset_type": "orders", "mapping": mapping, "excluded_row_numbers": []})
        self.assertTrue(preview.json()["valid"], preview.text)
        commit = self.client.post("/api/import/commit", json={"files": [{"draft_id": info["draft_id"], "dataset_type": "orders", "mapping": mapping, "excluded_row_numbers": []}]})
        self.assertEqual(commit.status_code, 200, commit.text)
        self.assertEqual(commit.json()["order_count"], 30)
        picking = self.client.post("/api/picking/optimize-totes", json={})
        self.assertEqual(picking.status_code, 200, picking.text)
        self.assertEqual(picking.json()["total_totes"], 9)
        self.assertEqual(picking.json()["complete_orders"], 30)
        self.assertEqual(picking.json()["split_orders"], 0)
        self.client.post("/api/session/mode", json={"mode": "challenge"})
        self.assertEqual(self.client.get("/api/orders").json().__len__(), 30)

    def test_empty_user_capacity_is_not_silently_filled_from_challenge_data(self):
        analyzed = self.analyze("orders.csv", ORDERS)
        entry = {"draft_id": analyzed["draft_id"], "dataset_type": "orders", "mapping": analyzed["suggested_mapping"], "excluded_row_numbers": []}
        self.assertEqual(self.client.post("/api/import/commit", json={"files": [entry]}).status_code, 200)
        self.assertEqual(self.client.get("/api/flights/capacities?stage=stage1").json(), [])
        self.client.post("/api/picking/optimize-totes", json={})
        response = self.client.post("/api/flights/optimize", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total_flights"], 0)


    def test_import_replacement_clears_tote_cart_and_flight_plans(self):
        order_file = self.analyze("orders.csv", ORDERS)
        capacity_file = self.analyze("flights.csv", CAPACITIES)
        entries = [
            {"draft_id": order_file["draft_id"], "dataset_type": "orders", "mapping": order_file["suggested_mapping"], "excluded_row_numbers": []},
            {"draft_id": capacity_file["draft_id"], "dataset_type": "flight_capacity", "mapping": capacity_file["suggested_mapping"], "excluded_row_numbers": []},
        ]
        committed = self.client.post("/api/import/commit", json={"files": entries})
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(self.client.post("/api/picking/optimize-totes", json={}).status_code, 200)
        self.assertEqual(self.client.post("/api/picking/optimize-carts", json={}).status_code, 200)
        flight_result = self.client.post("/api/flights/optimize", json={})
        self.assertEqual(flight_result.status_code, 200)
        source_order = self.client.get("/api/orders/81001").json()
        source_item = source_order["items"][0]
        item_tote = next(tote for tote in self.state.totes if any(line.order_item_id == source_item["order_item_id"] for line in tote.items))
        scanned = self.client.post("/api/picking/scan", json={
            "order_id": 81001, "tote_id": item_tote.tote_id,
            "identifier": source_item["order_item_id"], "quantity": 1,
        })
        self.assertEqual(scanned.status_code, 200, scanned.text)
        self.assertEqual(scanned.json()["outcome"], "quantity_complete")
        self.assertTrue(self.state.picked_quantities)
        before = self.client.get("/api/session").json()
        self.assertTrue(before["picking_completed"])
        self.assertTrue(before["cart_optimization_completed"])
        self.assertTrue(before["flight_optimization_completed"])

        replacement = self.analyze("replacement.csv", ORDERS.replace("81001", "82001").replace("81002", "82002"))
        replaced = self.client.post("/api/import/commit", json={"files": [{
            "draft_id": replacement["draft_id"], "dataset_type": "orders",
            "mapping": replacement["suggested_mapping"], "excluded_row_numbers": [],
        }]})
        self.assertEqual(replaced.status_code, 200, replaced.text)
        after = replaced.json()["session"]
        self.assertEqual(after["order_count"], 2)
        self.assertEqual(after["tote_count"], 0)
        self.assertEqual(after["cart_count"], 0)
        self.assertEqual(after["flight_count"], 0)
        self.assertFalse(after["picking_completed"])
        self.assertFalse(after["cart_optimization_completed"])
        self.assertFalse(after["flight_optimization_completed"])
        self.assertEqual(self.state.picked_quantities, {})


if __name__ == "__main__":
    unittest.main()
