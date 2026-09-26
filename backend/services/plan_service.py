"""Validated cart and flight membership operations for a shared tote plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.models import Cart, FlightPlan, Tote


class PlanAssignmentError(ValueError):
    """Raised when a tote assignment would make the canonical plan inconsistent."""


@dataclass(slots=True)
class FlightCapacityCheck:
    feasible: bool
    tote_count_used: int
    payload_used_lb: float
    volume_used_cuft: float
    remaining_totes: int
    remaining_payload_lb: float
    remaining_volume_cuft: float | None
    exceeded_constraints: list[str]


class PlanService:
    """Own canonical tote/cart/flight references and synchronize assignments."""

    def __init__(self, totes: list[Tote], carts: list[Cart], flights: list[FlightPlan]) -> None:
        self.totes = self._index(totes, "tote_id", "tote")
        self.carts = self._index(carts, "cart_id", "cart")
        self.flights = self._index(flights, "flight_id", "flight")
        # Rebind memberships to canonical tote objects and reject duplicate owners.
        cart_owner: dict[str, str] = {}
        flight_owner: dict[str, int] = {}
        for cart in self.carts.values():
            members: list[Tote] = []
            for tote in cart.totes:
                canonical = self._get(self.totes, tote.tote_id, "tote")
                if canonical.tote_id in cart_owner:
                    raise PlanAssignmentError(f"tote {canonical.tote_id} belongs to multiple carts")
                cart_owner[canonical.tote_id] = cart.cart_id
                canonical.cart_id = cart.cart_id
                members.append(canonical)
            cart.totes = tuple(members)
        for plan in self.flights.values():
            members = []
            for tote in plan.totes:
                canonical = self._get(self.totes, tote.tote_id, "tote")
                if canonical.tote_id in flight_owner:
                    raise PlanAssignmentError(f"tote {canonical.tote_id} belongs to multiple flights")
                flight_owner[canonical.tote_id] = plan.flight_id
                canonical.flight_id = plan.flight_id
                members.append(canonical)
            plan.totes = tuple(members)
        for tote in self.totes.values():
            if tote.cart_id is not None and cart_owner.get(tote.tote_id) != tote.cart_id:
                raise PlanAssignmentError(f"tote {tote.tote_id} has a cart_id without matching cart membership")
            if tote.flight_id is not None and flight_owner.get(tote.tote_id) != tote.flight_id:
                raise PlanAssignmentError(f"tote {tote.tote_id} has a flight_id without matching flight membership")

    @staticmethod
    def _index(objects: list, id_field: str, label: str) -> dict:
        result = {}
        for obj in objects:
            object_id = getattr(obj, id_field)
            if object_id in result:
                raise PlanAssignmentError(f"duplicate {label} ID: {object_id}")
            result[object_id] = obj
        return result

    @staticmethod
    def _get(index: dict, object_id, label: str):
        try:
            return index[object_id]
        except KeyError as exc:
            raise PlanAssignmentError(f"unknown {label}: {object_id}") from exc

    def assign_tote_to_cart(self, tote_id: str, cart_id: str) -> None:
        tote = self._get(self.totes, tote_id, "tote")
        cart = self._get(self.carts, cart_id, "cart")
        if tote.cart_id == cart_id:
            return
        if tote.cart_id is not None:
            raise PlanAssignmentError(f"tote {tote_id} is already assigned to cart {tote.cart_id}")
        if len(cart.totes) >= cart.tote_capacity:
            raise PlanAssignmentError(f"cart {cart_id} is at capacity")
        cart.totes = (*cart.totes, tote)
        tote.cart_id = cart_id

    def remove_tote_from_cart(self, tote_id: str) -> None:
        tote = self._get(self.totes, tote_id, "tote")
        if tote.cart_id is None:
            return
        cart = self._get(self.carts, tote.cart_id, "cart")
        cart.totes = tuple(member for member in cart.totes if member.tote_id != tote_id)
        tote.cart_id = None

    def can_assign_totes_to_flight(
        self, tote_ids: Iterable[str], flight_id: int
    ) -> FlightCapacityCheck:
        """Check a complete proposed load without changing plan or tote state.

        ``tote_ids`` describes the full candidate load for this flight, replacing
        its current membership for the calculation. Existing assignments to this
        same flight are allowed; assignment to a different flight is an error.
        Exceeded labels are ``tote_count``, ``payload``, and ``volume``.
        """
        plan = self._get(self.flights, flight_id, "flight")
        ids = list(tote_ids)
        if len(ids) != len(set(ids)):
            raise PlanAssignmentError("candidate flight load contains duplicate tote IDs")
        candidates = [self._get(self.totes, tote_id, "tote") for tote_id in ids]
        for tote in candidates:
            if tote.flight_id not in (None, flight_id):
                raise PlanAssignmentError(
                    f"tote {tote.tote_id} is already assigned to flight {tote.flight_id}"
                )

        capacity = plan.capacity
        tote_count = len(candidates)
        payload = sum(tote.weight_lb for tote in candidates)
        volume = sum(tote.volume_ft3 for tote in candidates)
        exceeded = []
        if tote_count > capacity.available_totes:
            exceeded.append("tote_count")
        if payload > capacity.available_payload_lb:
            exceeded.append("payload")
        if capacity.available_volume_cuft is not None and volume > capacity.available_volume_cuft:
            exceeded.append("volume")
        return FlightCapacityCheck(
            feasible=not exceeded,
            tote_count_used=tote_count,
            payload_used_lb=payload,
            volume_used_cuft=volume,
            remaining_totes=capacity.available_totes - tote_count,
            remaining_payload_lb=capacity.available_payload_lb - payload,
            remaining_volume_cuft=(
                capacity.available_volume_cuft - volume
                if capacity.available_volume_cuft is not None else None
            ),
            exceeded_constraints=exceeded,
        )

    def assign_tote_to_flight(self, tote_id: str, flight_id: int) -> None:
        tote = self._get(self.totes, tote_id, "tote")
        plan = self._get(self.flights, flight_id, "flight")
        if tote.flight_id == flight_id:
            return
        if tote.flight_id is not None:
            raise PlanAssignmentError(f"tote {tote_id} is already assigned to flight {tote.flight_id}")
        capacity = plan.capacity
        if len(plan.totes) + 1 > capacity.available_totes:
            raise PlanAssignmentError(f"flight {flight_id} exceeds tote capacity")
        if plan.total_weight_lb + tote.weight_lb > capacity.available_payload_lb:
            raise PlanAssignmentError(f"flight {flight_id} exceeds payload capacity")
        if (capacity.available_volume_cuft is not None
                and plan.total_volume_cuft + tote.volume_ft3 > capacity.available_volume_cuft):
            raise PlanAssignmentError(f"flight {flight_id} exceeds volume capacity")
        plan.totes = (*plan.totes, tote)
        tote.flight_id = flight_id

    def remove_tote_from_flight(self, tote_id: str) -> None:
        tote = self._get(self.totes, tote_id, "tote")
        if tote.flight_id is None:
            return
        plan = self._get(self.flights, tote.flight_id, "flight")
        plan.totes = tuple(member for member in plan.totes if member.tote_id != tote_id)
        tote.flight_id = None
