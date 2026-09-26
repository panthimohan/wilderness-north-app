"""Shared domain models for order intake, picking, and flight planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
import re
from typing import Optional


def _nonnegative_integer(value: int, name: str, *, allow_zero: bool = True) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or (not allow_zero and value == 0):
        rule = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {rule}")


@dataclass(frozen=True, slots=True)
class OrderItem:
    """One source CSV item row; row identity distinguishes identical products."""

    order_id: int
    household_id: int
    batch_id: int
    order_date: date
    destination_community: str
    product_id: int | str
    product_name: str
    length_in: float
    width_in: float
    height_in: float
    weight_lb: float
    order_item_id: str
    source_quantity: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.order_item_id, str) or not self.order_item_id.strip():
            raise ValueError("order_item_id is required")
        _nonnegative_integer(self.source_quantity, "source_quantity", allow_zero=False)
        for name in ("length_in", "width_in", "height_in", "weight_lb"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")

    @property
    def volume_in3(self) -> float:
        return self.length_in * self.width_in * self.height_in

    @property
    def volume_ft3(self) -> float:
        return self.volume_in3 / 1728.0

    @property
    def item_volume_in3(self) -> float:
        return self.volume_in3

    @property
    def item_volume_ft3(self) -> float:
        return self.volume_ft3


@dataclass(slots=True)
class HouseholdOrder:
    """One order, keyed by order_id; a household may place multiple orders."""

    order_id: int
    household_id: int
    batch_id: int
    order_date: date
    destination_community: str
    items: list[OrderItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        for item in self.items:
            if (item.order_id, item.household_id, item.batch_id, item.order_date, item.destination_community) != (
                self.order_id, self.household_id, self.batch_id, self.order_date, self.destination_community
            ):
                raise ValueError(f"item metadata does not match order {self.order_id}")
        ids = [item.order_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate order_item_id in order {self.order_id}")

    @property
    def weight_lb(self) -> float:
        return sum(item.weight_lb * item.source_quantity for item in self.items)

    @property
    def volume_in3(self) -> float:
        return sum(item.volume_in3 * item.source_quantity for item in self.items)

    @property
    def volume_ft3(self) -> float:
        return self.volume_in3 / 1728.0

    @property
    def calculated_weight_lb(self) -> float:
        return self.weight_lb

    @property
    def calculated_volume_ft3(self) -> float:
        return self.volume_ft3


@dataclass(frozen=True, slots=True)
class ToteItem:
    """Integer allocation of a source order item to one tote."""

    item: OrderItem
    quantity: int = 1

    def __post_init__(self) -> None:
        _nonnegative_integer(self.quantity, "allocation quantity")
        if self.quantity > self.item.source_quantity:
            raise ValueError(
                f"allocation for {self.item.order_item_id} exceeds source quantity "
                f"({self.quantity} > {self.item.source_quantity})"
            )

    @property
    def order_item_id(self) -> str:
        return self.item.order_item_id

    @property
    def order_id(self) -> int:
        return self.item.order_id

    @property
    def household_id(self) -> int:
        return self.item.household_id

    @property
    def weight_lb(self) -> float:
        return self.item.weight_lb * self.quantity

    @property
    def volume_in3(self) -> float:
        return self.item.volume_in3 * self.quantity

    @property
    def volume_ft3(self) -> float:
        return self.volume_in3 / 1728.0


@dataclass(slots=True)
class Tote:
    tote_id: str
    items: list[ToteItem] = field(default_factory=list)
    cart_id: Optional[str] = None
    flight_id: Optional[int] = None

    @property
    def order_ids(self) -> list[int]:
        return sorted({allocation.order_id for allocation in self.items if allocation.quantity})

    @property
    def household_ids(self) -> list[int]:
        return sorted({allocation.household_id for allocation in self.items if allocation.quantity})

    @property
    def weight_lb(self) -> float:
        return sum(allocation.weight_lb for allocation in self.items)

    @property
    def volume_in3(self) -> float:
        return sum(allocation.volume_in3 for allocation in self.items)

    @property
    def volume_ft3(self) -> float:
        return self.volume_in3 / 1728.0

    @property
    def volume_fill_pct(self) -> float:
        from backend.config import TOTE_VOLUME_IN3

        return 100.0 * self.volume_in3 / TOTE_VOLUME_IN3 if TOTE_VOLUME_IN3 else 0.0

    @property
    def calculated_weight_lb(self) -> float:
        return self.weight_lb

    @property
    def calculated_volume_ft3(self) -> float:
        return self.volume_ft3


@dataclass(slots=True)
class Cart:
    cart_id: str
    totes: tuple[Tote, ...] = ()
    tote_capacity: int = 5

    def __post_init__(self) -> None:
        _nonnegative_integer(self.tote_capacity, "tote_capacity", allow_zero=False)
        self.totes = tuple(self.totes)
        if len({t.tote_id for t in self.totes}) != len(self.totes):
            raise ValueError(f"duplicate tote membership in cart {self.cart_id}")
        if len(self.totes) > self.tote_capacity:
            raise ValueError(f"cart {self.cart_id} exceeds its tote capacity")
        for tote in self.totes:
            if tote.cart_id not in (None, self.cart_id):
                raise ValueError(f"tote {tote.tote_id} is already assigned to cart {tote.cart_id}")
            tote.cart_id = self.cart_id

    @property
    def order_ids(self) -> list[int]:
        return sorted({order_id for tote in self.totes for order_id in tote.order_ids})


@dataclass(slots=True)
class FlightCapacity:
    departure_id: int
    departure_date: date
    available_totes: int
    available_payload_lb: float
    available_volume_cuft: float | None = None
    flight_cost: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        _nonnegative_integer(self.available_totes, "available_totes")
        for name in ("available_payload_lb", "available_volume_cuft"):
            value = getattr(self, name)
            if value is None and name == "available_volume_cuft":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number or None when unspecified")
        if self.flight_cost is not None and (
            isinstance(self.flight_cost, bool)
            or not isinstance(self.flight_cost, (int, float))
            or not math.isfinite(self.flight_cost)
            or self.flight_cost < 0
        ):
            raise ValueError("flight_cost must be a finite non-negative number or None")
        if self.currency is not None:
            currency = self.currency.strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("currency must be a three-letter currency code")
            self.currency = currency


@dataclass(slots=True)
class FlightPlan:
    flight_id: int
    capacity: FlightCapacity
    totes: tuple[Tote, ...] = ()
    source_orders: tuple[HouseholdOrder, ...] = ()
    # Item quantities loaded on earlier departures in this same rolling plan.
    previously_loaded_quantities: dict[str, int] = field(default_factory=dict)
    # Unshipped item quantities explicitly assigned to a later departure.
    rollover_quantities: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.totes = tuple(self.totes)
        self.source_orders = tuple(self.source_orders)
        if len({t.tote_id for t in self.totes}) != len(self.totes):
            raise ValueError(f"duplicate tote membership in flight {self.flight_id}")
        source_items = {item.order_item_id: item for order in self.source_orders for item in order.items}
        source_item_count = sum(len(order.items) for order in self.source_orders)
        if len(source_items) != source_item_count:
            raise ValueError("source order items must have unique order_item_id values")
        order_ids = [order.order_id for order in self.source_orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("source_orders must have unique order_id values")
        for tote in self.totes:
            if tote.flight_id not in (None, self.flight_id):
                raise ValueError(f"tote {tote.tote_id} is already assigned to flight {tote.flight_id}")
        self.set_rollover_state(
            previously_loaded_quantities=self.previously_loaded_quantities,
            rollover_quantities=self.rollover_quantities,
        )
        if source_items:
            for item_id in self.loaded_item_quantities:
                if item_id not in source_items:
                    raise ValueError(f"flight tote references unknown source item {item_id}")
        if self.tote_count > self.capacity.available_totes:
            raise ValueError(f"flight {self.flight_id} exceeds tote capacity")
        if self.total_weight_lb > self.capacity.available_payload_lb:
            raise ValueError(f"flight {self.flight_id} exceeds payload capacity")
        if (self.capacity.available_volume_cuft is not None
                and self.total_volume_cuft > self.capacity.available_volume_cuft):
            raise ValueError(f"flight {self.flight_id} exceeds volume capacity")
        for tote in self.totes:
            tote.flight_id = self.flight_id

    def set_rollover_state(
        self,
        *,
        previously_loaded_quantities: dict[str, int],
        rollover_quantities: dict[str, int],
    ) -> None:
        """Atomically set prior loads and quantities explicitly rolling forward.

        Rollover quantities are the unshipped remainder after prior and current
        allocations. The method validates before replacing either state map.
        """
        previous = dict(previously_loaded_quantities)
        rollover = dict(rollover_quantities)
        source_items = {item.order_item_id: item for order in self.source_orders for item in order.items}
        if not source_items:
            source_items = {
                allocation.order_item_id: allocation.item
                for tote in self.totes
                for allocation in tote.items
            }
        for label, allocations in (("previously_loaded_quantities", previous), ("rollover_quantities", rollover)):
            for item_id, quantity in allocations.items():
                _nonnegative_integer(quantity, f"{label}[{item_id}]")
                if self.source_orders and item_id not in source_items:
                    raise ValueError(f"{label} references unknown item {item_id}")
        loaded = self.loaded_item_quantities
        for item_id in set(previous) | set(rollover) | set(loaded):
            item = source_items.get(item_id)
            if item is None:
                # Without source orders only item IDs present in current totes
                # can be checked against a known source quantity.
                if item_id in loaded:
                    item = next(
                        allocation.item
                        for tote in self.totes
                        for allocation in tote.items
                        if allocation.order_item_id == item_id
                    )
                else:
                    continue
            total = previous.get(item_id, 0) + loaded.get(item_id, 0) + rollover.get(item_id, 0)
            if total > item.source_quantity:
                raise ValueError(f"loaded plus rollover quantity exceeds source for {item_id}")
        self.previously_loaded_quantities = previous
        self.rollover_quantities = rollover

    @property
    def remaining_item_quantities(self) -> dict[str, int]:
        """Source quantities not yet loaded, including quantities marked rollover."""
        source_items = {item.order_item_id: item for order in self.source_orders for item in order.items}
        if not source_items:
            source_items = {
                allocation.order_item_id: allocation.item
                for tote in self.totes
                for allocation in tote.items
            }
        loaded = self.loaded_item_quantities
        return {
            item_id: item.source_quantity
            - self.previously_loaded_quantities.get(item_id, 0)
            - loaded.get(item_id, 0)
            for item_id, item in source_items.items()
        }

    @property
    def loaded_item_quantities(self) -> dict[str, int]:
        quantities: dict[str, int] = {}
        for tote in self.totes:
            for allocation in tote.items:
                if allocation.quantity:
                    quantities[allocation.order_item_id] = quantities.get(allocation.order_item_id, 0) + allocation.quantity
        return quantities

    @property
    def order_ids(self) -> list[int]:
        return sorted({order_id for tote in self.totes for order_id in tote.order_ids})

    @property
    def fully_loaded_order_ids(self) -> list[int]:
        """Orders completely delivered by this and any explicitly prior loads."""
        shipped: dict[int, int] = {}
        expected: dict[int, int] = {}
        for order in self.source_orders:
            expected[order.order_id] = sum(item.source_quantity for item in order.items)
            shipped[order.order_id] = 0
            for item in order.items:
                shipped[order.order_id] += self.previously_loaded_quantities.get(item.order_item_id, 0)
                shipped[order.order_id] += self.loaded_item_quantities.get(item.order_item_id, 0)
        return sorted(order_id for order_id, total in expected.items() if total and shipped[order_id] == total)

    @property
    def partially_loaded_order_ids(self) -> list[int]:
        shipped: dict[int, int] = {}
        expected: dict[int, int] = {}
        for order in self.source_orders:
            expected[order.order_id] = sum(item.source_quantity for item in order.items)
            shipped[order.order_id] = sum(
                self.previously_loaded_quantities.get(item.order_item_id, 0)
                + self.loaded_item_quantities.get(item.order_item_id, 0)
                for item in order.items
            )
        return sorted(order_id for order_id, total in expected.items() if 0 < shipped[order_id] < total)

    @property
    def rolled_over_order_ids(self) -> list[int]:
        order_by_item = {
            item.order_item_id: order.order_id
            for order in self.source_orders
            for item in order.items
        }
        return sorted({order_by_item[item_id] for item_id, quantity in self.rollover_quantities.items() if quantity})

    @property
    def total_weight_lb(self) -> float:
        return sum(tote.weight_lb for tote in self.totes)

    @property
    def total_volume_cuft(self) -> float:
        return sum(tote.volume_ft3 for tote in self.totes)

    @property
    def tote_count(self) -> int:
        return len(self.totes)

    @property
    def tote_utilization_pct(self) -> float:
        cap = self.capacity.available_totes
        return 100.0 * self.tote_count / cap if cap else 0.0

    @property
    def payload_utilization_pct(self) -> float:
        cap = self.capacity.available_payload_lb
        return 100.0 * self.total_weight_lb / cap if cap else 0.0

    @property
    def volume_utilization_pct(self) -> Optional[float]:
        cap = self.capacity.available_volume_cuft
        return 100.0 * self.total_volume_cuft / cap if cap else (0.0 if cap == 0 else None)

    @property
    def remaining_totes(self) -> int:
        return self.capacity.available_totes - self.tote_count

    @property
    def remaining_payload_lb(self) -> float:
        return self.capacity.available_payload_lb - self.total_weight_lb

    @property
    def remaining_volume_cuft(self) -> Optional[float]:
        if self.capacity.available_volume_cuft is None:
            return None
        return self.capacity.available_volume_cuft - self.total_volume_cuft

    @property
    def remaining_capacity(self) -> dict[str, float | int | None]:
        return {"totes": self.remaining_totes, "payload_lb": self.remaining_payload_lb, "volume_cuft": self.remaining_volume_cuft}

    @property
    def tightest_capacity_dimension(self) -> Optional[str]:
        """The most utilized dimension; this alone does not prove a hard binding limit."""
        if not self.totes:
            return None
        values = {"totes": self.tote_utilization_pct, "payload": self.payload_utilization_pct}
        if self.volume_utilization_pct is not None:
            values["volume"] = self.volume_utilization_pct
        return max(values, key=values.get)
