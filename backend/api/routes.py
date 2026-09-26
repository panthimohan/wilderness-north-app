"""HTTP endpoints and explicit domain-to-DTO conversion helpers."""
from __future__ import annotations
from datetime import date, datetime, timezone
import base64

from fastapi import APIRouter, HTTPException, Request

from backend import config
from backend.app_state import PlanningState
from backend.models import Cart, FlightPlan, Tote
from backend.services.cart_optimizer import CartMetrics, CartOptimizationError, CartOptimizationResult, optimize_carts
from backend.services.csv_service import summarize_orders
from backend.services.flight_optimizer import FlightMetrics, FlightOptimizationError, FlightOptimizationResult, optimize_flights
from backend.services.plan_service import PlanAssignmentError, PlanService
from backend.services.tote_optimizer import optimize_totes
from backend.services import import_service
from backend.services.picking_service import progress_for_orders, scan_item
from backend.api.schemas import (
    ImportAnalyzeRequest, ImportAnalysisFileResponse, ImportPreviewRequest, ImportCommitRequest, ImportCommitResponse, ModeRequest,
    CartAssignmentRequest, CartOptimizationResponse, CartResponse, FlightAssignmentRequest,
    FlightCapacityResponse, FlightCostUpdateRequest, FlightCostUpdateResponse, FlightMetricResponse, FlightOptimizationResponse, FlightResponse,
    FlightSummaryResponse, HealthResponse, ManualCartAssignmentResponse,
    ManualFlightAssignmentResponse, MessageResponse, OptimizeCartsRequest,
    OptimizeFlightsRequest, OptimizeTotesRequest, OrderItemResponse, OrderResponse, StageRequest,
    OrderSummaryResponse, PickingSummaryResponse, SessionResponse, ToteItemResponse,
    ToteOptimizationResponse, ToteResponse, PickingItemProgressResponse, OrderPickingProgressResponse,
    PickingProgressResponse, PickScanRequest, PickScanResponse, ManifestItemResponse, ManifestOrderResponse,
    ManifestHouseholdResponse, ManifestToteResponse,
)

router = APIRouter()


def _state(request: Request) -> PlanningState:
    return request.app.state.planning


def _stage(state: PlanningState, requested: str | None) -> str:
    if requested is not None and requested not in {"stage1", "stage2"}:
        raise HTTPException(status_code=400, detail="stage must be 'stage1' or 'stage2'")
    stage = state.active_stage if requested is None or state.active_mode == "user" else requested
    if stage not in {"stage1", "stage2"}:
        raise HTTPException(status_code=400, detail="stage must be 'stage1' or 'stage2'")
    if stage != state.active_stage:
        state.select_stage(stage)
    return stage


def _order_progress(state: PlanningState, order):
    totes = state.totes if state.picking_completed else []
    return progress_for_orders([order], totes, state.picked_quantities)[0]


def order_to_response(order, state: PlanningState) -> OrderResponse:
    progress = _order_progress(state, order)
    item_progress = {row["order_item_id"]: row for row in progress["items"]}
    return OrderResponse(
        order_id=order.order_id, household_id=order.household_id, batch_id=order.batch_id,
        order_date=order.order_date, destination_community=order.destination_community,
        picking_status=progress["picking_status"], packing_status=progress["packing_status"],
        picked_quantity=progress["picked_quantity"], remaining_quantity=progress["remaining_quantity"],
        items=[OrderItemResponse(
            order_item_id=item.order_item_id, product_id=item.product_id, product_name=item.product_name,
            quantity=item.source_quantity,
            picked_quantity=item_progress[item.order_item_id]["picked_quantity"],
            remaining_quantity=item_progress[item.order_item_id]["remaining_quantity"],
            picking_status=item_progress[item.order_item_id]["picking_status"],
            tote_ids=item_progress[item.order_item_id]["tote_ids"],
            length_in=item.length_in, width_in=item.width_in, height_in=item.height_in,
            weight_lb=item.weight_lb, volume_in3=item.volume_in3, volume_ft3=item.volume_ft3,
        ) for item in order.items],
        total_weight_lb=order.weight_lb, total_volume_ft3=order.volume_ft3,
    )


def manifest_tote_to_response(tote: Tote) -> ManifestToteResponse:
    households: dict[int, dict[int, list[ManifestItemResponse]]] = {}
    for allocation in tote.items:
        households.setdefault(allocation.household_id, {}).setdefault(allocation.order_id, []).append(
            ManifestItemResponse(
                source_item_id=allocation.order_item_id,
                product_id=allocation.item.product_id,
                product_name=allocation.item.product_name,
                quantity=allocation.quantity,
            )
        )
    return ManifestToteResponse(
        tote_id=tote.tote_id, cart_id=tote.cart_id, weight_lb=tote.weight_lb,
        volume_cuft=tote.volume_ft3, volume_fill_percent=tote.volume_fill_pct,
        households=[
            ManifestHouseholdResponse(
                household_id=household_id,
                orders=[
                    ManifestOrderResponse(order_id=order_id, items=items)
                    for order_id, items in sorted(order_map.items())
                ],
            )
            for household_id, order_map in sorted(households.items())
        ],
    )


