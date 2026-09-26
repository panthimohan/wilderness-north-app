"""In-memory canonical planning session, replaceable by a repository later."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from threading import RLock

from backend import config
from backend.models import Cart, FlightCapacity, FlightPlan, HouseholdOrder, Tote
from backend.services.import_service import ImportDraft
from backend.services.csv_service import (
    load_stage1_orders,
    load_stage2_flight_capacities,
    load_stage2_orders,
)


def build_stage1_flight_capacity(orders: list[HouseholdOrder]) -> FlightCapacity:
    """Build Stage 1's single-flight limits from values stated in the writeup.

    The challenge gives a 90-tote ceiling and estimated 2,877 lb route payload.
    It does not give a usable cargo-volume capacity, so that constraint remains
    unspecified (None). The Stage 1 dataset's latest order date is used only as
    the optimizer's departure-eligibility date because no Stage 1 departure
    date is supplied.
    """
    departure_date = max((order.order_date for order in orders), default=date.min)
    return FlightCapacity(
        departure_id=1,
        departure_date=departure_date,
        available_totes=config.DEFAULT_AIRCRAFT_MAX_TOTES,
        available_payload_lb=config.DEFAULT_AIRCRAFT_PAYLOAD_LB,
        available_volume_cuft=None,
    )


@dataclass(slots=True)
class PlanningState:
    stage1_orders: list[HouseholdOrder]
    stage2_orders: list[HouseholdOrder]
    # Existing public field remains the Stage 2 CSV capacities.
    flight_capacities: list[FlightCapacity]
    active_stage: str = "stage1"
    active_mode: str = "challenge"
    user_orders: list[HouseholdOrder] = field(default_factory=list)
    user_flight_capacities: list[FlightCapacity] = field(default_factory=list)
    active_dataset_name: str | None = None
    active_dataset_source: str | None = None
    user_imported_at: str | None = None
    user_order_source_name: str | None = None
    user_capacity_source_name: str | None = None
    import_drafts: dict[str, ImportDraft] = field(default_factory=dict)
    active_orders: list[HouseholdOrder] = field(default_factory=list)
    totes: list[Tote] = field(default_factory=list)
    carts: list[Cart] = field(default_factory=list)
    flight_plans: list[FlightPlan] = field(default_factory=list)
    # Canonical progress keyed by stable order_item_id; independent of packing.
    picked_quantities: dict[str, int] = field(default_factory=dict)
    last_tote_result: object | None = None
    last_cart_result: object | None = None
    last_flight_result: object | None = None
    stage1_flight_capacities: list[FlightCapacity] = field(default_factory=list)
    lock: RLock = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        self.active_orders = self.orders_for_stage(self.active_stage)
        self.active_dataset_name = f"{self.active_stage.replace('stage', 'Stage ')} challenge data"
        self.active_dataset_source = "Challenge dataset"
        if not self.stage1_flight_capacities:
            self.stage1_flight_capacities = [build_stage1_flight_capacity(self.stage1_orders)]

    def orders_for_stage(self, stage: str) -> list[HouseholdOrder]:
        if stage == "stage1":
            return self.stage1_orders
        if stage == "stage2":
            return self.stage2_orders
        raise ValueError(f"unsupported stage: {stage}")

    def capacities_for_stage(self, stage: str) -> list[FlightCapacity]:
        if stage == "stage1":
            return self.stage1_flight_capacities
        if stage == "stage2":
            return self.flight_capacities
        raise ValueError(f"unsupported stage: {stage}")

    def orders_for_active(self, stage: str | None = None) -> list[HouseholdOrder]:
        return self.user_orders if self.active_mode == "user" else self.orders_for_stage(stage or self.active_stage)

    def capacities_for_active(self, stage: str | None = None) -> list[FlightCapacity]:
        return self.user_flight_capacities if self.active_mode == "user" else self.capacities_for_stage(stage or self.active_stage)

    def select_mode(self, mode: str) -> None:
        if mode not in {"challenge", "user"}:
            raise ValueError(f"unsupported mode: {mode}")
        self.clear_plan()
        self.active_mode = mode
        if mode == "user":
            self.active_orders = self.user_orders
            names = [name for name in (self.user_order_source_name, self.user_capacity_source_name) if name]
            self.active_dataset_name = ", ".join(names) if names else "No user files imported"
            self.active_dataset_source = "User upload"
        else:
            self.active_orders = self.orders_for_stage(self.active_stage)
            self.active_dataset_name = f"{self.active_stage.replace('stage', 'Stage ')} challenge data"
            self.active_dataset_source = "Challenge dataset"

    def clear_plan(self) -> None:
        self.totes = []
        self.carts = []
        self.flight_plans = []
        self.last_tote_result = None
        self.last_cart_result = None
        self.last_flight_result = None

    def select_stage(self, stage: str) -> None:
        orders = self.orders_for_stage(stage)
        if stage != self.active_stage:
            self.clear_plan()
        self.active_stage = stage
        self.active_orders = orders
        if self.active_mode == "challenge":
            self.active_dataset_name = f"{stage.replace('stage', 'Stage ')} challenge data"
            self.active_dataset_source = "Challenge dataset"

    def reset_session(self) -> None:
        self.clear_plan()
        self.picked_quantities.clear()
        self.active_mode = "challenge"
        self.active_stage = "stage1"
        self.active_orders = self.stage1_orders
        self.active_dataset_name = "Stage 1 challenge data"
        self.active_dataset_source = "Challenge dataset"

    @property
    def picking_completed(self) -> bool:
        return self.last_tote_result is not None

    @property
    def cart_optimization_completed(self) -> bool:
        return self.last_cart_result is not None

    @property
    def flight_optimization_completed(self) -> bool:
        return self.last_flight_result is not None


def initialize_state() -> PlanningState:
    """Load CSV source data and stage-specific capacities; run no optimizers."""
    stage1_orders = load_stage1_orders()
    stage2_orders = load_stage2_orders()
    return PlanningState(
        stage1_orders=stage1_orders,
        stage2_orders=stage2_orders,
        flight_capacities=load_stage2_flight_capacities(),
        stage1_flight_capacities=[build_stage1_flight_capacity(stage1_orders)],
    )


def reset_state(state: PlanningState) -> PlanningState:
    with state.lock:
        state.reset_session()
    return state


def select_stage(state: PlanningState, stage: str) -> PlanningState:
    if stage not in {"stage1", "stage2"}:
        raise ValueError(f"unsupported stage: {stage}")
    with state.lock:
        state.select_stage(stage)
    return state
