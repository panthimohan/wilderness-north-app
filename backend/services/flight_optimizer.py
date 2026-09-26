"""Deterministic flight assignment using totes as the physical load unit.

Aircraft constraints are tote count, payload weight, and cargo volume; no tote
weight limit is applied. Candidate feasibility is checked with PlanService
before any flight assignment is committed. Orders sharing totes form an
indivisible component for this heuristic: a component is either loaded whole
on one eligible departure or rolled over whole. This keeps completion and
rollover accounting clear, but is not a globally optimal schedule.

Eligibility assumes an order dated on or before a departure is available for
that departure. No cutoff time or delivery promise is inferred. Volume is the
sum of item-box volumes and does not prove physical packing geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from backend.models import FlightCapacity, FlightPlan, HouseholdOrder, Tote
from backend.services.csv_service import validate_tote_allocations
from backend.services.plan_service import PlanAssignmentError, PlanService


@dataclass(frozen=True, slots=True)
class FlightMetrics:
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


@dataclass(slots=True)
class FlightOptimizationResult:
    flight_plans: list[FlightPlan]
    total_flights: int
    assigned_tote_ids: list[str]
    unassigned_tote_ids: list[str]
    complete_order_ids: list[int]
    rollover_order_ids: list[int]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    feasible: bool = True
    per_flight_metrics: list[FlightMetrics] = field(default_factory=list)


class FlightOptimizationError(ValueError):
    """Raised for invalid input plans that cannot be traced safely."""


def _connected_tote_groups(totes: Sequence[Tote]) -> list[tuple[list[str], list[int]]]:
    """Return components linked by orders represented in multiple totes."""
    parent = {tote.tote_id: tote.tote_id for tote in totes}
    order_totes: dict[int, set[str]] = {}
    for tote in totes:
        for order_id in tote.order_ids:
            order_totes.setdefault(order_id, set()).add(tote.tote_id)

    def find(tote_id: str) -> str:
        while parent[tote_id] != tote_id:
            parent[tote_id] = parent[parent[tote_id]]
            tote_id = parent[tote_id]
        return tote_id

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            first, second = sorted((ra, rb))
            parent[second] = first

    for tote_ids in order_totes.values():
        ids = sorted(tote_ids)
        for tote_id in ids[1:]:
            union(ids[0], tote_id)

    by_root: dict[str, list[str]] = {}
    for tote_id in sorted(parent):
        by_root.setdefault(find(tote_id), []).append(tote_id)
    groups = []
    for tote_ids in by_root.values():
        orders = sorted(
            order_id for order_id, related_totes in order_totes.items()
            if not related_totes.isdisjoint(tote_ids)
        )
        groups.append((sorted(tote_ids), orders))
    return groups


def _loaded_quantities(plans: Sequence[FlightPlan]) -> dict[str, int]:
    result: dict[str, int] = {}
    for plan in plans:
        for item_id, quantity in plan.loaded_item_quantities.items():
            result[item_id] = result.get(item_id, 0) + quantity
    return result


def _complete_orders(orders: Sequence[HouseholdOrder], loaded: dict[str, int]) -> list[int]:
    return sorted(
        order.order_id
        for order in orders
        if order.items
        and all(loaded.get(item.order_item_id, 0) == item.source_quantity for item in order.items)
    )


def _remaining_by_item(orders: Sequence[HouseholdOrder], loaded: dict[str, int]) -> dict[str, int]:
    return {
        item.order_item_id: item.source_quantity - loaded.get(item.order_item_id, 0)
        for order in orders
        for item in order.items
        if item.source_quantity - loaded.get(item.order_item_id, 0) > 0
    }


def _flight_metrics(
    plan: FlightPlan,
    newly_complete_order_ids: list[int],
) -> FlightMetrics:
    capacity = plan.capacity
    return FlightMetrics(
        flight_id=plan.flight_id,
        departure_date=capacity.departure_date,
        tote_ids=sorted(tote.tote_id for tote in plan.totes),
        tote_count=plan.tote_count,
        tote_capacity=capacity.available_totes,
        used_payload_lb=plan.total_weight_lb,
        remaining_payload_lb=plan.remaining_payload_lb,
        used_volume_cuft=plan.total_volume_cuft,
        remaining_volume_cuft=plan.remaining_volume_cuft,
        payload_utilization_percent=plan.payload_utilization_pct,
        volume_utilization_percent=plan.volume_utilization_pct,
        tote_utilization_percent=plan.tote_utilization_pct,
        order_ids=sorted({order_id for tote in plan.totes for order_id in tote.order_ids}),
        household_ids=sorted({
            allocation.household_id
            for tote in plan.totes
            for allocation in tote.items
            if allocation.quantity
        }),
        complete_order_ids=newly_complete_order_ids,
        rollover_order_ids=plan.rolled_over_order_ids,
        tightest_capacity_dimension=plan.tightest_capacity_dimension,
    )


def _existing_carts_for_totes(totes: Sequence[Tote]):
    """Reconstruct current cart memberships so PlanService preserves tote state."""
    cart_members: dict[str, list[Tote]] = {}
    for tote in totes:
        if tote.cart_id is not None:
            cart_members.setdefault(tote.cart_id, []).append(tote)
    from backend.models import Cart

    return [
        Cart(cart_id=cart_id, totes=tuple(members), tote_capacity=max(1, len(members)))
        for cart_id, members in sorted(cart_members.items())
    ]


def optimize_flights(
    totes: Sequence[Tote],
    orders: Sequence[HouseholdOrder],
    flight_capacities: Sequence[FlightCapacity],
    *,
    stage: str = "stage2",
) -> FlightOptimizationResult:
    """Assign order-linked tote groups to eligible flights deterministically.

    ``stage`` is retained as result context at the call site; assignment policy
    is determined by the supplied order and departure dates/capacities.
    """
    del stage  # The same explicit scheduling policy applies to both stages.
    totes, orders, capacities = list(totes), list(orders), list(flight_capacities)
    if len({tote.tote_id for tote in totes}) != len(totes):
        raise FlightOptimizationError("input contains duplicate tote IDs")
    if len({order.order_id for order in orders}) != len(orders):
        raise FlightOptimizationError("input contains duplicate order_id values")
    if len({capacity.departure_id for capacity in capacities}) != len(capacities):
        raise FlightOptimizationError("flight capacities contain duplicate departure_id values")
    if any(tote.flight_id is not None for tote in totes):
        raise FlightOptimizationError("input totes already have flight assignments; provide fresh totes")
    try:
        validate_tote_allocations(orders, totes, require_complete=True)
    except ValueError as exc:
        raise FlightOptimizationError(f"invalid tote/order traceability: {exc}") from exc

    order_by_id = {order.order_id: order for order in orders}
    tote_by_id = {tote.tote_id: tote for tote in totes}
    groups = _connected_tote_groups(totes)
    groups.sort(key=lambda group: (
        -len(group[0]),
        -sum(tote_by_id[tote_id].volume_ft3 for tote_id in group[0]),
        -sum(tote_by_id[tote_id].weight_lb for tote_id in group[0]),
        group[1],
        group[0],
    ))
    ordered_capacities = sorted(capacities, key=lambda c: (c.departure_date, c.departure_id))
    plans = [
        FlightPlan(
            flight_id=capacity.departure_id,
            capacity=capacity,
            source_orders=tuple(orders),
        )
        for capacity in ordered_capacities
    ]
    try:
        plan_service = PlanService(totes, _existing_carts_for_totes(totes), plans)
    except (PlanAssignmentError, ValueError) as exc:
        raise FlightOptimizationError(f"could not initialize canonical tote plan: {exc}") from exc

    warnings = [
        "Scheduling assumes orders dated on or before a departure are eligible; no cutoff time is inferred.",
        "Order-linked tote groups are loaded whole on one flight or rolled over whole; this heuristic does not split a group across flights.",
        "Volume is summed item-box volume and does not verify physical aircraft packing geometry.",
    ]
    failed_checks: set[tuple[int, tuple[str, ...], tuple[int, ...]]] = set()
    order_flight_dates = {order_id: order.order_date for order_id, order in order_by_id.items()}

    newly_complete_by_flight: dict[int, list[int]] = {}
    item_order_id = {item.order_item_id: order.order_id for order in orders for item in order.items}
    for plan_index, plan in enumerate(plans):
        departure_date = plan.capacity.departure_date
        candidates = []
        for tote_ids, group_order_ids in groups:
            if all(tote_by_id[tote_id].flight_id is None for tote_id in tote_ids):
                if all(order_flight_dates[order_id] <= departure_date for order_id in group_order_ids):
                    candidates.append((tote_ids, group_order_ids))

        made_progress = True
        while made_progress:
            made_progress = False
            for tote_ids, group_order_ids in candidates:
                if not all(tote_by_id[tote_id].flight_id is None for tote_id in tote_ids):
                    continue
                proposed_ids = [tote.tote_id for tote in plan.totes] + tote_ids
                check = plan_service.can_assign_totes_to_flight(proposed_ids, plan.flight_id)
                if not check.feasible:
                    failed_checks.add((plan.flight_id, tuple(check.exceeded_constraints), tuple(group_order_ids)))
                    continue
                # Commit only after the canonical non-mutating check accepts it.
                for tote_id in tote_ids:
                    plan_service.assign_tote_to_flight(tote_id, plan.flight_id)
                made_progress = True

        prior_loaded = _loaded_quantities(plans[:plan_index])
        loaded_through_this_flight = _loaded_quantities(plans[: plan_index + 1])
        before_complete = set(_complete_orders(orders, prior_loaded))
        newly_complete = sorted(set(_complete_orders(orders, loaded_through_this_flight)) - before_complete)
        newly_complete_by_flight[plan.flight_id] = newly_complete
        current_rollover = {
            item_id: quantity
            for item_id, quantity in _remaining_by_item(orders, loaded_through_this_flight).items()
            if order_flight_dates[item_order_id[item_id]] <= departure_date
        }
        plan.set_rollover_state(
            previously_loaded_quantities=prior_loaded,
            rollover_quantities=current_rollover,
        )

    for flight_id, constraints, group_order_ids in sorted(failed_checks):
        warnings.append(
            f"Departure {flight_id}: orders {list(group_order_ids)} were deferred; "
            f"candidate exceeded {', '.join(constraints)} capacity."
        )

    assigned = [tote.tote_id for plan in plans for tote in plan.totes]
    if len(assigned) != len(set(assigned)):
        raise FlightOptimizationError("a physical tote appears on multiple flights")
    if set(assigned) - set(tote_by_id):
        raise FlightOptimizationError("a flight contains an unknown tote")
    unassigned_ids = sorted(tote.tote_id for tote in totes if tote.flight_id is None)
    if set(assigned) & set(unassigned_ids) or set(assigned) | set(unassigned_ids) != set(tote_by_id):
        raise FlightOptimizationError("flight assignment lost or introduced a tote")
    for plan in plans:
        check = plan_service.can_assign_totes_to_flight(
            [tote.tote_id for tote in plan.totes], plan.flight_id
        )
        if not check.feasible:
            raise FlightOptimizationError(
                f"flight {plan.flight_id} exceeds capacity: {', '.join(check.exceeded_constraints)}"
            )

    loaded_total = _loaded_quantities(plans)
    complete_ids = _complete_orders(orders, loaded_total)
    remaining_quantities = _remaining_by_item(orders, loaded_total)
    rollover_ids = sorted({
        order.order_id
        for order in orders
        if any(remaining_quantities.get(item.order_item_id, 0) for item in order.items)
    })
    unassigned_ids = sorted(tote.tote_id for tote in totes if tote.flight_id is None)
    assigned_order_ids = {
        order_id for plan in plans for tote in plan.totes for order_id in tote.order_ids
    }
    if unassigned_ids:
        errors = [
            f"Unable to assign {len(unassigned_ids)} tote(s) within the supplied departures; "
            f"orders remaining/rolling over: {rollover_ids}."
        ]
        feasible = False
    else:
        errors = []
        feasible = True

    # If an order's required totes were split across flights by externally supplied
    # membership, verify completion from item quantities rather than tote presence.
    metrics = [
        _flight_metrics(plan, newly_complete_by_flight[plan.flight_id])
        for plan in plans
    ]
    return FlightOptimizationResult(
        flight_plans=plans,
        total_flights=len(plans),
        assigned_tote_ids=sorted(assigned),
        unassigned_tote_ids=unassigned_ids,
        complete_order_ids=complete_ids,
        rollover_order_ids=rollover_ids,
        warnings=warnings,
        errors=errors,
        feasible=feasible,
        per_flight_metrics=metrics,
    )