def tote_to_response(tote: Tote) -> ToteResponse:
    return ToteResponse(
        tote_id=tote.tote_id, cart_id=tote.cart_id, flight_id=tote.flight_id,
        order_ids=tote.order_ids, household_ids=tote.household_ids,
        items=[ToteItemResponse(
            order_item_id=allocation.order_item_id, order_id=allocation.order_id,
            household_id=allocation.household_id, quantity=allocation.quantity,
            product_id=allocation.item.product_id, product_name=allocation.item.product_name,
            weight_lb=allocation.weight_lb, volume_in3=allocation.volume_in3,
            volume_ft3=allocation.volume_ft3,
        ) for allocation in tote.items],
        weight_lb=tote.weight_lb, volume_in3=tote.volume_in3, volume_ft3=tote.volume_ft3,
        volume_fill_percent=tote.volume_fill_pct,
    )


def cart_to_response(cart: Cart, split_order_ids: set[int] | None = None) -> CartResponse:
    split_order_ids = split_order_ids or set()
    return CartResponse(
        cart_id=cart.cart_id, tote_ids=[tote.tote_id for tote in cart.totes],
        tote_count=len(cart.totes), tote_capacity=cart.tote_capacity,
        utilization_percent=100.0 * len(cart.totes) / cart.tote_capacity if cart.tote_capacity else 0.0,
        order_ids=cart.order_ids, split_order_ids=sorted(split_order_ids.intersection(cart.order_ids)),
    )


def flight_metrics_to_response(metrics: FlightMetrics) -> FlightMetricResponse:
    return FlightMetricResponse(
        flight_id=metrics.flight_id, departure_date=metrics.departure_date,
        tote_ids=metrics.tote_ids, tote_count=metrics.tote_count, tote_capacity=metrics.tote_capacity,
        used_payload_lb=metrics.used_payload_lb, remaining_payload_lb=metrics.remaining_payload_lb,
        used_volume_cuft=metrics.used_volume_cuft, remaining_volume_cuft=metrics.remaining_volume_cuft,
        payload_utilization_percent=metrics.payload_utilization_percent,
        volume_utilization_percent=metrics.volume_utilization_percent,
        tote_utilization_percent=metrics.tote_utilization_percent, order_ids=metrics.order_ids,
        household_ids=metrics.household_ids, complete_order_ids=metrics.complete_order_ids,
        rollover_order_ids=metrics.rollover_order_ids,
        tightest_capacity_dimension=metrics.tightest_capacity_dimension,
    )


def flight_cost_summary(plan: FlightPlan | None, capacity) -> tuple[int, float | None, str | None]:
    if capacity.flight_cost is None:
        return 0 if plan is None else len(set(plan.order_ids) - set(plan.rolled_over_order_ids)), None, "Flight cost not provided."
    completed_ids = set() if plan is None else set(plan.order_ids) - set(plan.rolled_over_order_ids)
    count = len(completed_ids)
    if count == 0:
        return 0, None, "No completed orders carried on this flight."
    return count, capacity.flight_cost / count, None


def flight_plan_to_response(plan: FlightPlan, metrics: FlightMetrics | None = None) -> FlightResponse:
    capacity = FlightCapacityResponse(
        departure_id=plan.capacity.departure_id, departure_date=plan.capacity.departure_date,
        available_totes=plan.capacity.available_totes,
        available_payload_lb=plan.capacity.available_payload_lb,
        available_volume_cuft=plan.capacity.available_volume_cuft,
        flight_cost=plan.capacity.flight_cost, currency=plan.capacity.currency,
    )
    destinations = sorted({
        allocation.item.destination_community
        for tote in plan.totes for allocation in tote.items if allocation.quantity
    })
    completed_order_count, cost_per_order, cost_unknown_reason = flight_cost_summary(plan, plan.capacity)
    return FlightResponse(
        flight_id=plan.flight_id, flight_cost=plan.capacity.flight_cost,
        currency=plan.capacity.currency, completed_order_count=completed_order_count,
        cost_per_order=cost_per_order, cost_per_order_unknown_reason=cost_unknown_reason,
        departure_date=plan.capacity.departure_date,
        destination_communities=destinations, capacity=capacity,
        tote_ids=sorted(tote.tote_id for tote in plan.totes),
        manifest_totes=[manifest_tote_to_response(tote) for tote in sorted(plan.totes, key=lambda item: item.tote_id)],
        order_ids=plan.order_ids,
        household_ids=sorted({household_id for tote in plan.totes for household_id in tote.household_ids}),
        complete_order_ids=metrics.complete_order_ids if metrics else plan.fully_loaded_order_ids,
        rollover_order_ids=metrics.rollover_order_ids if metrics else plan.rolled_over_order_ids,
        total_weight_lb=plan.total_weight_lb, total_volume_cuft=plan.total_volume_cuft,
        tote_count=plan.tote_count, payload_utilization_percent=plan.payload_utilization_pct,
        volume_utilization_percent=plan.volume_utilization_pct,
        tote_utilization_percent=plan.tote_utilization_pct,
        remaining_totes=plan.remaining_totes, remaining_payload_lb=plan.remaining_payload_lb,
        remaining_volume_cuft=plan.remaining_volume_cuft,
        tightest_capacity_dimension=plan.tightest_capacity_dimension,
    )


