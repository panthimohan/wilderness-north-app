"""Explicit API DTOs; domain dataclasses are never returned directly."""
from __future__ import annotations
from datetime import date
import math
import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

class HealthResponse(BaseModel):
    status: str = "ok"
class StageRequest(BaseModel):
    stage: str
class ModeRequest(BaseModel):
    mode: Literal["challenge", "user"]
class ImportFileInput(BaseModel):
    filename: str
    content_base64: str
class ImportAnalyzeRequest(BaseModel):
    files: list[ImportFileInput] = Field(min_length=1, max_length=5)
class ImportPreviewRequest(BaseModel):
    draft_id: str
    dataset_type: Literal["orders", "flight_capacity"]
    mapping: dict[str, str | None]
    excluded_row_numbers: list[int] = []
class ImportCommitFile(BaseModel):
    draft_id: str
    dataset_type: Literal["orders", "flight_capacity"]
    mapping: dict[str, str | None]
    excluded_row_numbers: list[int] = []
class ImportCommitRequest(BaseModel):
    files: list[ImportCommitFile] = Field(min_length=1, max_length=5)
class ImportAnalysisFileResponse(BaseModel):
    draft_id: str
    filename: str
    dataset_type: str
    confidence: str
    signals: list[str]
    headers: list[str]
    rows: list[dict[str, str]]
    suggested_mapping: dict[str, str | None]
    parse_error: str | None = None
    note: str | None = None
class ImportCommitResponse(BaseModel):
    mode: Literal["user"]
    order_count: int
    household_count: int
    flight_capacity_count: int
    imported_files: list[str]
    session: dict
class OptimizeTotesRequest(BaseModel):
    stage: str | None = None
class OptimizeCartsRequest(BaseModel):
    totes_per_cart: int | None = Field(default=None, gt=0)
class OptimizeFlightsRequest(BaseModel):
    stage: str | None = None
class CartAssignmentRequest(BaseModel):
    cart_id: str
class FlightAssignmentRequest(BaseModel):
    flight_id: int

class FlightCostUpdateRequest(BaseModel):
    flight_cost: float | None
    currency: str | None = "CAD"

    @field_validator("flight_cost", mode="before")
    @classmethod
    def validate_flight_cost(cls, value):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("flight_cost must be a finite non-negative number or null")
        if not math.isfinite(value) or value < 0:
            raise ValueError("flight_cost must be a finite non-negative number or null")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("currency must be a non-empty three-letter currency code")
        return normalized

    @model_validator(mode="after")
    def require_currency_for_cost(self):
        if self.flight_cost is not None and self.currency is None:
            raise ValueError("currency is required when flight_cost is provided")
        return self

class SessionResponse(BaseModel):
    active_stage: Literal["stage1", "stage2"]
    active_mode: Literal["challenge", "user"] = "challenge"
    active_dataset_name: str | None = None
    active_dataset_source: str | None = None
    user_imported_at: str | None = None
    order_count: int
    tote_count: int
    cart_count: int
    flight_count: int
    picking_completed: bool
    cart_optimization_completed: bool
    flight_optimization_completed: bool
class OrderItemResponse(BaseModel):
    order_item_id: str
    product_id: int | str
    product_name: str
    quantity: int
    picked_quantity: int
    remaining_quantity: int
    picking_status: Literal["NOT_PICKED", "PARTIAL", "PICKED"]
    tote_ids: list[str]
    length_in: float
    width_in: float
    height_in: float
    weight_lb: float
    volume_in3: float
    volume_ft3: float
class OrderResponse(BaseModel):
    order_id: int
    household_id: int
    batch_id: int
    order_date: date
    destination_community: str
    picking_status: Literal["ENTERED", "READY_FOR_PICKING", "IN_PROGRESS", "PICKED"]
    packing_status: Literal["NOT_PACKED", "PARTIALLY_ALLOCATED", "FULLY_ALLOCATED"]
    picked_quantity: int
    remaining_quantity: int
    items: list[OrderItemResponse]
    total_weight_lb: float
    total_volume_ft3: float

class PickingItemProgressResponse(BaseModel):
    order_item_id: str
    order_id: int
    household_id: int
    product_id: int | str
    product_name: str
    ordered_quantity: int
    picked_quantity: int
    remaining_quantity: int
    allocated_quantity: int
    tote_ids: list[str]
    picking_status: Literal["NOT_PICKED", "PARTIAL", "PICKED"]

class OrderPickingProgressResponse(BaseModel):
    order_id: int
    household_id: int
    ordered_quantity: int
    picked_quantity: int
    remaining_quantity: int
    picking_status: Literal["ENTERED", "READY_FOR_PICKING", "IN_PROGRESS", "PICKED"]
    packing_status: Literal["NOT_PACKED", "PARTIALLY_ALLOCATED", "FULLY_ALLOCATED"]
    items: list[PickingItemProgressResponse]

class PickingProgressResponse(BaseModel):
    orders: list[OrderPickingProgressResponse]

class PickScanRequest(BaseModel):
    order_id: int
    tote_id: str
    identifier: str = Field(min_length=1)
    quantity: int = Field(default=1, gt=0)

class PickScanResponse(BaseModel):
    outcome: Literal["scanned", "quantity_complete", "already_complete", "unknown_item", "wrong_order", "wrong_tote", "unknown_order", "ambiguous_item"]
    message: str
    order_id: int
    order_item_id: str | None = None
    product_id: int | str | None = None
    tote_id: str
    picked_quantity: int
    ordered_quantity: int
    remaining_quantity: int
    progress: OrderPickingProgressResponse | None = None