def _cart_split_ids(result: CartOptimizationResult | None) -> set[int]:
    if result is None:
        return set()
    return {order_id for metric in result.per_cart_metrics for order_id in metric.split_order_ids}


def _refresh_cart_result(state: PlanningState) -> None:
    result = state.last_cart_result
    if result is None:
        return
    split_ids = {
        order_id for tote in state.totes
        for order_id in tote.order_ids
        if sum(order_id in member.order_ids for member in state.totes) > 1
    }
    metrics = [CartMetrics(
        cart_id=cart.cart_id, tote_ids=sorted(t.tote_id for t in cart.totes),
        tote_count=len(cart.totes),
        utilization_percent=100 * len(cart.totes) / result.totes_per_cart,
        order_ids=cart.order_ids,
        split_order_ids=sorted(split_ids.intersection(cart.order_ids)),
    ) for cart in state.carts]
    result.carts = state.carts
    result.total_carts = len(state.carts)
    result.per_cart_metrics = metrics
    result.orders_kept_together_ids = sorted({oid for t in state.totes for oid in t.order_ids})
    result.orders_kept_together = len(result.orders_kept_together_ids)
    result.unassigned_tote_ids = sorted(t.tote_id for t in state.totes if t.cart_id is None)
    result.feasible = not result.unassigned_tote_ids
    if result.unassigned_tote_ids:
        result.errors = [f"{len(result.unassigned_tote_ids)} tote(s) are not assigned to a cart."]
    else:
        result.errors = []


def _refresh_flight_result(state: PlanningState) -> None:
    result = state.last_flight_result
    if result is None:
        return
    plans = sorted(state.flight_plans, key=lambda plan: (plan.capacity.departure_date, plan.flight_id))
    loaded: dict[str, int] = {}
    metrics: list[FlightMetrics] = []
    order_for_item = {item.order_item_id: order for order in state.active_orders for item in order.items}
    for plan in plans:
        prior = dict(loaded)
        for item_id, qty in plan.loaded_item_quantities.items():
            loaded[item_id] = loaded.get(item_id, 0) + qty
        remaining = {
            item.order_item_id: item.source_quantity - loaded.get(item.order_item_id, 0)
            for order in state.active_orders for item in order.items
            if item.source_quantity - loaded.get(item.order_item_id, 0) > 0
            and order.order_date <= plan.capacity.departure_date
        }
        plan.set_rollover_state(previously_loaded_quantities=prior, rollover_quantities=remaining)
        newly_complete = [
            order.order_id for order in state.active_orders if order.items and all(
                loaded.get(item.order_item_id, 0) == item.source_quantity for item in order.items
            ) and not all(
                prior.get(item.order_item_id, 0) == item.source_quantity for item in order.items
            )
        ]
        metrics.append(FlightMetrics(
            flight_id=plan.flight_id, departure_date=plan.capacity.departure_date,
            tote_ids=sorted(t.tote_id for t in plan.totes), tote_count=plan.tote_count,
            tote_capacity=plan.capacity.available_totes, used_payload_lb=plan.total_weight_lb,
            remaining_payload_lb=plan.remaining_payload_lb, used_volume_cuft=plan.total_volume_cuft,
            remaining_volume_cuft=plan.remaining_volume_cuft,
            payload_utilization_percent=plan.payload_utilization_pct,
            volume_utilization_percent=plan.volume_utilization_pct,
            tote_utilization_percent=plan.tote_utilization_pct, order_ids=plan.order_ids,
            household_ids=sorted({hid for t in plan.totes for hid in t.household_ids}),
            complete_order_ids=sorted(newly_complete), rollover_order_ids=plan.rolled_over_order_ids,
            tightest_capacity_dimension=plan.tightest_capacity_dimension,
        ))
    result.flight_plans = plans
    result.per_flight_metrics = metrics
    result.assigned_tote_ids = sorted(t.tote_id for p in plans for t in p.totes)
    result.unassigned_tote_ids = sorted(t.tote_id for t in state.totes if t.flight_id is None)
    result.complete_order_ids = sorted(
        order.order_id for order in state.active_orders if order.items and all(
            loaded.get(item.order_item_id, 0) == item.source_quantity for item in order.items
        )
    )
    result.rollover_order_ids = sorted({
        order.order_id for order in state.active_orders
        if any(loaded.get(item.order_item_id, 0) < item.source_quantity for item in order.items)
    })
    result.feasible = not result.unassigned_tote_ids
    result.errors = [] if result.feasible else [
        f"{len(result.unassigned_tote_ids)} tote(s) are not assigned to a flight."
    ]


def _flight_result_response(result: FlightOptimizationResult) -> FlightOptimizationResponse:
    metric_by_id = {metric.flight_id: metric for metric in result.per_flight_metrics}
    return FlightOptimizationResponse(
        feasible=result.feasible, total_flights=result.total_flights,
        assigned_tote_ids=result.assigned_tote_ids, unassigned_tote_ids=result.unassigned_tote_ids,
        complete_order_ids=result.complete_order_ids, rollover_order_ids=result.rollover_order_ids,
        warnings=result.warnings, errors=result.errors,
        flight_plans=[flight_plan_to_response(plan, metric_by_id.get(plan.flight_id)) for plan in result.flight_plans],
        per_flight_metrics=[flight_metrics_to_response(metric) for metric in result.per_flight_metrics],
    )


def _cart_result_response(result: CartOptimizationResult, known_split_order_ids: set[int] | None = None) -> CartOptimizationResponse:
    split_ids = _cart_split_ids(result) | (known_split_order_ids or set())
    by_id = {cart.cart_id: cart for cart in result.carts}
    metric_responses = []
    for metric in result.per_cart_metrics:
        cart = by_id.get(metric.cart_id)
        if cart is not None:
            metric_responses.append(cart_to_response(cart, set(metric.split_order_ids)))
    return CartOptimizationResponse(
        feasible=result.feasible, total_carts=result.total_carts, total_totes=result.total_totes,
        totes_per_cart=result.totes_per_cart, orders_kept_together=result.orders_kept_together,
        orders_kept_together_ids=result.orders_kept_together_ids, split_order_ids=sorted(split_ids),
        warnings=result.warnings, errors=result.errors, unassigned_tote_ids=result.unassigned_tote_ids,
        carts=[cart_to_response(cart, split_ids) for cart in result.carts], per_cart_metrics=metric_responses,
    )


def _plan_service(state: PlanningState) -> PlanService:
    return PlanService(state.totes, state.carts, state.flight_plans)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/api/session", response_model=SessionResponse)
def session(request: Request) -> SessionResponse:
    state = _state(request)
    with state.lock:
        return SessionResponse(
            active_stage=state.active_stage, active_mode=state.active_mode, active_dataset_name=state.active_dataset_name,
            active_dataset_source=state.active_dataset_source, user_imported_at=state.user_imported_at,
            order_count=len(state.active_orders), tote_count=len(state.totes),
            cart_count=len(state.carts), flight_count=len(state.flight_plans),
            picking_completed=state.picking_completed,
            cart_optimization_completed=state.cart_optimization_completed,
            flight_optimization_completed=state.flight_optimization_completed,
        )


@router.post("/api/session/reset", response_model=SessionResponse)
def reset_session(request: Request) -> SessionResponse:
    state = _state(request)
    with state.lock: state.reset_session()
    return session(request)


@router.post("/api/session/stage", response_model=SessionResponse)
def change_stage(payload: StageRequest, request: Request) -> SessionResponse:
    stage = payload.stage
    if stage not in {"stage1", "stage2"}:
        raise HTTPException(status_code=400, detail="stage must be 'stage1' or 'stage2'")
    state = _state(request)
    with state.lock:
        if state.active_mode == "challenge":
            state.select_stage(stage)
        elif stage != state.active_stage:
            state.clear_plan()
            state.active_stage = stage
            state.active_orders = state.user_orders
    return session(request)


@router.post("/api/session/mode", response_model=SessionResponse)
def change_mode(payload: ModeRequest, request: Request) -> SessionResponse:
    state = _state(request)
    with state.lock:
        state.select_mode(payload.mode)
    return session(request)