class OrderSummaryResponse(BaseModel):
    order_count: int
    household_count: int
    total_weight_lb: float
    total_volume_ft3: float
    oversized_order_ids: list[int]
    date_min: date | None
    date_max: date | None
    destination_communities: list[str]
class ToteItemResponse(BaseModel):
    order_item_id: str
    order_id: int
    household_id: int
    quantity: int
    product_id: int | str
    product_name: str
    weight_lb: float
    volume_in3: float
    volume_ft3: float
class ToteResponse(BaseModel):
    tote_id: str
    cart_id: str | None
    flight_id: int | None
    order_ids: list[int]
    household_ids: list[int]
    items: list[ToteItemResponse]
    weight_lb: float
    volume_in3: float
    volume_ft3: float
    volume_fill_percent: float
class CartResponse(BaseModel):
    cart_id: str
    tote_ids: list[str]
    tote_count: int
    tote_capacity: int
    utilization_percent: float
    order_ids: list[int]
    split_order_ids: list[int]
class CartOptimizationResponse(BaseModel):
    feasible: bool
    total_carts: int
    total_totes: int
    totes_per_cart: int
    orders_kept_together: int
    orders_kept_together_ids: list[int]
    split_order_ids: list[int]
    warnings: list[str]
    errors: list[str]
    unassigned_tote_ids: list[str]
    carts: list[CartResponse]
    per_cart_metrics: list[CartResponse]
class ToteOptimizationResponse(BaseModel):
    total_totes: int
    total_orders: int
    complete_orders: int
    complete_order_ids: list[int]
    split_orders: int
    split_order_ids: list[int]
    oversized_order_ids: list[int]
    unallocated_item_count: int
    total_weight_lb: float
    total_volume_ft3: float
    average_fill_percent: float
    warnings: list[str]
    totes: list[ToteResponse]
class PickingSummaryResponse(BaseModel):
    total_totes: int
    total_carts: int
    tote_capacity_in3: float
    fully_allocated_orders: int
    fully_allocated_order_ids: list[int]
    split_orders: list[int]
    cart_feasible: bool | None
    warnings: list[str]
    errors: list[str]
class FlightCapacityResponse(BaseModel):
    departure_id: int
    departure_date: date
    available_totes: int
    available_payload_lb: float
    available_volume_cuft: float | None
    flight_cost: float | None = None
    currency: str | None = None

class ManifestItemResponse(BaseModel):
    source_item_id: str
    product_id: int | str
    product_name: str
    quantity: int

class ManifestOrderResponse(BaseModel):
    order_id: int
    items: list[ManifestItemResponse]

class ManifestHouseholdResponse(BaseModel):
    household_id: int
    orders: list[ManifestOrderResponse]

class ManifestToteResponse(BaseModel):
    tote_id: str
    cart_id: str | None
    weight_lb: float
    volume_cuft: float
    volume_fill_percent: float
    households: list[ManifestHouseholdResponse]
class FlightMetricResponse(BaseModel):
    flight_id: int
    departure_date: date
    tote_ids: list[str]
    tote_count: int
    tote_capacity: int
    used_payload_lb: float
    remaining_payload_lb: float
    used_volume_cuft: float
    remaining_volume_cuft: float | None
    payload_utilization_percent: float
    volume_utilization_percent: float | None
    tote_utilization_percent: float
    order_ids: list[int]
    household_ids: list[int]
    complete_order_ids: list[int]
    rollover_order_ids: list[int]
    tightest_capacity_dimension: str | None
class FlightCostUpdateResponse(BaseModel):
    flight_id: int
    flight_cost: float | None
    currency: str | None
    completed_order_count: int
    cost_per_order: float | None
    cost_per_order_unknown_reason: str | None

class FlightResponse(BaseModel):
    flight_id: int
    flight_cost: float | None
    currency: str | None
    completed_order_count: int
    cost_per_order: float | None
    cost_per_order_unknown_reason: str | None
    departure_date: date
    destination_communities: list[str]
    capacity: FlightCapacityResponse
    tote_ids: list[str]
    manifest_totes: list[ManifestToteResponse]
    order_ids: list[int]
    household_ids: list[int]
    complete_order_ids: list[int]
    rollover_order_ids: list[int]
    total_weight_lb: float
    total_volume_cuft: float
    tote_count: int
    payload_utilization_percent: float
    volume_utilization_percent: float | None
    tote_utilization_percent: float
    remaining_totes: int
    remaining_payload_lb: float
    remaining_volume_cuft: float | None
    tightest_capacity_dimension: str | None
class FlightOptimizationResponse(BaseModel):
    feasible: bool
    total_flights: int
    assigned_tote_ids: list[str]
    unassigned_tote_ids: list[str]
    complete_order_ids: list[int]
    rollover_order_ids: list[int]
    warnings: list[str]
    errors: list[str]
    flight_plans: list[FlightResponse]
    per_flight_metrics: list[FlightMetricResponse]
class FlightSummaryResponse(BaseModel):
    total_flights: int
    assigned_totes: int
    unassigned_totes: int
    complete_orders: list[int]
    rollover_orders: list[int]
    warnings: list[str]
    errors: list[str]
    per_flight_metrics: list[FlightMetricResponse]
class MessageResponse(BaseModel):
    message: str
class ManualCartAssignmentResponse(BaseModel):
    tote: ToteResponse
    cart: CartResponse
class ManualFlightAssignmentResponse(BaseModel):
    tote: ToteResponse
    flight: FlightResponse