@router.post("/api/import/analyze", response_model=list[ImportAnalysisFileResponse])
def analyze_import(payload: ImportAnalyzeRequest, request: Request) -> list[ImportAnalysisFileResponse]:
    state = _state(request)
    analyzed = []
    try:
        for upload in payload.files:
            filename, content = import_service.decode_upload(upload.filename, upload.content_base64)
            draft = import_service.analyze_upload(filename, content)
            suggested = import_service.detect_type(draft.headers, filename=draft.filename, rows=draft.rows)[3] if draft.headers else {}
            if draft.dataset_type == "unknown":
                suggested = {}
            state.import_drafts[draft.draft_id] = draft
            analyzed.append(ImportAnalysisFileResponse(
                draft_id=draft.draft_id, filename=draft.filename, dataset_type=draft.dataset_type,
                confidence=draft.confidence, signals=draft.signals, headers=draft.headers,
                rows=draft.rows[:10], suggested_mapping=suggested, parse_error=draft.parse_error, note=draft.note,
            ))
        if len(state.import_drafts) > 25:
            for key in list(state.import_drafts)[:-25]:
                state.import_drafts.pop(key, None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return analyzed


@router.delete("/api/import/drafts/{draft_id}", response_model=MessageResponse)
def discard_import_draft(draft_id: str, request: Request) -> MessageResponse:
    state = _state(request)
    with state.lock:
        existed = state.import_drafts.pop(draft_id, None) is not None
    if not existed:
        raise HTTPException(status_code=404, detail="Import draft was not found or has expired.")
    return MessageResponse(message="Import draft discarded.")


@router.post("/api/import/preview")
def preview_import(payload: ImportPreviewRequest, request: Request) -> dict:
    state = _state(request)
    draft = state.import_drafts.get(payload.draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Import draft expired; select and analyze the file again.")
    return import_service.preview_draft(draft, payload.dataset_type, payload.mapping, set(payload.excluded_row_numbers))


@router.post("/api/import/commit", response_model=ImportCommitResponse)
def commit_import(payload: ImportCommitRequest, request: Request) -> ImportCommitResponse:
    state = _state(request)
    order_groups = []
    capacity_groups = []
    filenames = []
    # Prepare and validate every file before mutating session state (atomic commit).
    for item in payload.files:
        draft = state.import_drafts.get(item.draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Import draft expired; analyze the file again.")
        excluded = set(item.excluded_row_numbers)
        preview = import_service.preview_draft(draft, item.dataset_type, item.mapping, excluded)
        if not preview.get("valid"):
            raise HTTPException(status_code=422, detail={"filename": draft.filename, "error": preview.get("error"), "mapping_errors": preview.get("mapping_errors", []), "invalid_rows": preview.get("invalid_rows", [])})
        try:
            if item.dataset_type == "orders":
                order_groups.append(import_service.normalize_orders(draft, item.mapping, excluded))
            else:
                capacity_groups.append(import_service.normalize_capacities(draft, item.mapping, excluded))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"filename": draft.filename, "error": str(exc)}) from exc
        filenames.append(draft.filename)
    if not payload.files:
        raise HTTPException(status_code=422, detail="Select at least one dataset to import.")
    imported_orders = [order for group in order_groups for order in group]
    imported_capacities = [capacity for group in capacity_groups for capacity in group]
    # Merge multiple order files only when the shared order metadata agrees.
    merged = {}
    for order in imported_orders:
        existing = merged.get(order.order_id)
        if existing is not None:
            if (existing.household_id, existing.batch_id, existing.order_date, existing.destination_community) != (order.household_id, order.batch_id, order.order_date, order.destination_community):
                raise HTTPException(status_code=422, detail=f"Order {order.order_id} has conflicting metadata across imported files.")
            existing.items.extend(order.items)
        else:
            merged[order.order_id] = order
    capacity_ids = [capacity.departure_id for capacity in imported_capacities]
    if len(set(capacity_ids)) != len(capacity_ids):
        raise HTTPException(status_code=422, detail="Duplicate flight/departure IDs exist across imported capacity files.")
    with state.lock:
        if order_groups:
            state.user_orders = list(merged.values())
            state.picked_quantities.clear()
            state.user_order_source_name = ", ".join(draft.filename for item in payload.files if item.dataset_type == "orders" for draft in [state.import_drafts[item.draft_id]])
        if capacity_groups:
            state.user_flight_capacities = imported_capacities
            state.user_capacity_source_name = ", ".join(draft.filename for item in payload.files if item.dataset_type == "flight_capacity" for draft in [state.import_drafts[item.draft_id]])
        state.active_mode = "user"
        state.active_orders = state.user_orders
        names = [name for name in (state.user_order_source_name, state.user_capacity_source_name) if name]
        state.active_dataset_name = ", ".join(names) if names else ", ".join(filenames)
        state.active_dataset_source = "User upload"
        state.user_imported_at = datetime.now(timezone.utc).isoformat()
        state.clear_plan()
        for item in payload.files:
            state.import_drafts.pop(item.draft_id, None)
    summary = summarize_orders(state.active_orders)
    return ImportCommitResponse(mode="user", order_count=len(state.active_orders), household_count=int(summary["household_count"]), flight_capacity_count=len(state.user_flight_capacities), imported_files=filenames, session=session(request).model_dump())


@router.get("/api/orders", response_model=list[OrderResponse])
def list_orders(
    request: Request, stage: str | None = None, order_date: date | None = None, order_id: int | None = None
) -> list[OrderResponse]:
    state = _state(request)
    with state.lock:
        selected = _stage(state, stage)
        orders = state.orders_for_active(selected)
        if order_date is not None: orders = [o for o in orders if o.order_date == order_date]
        if order_id is not None: orders = [o for o in orders if o.order_id == order_id]
        return [order_to_response(order, state) for order in orders]


@router.get("/api/orders/summary", response_model=OrderSummaryResponse)
def order_summary(request: Request, stage: str | None = None) -> OrderSummaryResponse:
    state = _state(request)
    with state.lock:
        orders = state.orders_for_active(_stage(state, stage))
        summary = summarize_orders(orders)
        dates = [order.order_date for order in orders]
        return OrderSummaryResponse(
            order_count=int(summary["order_count"]), household_count=int(summary["household_count"]),
            total_weight_lb=float(summary["total_weight_lb"]), total_volume_ft3=float(summary["total_volume_ft3"]),
            oversized_order_ids=list(summary["oversized_order_ids"]), date_min=min(dates) if dates else None,
            date_max=max(dates) if dates else None,
            destination_communities=sorted({order.destination_community for order in orders}),
        )


@router.get("/api/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, request: Request, stage: str | None = None) -> OrderResponse:
    state = _state(request)
    with state.lock:
        orders = state.orders_for_active(_stage(state, stage))
        order = next((o for o in orders if o.order_id == order_id), None)
        if order is None: raise HTTPException(status_code=404, detail=f"order {order_id} was not found")
        return order_to_response(order, state)


@router.get("/api/picking/progress", response_model=PickingProgressResponse)
def get_picking_progress(request: Request) -> PickingProgressResponse:
    state = _state(request)
    with state.lock:
        rows = progress_for_orders(
            state.orders_for_active(state.active_stage),
            state.totes if state.picking_completed else [],
            state.picked_quantities,
        )
        return PickingProgressResponse(orders=[OrderPickingProgressResponse(**row) for row in rows])


@router.post("/api/picking/scan", response_model=PickScanResponse)
def scan_picking_item(payload: PickScanRequest, request: Request) -> PickScanResponse:
    state = _state(request)
    with state.lock:
        if not state.picking_completed:
            raise HTTPException(status_code=409, detail="run tote optimization before scanning items")
        try:
            outcome = scan_item(
                state.orders_for_active(state.active_stage), state.totes, state.picked_quantities,
                order_id=payload.order_id, identifier=payload.identifier,
                quantity=payload.quantity, tote_id=payload.tote_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        current_order = next((order for order in state.orders_for_active(state.active_stage) if order.order_id == payload.order_id), None)
        progress = None
        if current_order is not None:
            row = _order_progress(state, current_order)
            progress = OrderPickingProgressResponse(**row)
        return PickScanResponse(
            outcome=outcome.outcome, message=outcome.message, order_id=outcome.order_id,
            order_item_id=outcome.order_item_id, product_id=outcome.product_id,
            tote_id=payload.tote_id, picked_quantity=outcome.picked_quantity,
            ordered_quantity=outcome.ordered_quantity, remaining_quantity=outcome.remaining_quantity,
            progress=progress,
        )


@router.post("/api/picking/optimize-totes", response_model=ToteOptimizationResponse)
def picking_optimize_totes(request: Request, payload: OptimizeTotesRequest = OptimizeTotesRequest()) -> ToteOptimizationResponse:
    state = _state(request)
    with state.lock:
        selected = _stage(state, payload.stage)
        orders = state.orders_for_active(selected)
        try: result = optimize_totes(orders)
        except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        state.totes = result.totes
        state.carts, state.flight_plans = [], []
        state.last_tote_result, state.last_cart_result, state.last_flight_result = result, None, None
        return ToteOptimizationResponse(
            total_totes=result.total_totes, total_orders=result.total_orders,
            complete_orders=result.complete_orders, complete_order_ids=result.complete_order_ids,
            split_orders=result.split_orders, split_order_ids=result.split_order_ids,
            oversized_order_ids=result.oversized_order_ids, unallocated_item_count=len(result.unallocated_items),
            total_weight_lb=result.total_weight_lb, total_volume_ft3=result.total_volume_ft3,
            average_fill_percent=result.average_fill_percent, warnings=result.warnings,
            totes=[tote_to_response(tote) for tote in state.totes],
        )


@router.get("/api/picking/totes", response_model=list[ToteResponse])
def get_totes(request: Request) -> list[ToteResponse]:
    state = _state(request)
    with state.lock:
        if not state.picking_completed: raise HTTPException(status_code=409, detail="tote optimization has not been run")
        return [tote_to_response(tote) for tote in state.totes]


@router.get("/api/picking/carts", response_model=list[CartResponse])
def get_carts(request: Request) -> list[CartResponse]:
    state = _state(request)
    with state.lock:
        split_ids = _cart_split_ids(state.last_cart_result)
        if not state.cart_optimization_completed: return []
        return [cart_to_response(cart, split_ids) for cart in state.carts]


@router.post("/api/picking/optimize-carts", response_model=CartOptimizationResponse)
def picking_optimize_carts(request: Request, payload: OptimizeCartsRequest = OptimizeCartsRequest()) -> CartOptimizationResponse:
    state = _state(request)
    with state.lock:
        if not state.picking_completed: raise HTTPException(status_code=409, detail="run tote optimization first")
        capacity = payload.totes_per_cart if payload.totes_per_cart is not None else config.DEFAULT_TOTES_PER_CART
        try: result = optimize_carts(state.totes, state.active_orders, capacity)
        except (ValueError, CartOptimizationError) as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        state.carts = result.carts
        state.last_cart_result = result
        return _cart_result_response(result, set(state.last_tote_result.split_order_ids))


@router.get("/api/picking/summary", response_model=PickingSummaryResponse)
def picking_summary(request: Request) -> PickingSummaryResponse:
    state = _state(request)
    with state.lock:
        tote_result = state.last_tote_result
        cart_result = state.last_cart_result
        return PickingSummaryResponse(
            total_totes=len(state.totes), total_carts=len(state.carts), tote_capacity_in3=config.TOTE_VOLUME_IN3,
            fully_allocated_orders=tote_result.complete_orders if tote_result else 0,
            fully_allocated_order_ids=tote_result.complete_order_ids if tote_result else [],
            split_orders=tote_result.split_order_ids if tote_result else [],
            cart_feasible=cart_result.feasible if cart_result else None,
            warnings=(tote_result.warnings if tote_result else []) + (cart_result.warnings if cart_result else []),
            errors=cart_result.errors if cart_result else [],
        )


@router.get("/api/flights/capacities", response_model=list[FlightCapacityResponse])
def get_flight_capacities(request: Request, stage: str = "stage2") -> list[FlightCapacityResponse]:
    state = _state(request)
    if stage not in {"stage1", "stage2"}:
        raise HTTPException(status_code=400, detail="stage must be 'stage1' or 'stage2'")
    return [FlightCapacityResponse(
        departure_id=c.departure_id, departure_date=c.departure_date, available_totes=c.available_totes,
        available_payload_lb=c.available_payload_lb, available_volume_cuft=c.available_volume_cuft,
        flight_cost=c.flight_cost, currency=c.currency,
    ) for c in state.capacities_for_active(stage)]


@router.post("/api/flights/optimize", response_model=FlightOptimizationResponse)
def flight_optimize(request: Request, payload: OptimizeFlightsRequest = OptimizeFlightsRequest()) -> FlightOptimizationResponse:
    state = _state(request)
    with state.lock:
        selected = _stage(state, payload.stage)
        if not state.picking_completed: raise HTTPException(status_code=409, detail="run tote optimization first")
        # The domain optimizer expects unassigned totes. Clear an earlier/manual
        # flight plan through PlanService while retaining the canonical tote objects.
        if state.flight_plans:
            try:
                service = _plan_service(state)
                for tote in state.totes:
                    if tote.flight_id is not None:
                        service.remove_tote_from_flight(tote.tote_id)
            except PlanAssignmentError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            state.flight_plans = []
            state.last_flight_result = None
        try: result = optimize_flights(state.totes, state.active_orders, state.capacities_for_active(selected), stage=selected)
        except (ValueError, FlightOptimizationError) as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        state.flight_plans = result.flight_plans
        state.last_flight_result = result
        return _flight_result_response(result)


@router.get("/api/flights", response_model=list[FlightResponse])
def get_flights(request: Request) -> list[FlightResponse]:
    state = _state(request)
    with state.lock:
        result = state.last_flight_result
        if result is None: return []
        metric_by_id = {metric.flight_id: metric for metric in result.per_flight_metrics}
        return [flight_plan_to_response(plan, metric_by_id.get(plan.flight_id)) for plan in state.flight_plans]


@router.patch("/api/flights/{flight_id}/cost", response_model=FlightCostUpdateResponse)
def update_flight_cost(flight_id: int, payload: FlightCostUpdateRequest, request: Request) -> FlightCostUpdateResponse:
    state = _state(request)
    with state.lock:
        capacity = next((item for item in state.capacities_for_active() if item.departure_id == flight_id), None)
        if capacity is None:
            raise HTTPException(status_code=404, detail=f"flight {flight_id} was not found")
        capacity.flight_cost = payload.flight_cost
        capacity.currency = payload.currency if payload.flight_cost is not None else payload.currency
        plan = next((item for item in state.flight_plans if item.flight_id == flight_id), None)
        count, amount, reason = flight_cost_summary(plan, capacity)
        return FlightCostUpdateResponse(
            flight_id=flight_id, flight_cost=capacity.flight_cost, currency=capacity.currency,
            completed_order_count=count, cost_per_order=amount,
            cost_per_order_unknown_reason=reason,
        )


@router.get("/api/flights/summary", response_model=FlightSummaryResponse)
def flight_summary(request: Request) -> FlightSummaryResponse:
    state = _state(request)
    result = state.last_flight_result
    if result is None:
        return FlightSummaryResponse(total_flights=0, assigned_totes=0, unassigned_totes=0,
            complete_orders=[], rollover_orders=[], warnings=[], errors=[], per_flight_metrics=[])
    return FlightSummaryResponse(
        total_flights=result.total_flights, assigned_totes=len(result.assigned_tote_ids),
        unassigned_totes=len(result.unassigned_tote_ids), complete_orders=result.complete_order_ids,
        rollover_orders=result.rollover_order_ids, warnings=result.warnings, errors=result.errors,
        per_flight_metrics=[flight_metrics_to_response(m) for m in result.per_flight_metrics],
    )


@router.get("/api/flights/{flight_id}", response_model=FlightResponse)
def get_flight(flight_id: int, request: Request) -> FlightResponse:
    state = _state(request)
    with state.lock:
        result = state.last_flight_result
        if result is None: raise HTTPException(status_code=404, detail=f"flight {flight_id} is not available")
        plan = next((p for p in state.flight_plans if p.flight_id == flight_id), None)
        if plan is None: raise HTTPException(status_code=404, detail=f"flight {flight_id} is not available")
        metric = next((m for m in result.per_flight_metrics if m.flight_id == flight_id), None)
        return flight_plan_to_response(plan, metric)




@router.post("/api/picking/totes/{tote_id}/cart", response_model=ManualCartAssignmentResponse)
def assign_cart(tote_id: str, payload: CartAssignmentRequest, request: Request) -> ManualCartAssignmentResponse:
    state = _state(request)
    with state.lock:
        if not state.picking_completed: raise HTTPException(status_code=409, detail="run tote optimization first")
        if not any(t.tote_id == tote_id for t in state.totes): raise HTTPException(status_code=404, detail=f"tote {tote_id} was not found")
        cart_id = payload.cart_id
        if not isinstance(cart_id, str) or not cart_id: raise HTTPException(status_code=400, detail="cart_id is required")
        cart = next((c for c in state.carts if c.cart_id == cart_id), None)
        if cart is None: raise HTTPException(status_code=404, detail=f"cart {cart_id} was not found")
        tote = next(t for t in state.totes if t.tote_id == tote_id)
        for order_id in tote.order_ids:
            related = [member for member in state.totes if order_id in member.order_ids]
            conflicts = sorted({member.cart_id for member in related if member.cart_id not in (None, cart_id)})
            if conflicts:
                raise HTTPException(status_code=409, detail=f"assigning tote {tote_id} would split order {order_id} across carts {conflicts} and {cart_id}")
        try: _plan_service(state).assign_tote_to_cart(tote_id, cart_id)
        except PlanAssignmentError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        _refresh_cart_result(state)
        return ManualCartAssignmentResponse(tote=tote_to_response(next(t for t in state.totes if t.tote_id == tote_id)), cart=cart_to_response(cart, _cart_split_ids(state.last_cart_result)))


@router.post("/api/picking/totes/{tote_id}/remove-cart", response_model=ToteResponse)
def remove_cart(tote_id: str, request: Request) -> ToteResponse:
    state = _state(request)
    with state.lock:
        if not state.picking_completed: raise HTTPException(status_code=409, detail="run tote optimization first")
        if not any(t.tote_id == tote_id for t in state.totes): raise HTTPException(status_code=404, detail=f"tote {tote_id} was not found")
        try: _plan_service(state).remove_tote_from_cart(tote_id)
        except PlanAssignmentError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        _refresh_cart_result(state)
        return tote_to_response(next(t for t in state.totes if t.tote_id == tote_id))


@router.post("/api/flights/totes/{tote_id}/flight", response_model=ManualFlightAssignmentResponse)
def assign_flight(tote_id: str, payload: FlightAssignmentRequest, request: Request) -> ManualFlightAssignmentResponse:
    state = _state(request)
    with state.lock:
        if not state.picking_completed: raise HTTPException(status_code=409, detail="run tote optimization first")
        flight_id = payload.flight_id
        if not any(cap.departure_id == flight_id for cap in state.capacities_for_active(state.active_stage)):
            raise HTTPException(status_code=404, detail=f"flight {flight_id} was not found")
        if state.last_flight_result is None: raise HTTPException(status_code=409, detail="run flight optimization first")
        tote = next((t for t in state.totes if t.tote_id == tote_id), None)
        if tote is None: raise HTTPException(status_code=404, detail=f"tote {tote_id} was not found")
        plan = next((p for p in state.flight_plans if p.flight_id == flight_id), None)
        if plan is None: raise HTTPException(status_code=404, detail=f"flight {flight_id} was not found")
        service = _plan_service(state)
        try:
            proposed = [t.tote_id for t in plan.totes]
            if tote.flight_id != flight_id: proposed.append(tote_id)
            check = service.can_assign_totes_to_flight(proposed, flight_id)
            if not check.feasible:
                raise HTTPException(status_code=409, detail={"message": "flight capacity would be exceeded", "exceeded_constraints": check.exceeded_constraints, "remaining_totes": check.remaining_totes, "remaining_payload_lb": check.remaining_payload_lb, "remaining_volume_cuft": check.remaining_volume_cuft})
            service.assign_tote_to_flight(tote_id, flight_id)
        except PlanAssignmentError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        _refresh_flight_result(state)
        metric = next((m for m in state.last_flight_result.per_flight_metrics if m.flight_id == flight_id), None)
        return ManualFlightAssignmentResponse(tote=tote_to_response(tote), flight=flight_plan_to_response(plan, metric))


@router.post("/api/flights/totes/{tote_id}/remove-flight", response_model=ToteResponse)
def remove_flight(tote_id: str, request: Request) -> ToteResponse:
    state = _state(request)
    with state.lock:
        if not state.picking_completed: raise HTTPException(status_code=409, detail="run tote optimization first")
        if state.last_flight_result is None: raise HTTPException(status_code=409, detail="run flight optimization first")
        tote = next((t for t in state.totes if t.tote_id == tote_id), None)
        if tote is None: raise HTTPException(status_code=404, detail=f"tote {tote_id} was not found")
        try: _plan_service(state).remove_tote_from_flight(tote_id)
        except PlanAssignmentError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        _refresh_flight_result(state)
        return tote_to_response(tote)
