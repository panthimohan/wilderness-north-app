"""Deterministic volume-only heuristic for grouping household orders into totes.

Packing decisions use summed item-box volume (length × width × height). This is
an approximation and does not prove that item shapes physically fit together.
No tote weight limit is applied; weights are measured for later flight planning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from backend import config
from backend.models import HouseholdOrder, OrderItem, Tote, ToteItem
from backend.services.csv_service import validate_tote_allocations


@dataclass(frozen=True, slots=True)
class UnallocatedItem:
    order_item_id: str
    order_id: int
    household_id: int
    product_id: int | str
    product_name: str
    quantity: int
    reason: str


@dataclass(frozen=True, slots=True)
class ToteMetrics:
    tote_id: str
    weight_lb: float
    volume_ft3: float
    fill_percent: float
    order_ids: list[int]
    item_count: int
    allocation_count: int


@dataclass(slots=True)
class ToteOptimizationResult:
    totes: list[Tote]
    total_totes: int
    total_orders: int
    complete_orders: int
    split_orders: int
    unallocated_items: list[UnallocatedItem]
    total_weight_lb: float
    total_volume_ft3: float
    average_fill_percent: float
    warnings: list[str] = field(default_factory=list)
    complete_order_ids: list[int] = field(default_factory=list)
    split_order_ids: list[int] = field(default_factory=list)
    orders_requiring_multiple_totes: list[int] = field(default_factory=list)
    oversized_order_ids: list[int] = field(default_factory=list)
    per_tote_metrics: list[ToteMetrics] = field(default_factory=list)


def _order_volume_in3(order: HouseholdOrder) -> float:
    return sum(item.volume_in3 * item.source_quantity for item in order.items)


def _order_weight_lb(order: HouseholdOrder) -> float:
    return sum(item.weight_lb * item.source_quantity for item in order.items)


def _add_allocation(tote: Tote, item: OrderItem, quantity: int) -> None:
    """Add units to this tote, keeping at most one allocation per item ID/tote."""
    if quantity <= 0:
        return
    for index, existing in enumerate(tote.items):
        if existing.order_item_id == item.order_item_id:
            tote.items[index] = ToteItem(item=item, quantity=existing.quantity + quantity)
            return
    tote.items.append(ToteItem(item=item, quantity=quantity))


def optimize_totes(orders: Sequence[HouseholdOrder]) -> ToteOptimizationResult:
    """Pack orders using complete-order best fit and item-level best-fit decreasing.

    Normal orders are kept intact in one tote when their total item volume fits.
    Oversized orders are assigned unit by unit, largest item volume first, to
    the feasible tote with the least remaining volume. An individual item unit
    larger than the tote volume is reported as unallocated.
    """
    orders = list(orders)
    capacity_in3 = config.TOTE_VOLUME_IN3
    if capacity_in3 <= 0:
        raise ValueError("configured tote volume must be positive")

    order_by_id: dict[int, HouseholdOrder] = {}
    seen_item_ids: set[str] = set()
    for order in orders:
        if order.order_id in order_by_id:
            raise ValueError(f"duplicate order_id in optimizer input: {order.order_id}")
        order_by_id[order.order_id] = order
        for item in order.items:
            if item.order_item_id in seen_item_ids:
                raise ValueError(f"duplicate source order_item_id: {item.order_item_id}")
            seen_item_ids.add(item.order_item_id)
            if item.order_id != order.order_id:
                raise ValueError(f"item {item.order_item_id} does not belong to order {order.order_id}")

    totes: list[Tote] = []
    unallocated: list[UnallocatedItem] = []

    def create_tote() -> Tote:
        tote = Tote(tote_id=f"T{len(totes) + 1:03d}")
        totes.append(tote)
        return tote

    # Larger orders first; order ID breaks ties. Processing normal and oversized
    # orders in this order keeps the heuristic stable and favors large placements.
    ordered = sorted(orders, key=lambda order: (-_order_volume_in3(order), order.order_id))
    for order in ordered:
        items = sorted(order.items, key=lambda item: (-item.volume_in3, order.order_id, item.order_item_id))
        order_volume = _order_volume_in3(order)

        if not items:
            continue

        if order_volume <= capacity_in3:
            # Place the entire order in the tightest fitting existing tote.
            choices = [
                (capacity_in3 - tote.volume_in3 - order_volume, index, tote)
                for index, tote in enumerate(totes)
                if tote.volume_in3 + order_volume <= capacity_in3
            ]
            target = min(choices, key=lambda choice: (choice[0], choice[1]))[2] if choices else create_tote()
            for item in items:
                _add_allocation(target, item, item.source_quantity)
            continue

        # Oversized order: best-fit decreasing across individual whole units.
        for item in items:
            unit_volume = item.volume_in3
            if unit_volume > capacity_in3:
                unallocated.append(UnallocatedItem(
                    order_item_id=item.order_item_id,
                    order_id=item.order_id,
                    household_id=item.household_id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    quantity=item.source_quantity,
                    reason=(
                        f"One item unit has estimated volume {unit_volume:.4f} in³, "
                        f"greater than tote capacity {capacity_in3:.4f} in³"
                    ),
                ))
                continue

            for _ in range(item.source_quantity):
                choices = [
                    (capacity_in3 - tote.volume_in3 - unit_volume, index, tote)
                    for index, tote in enumerate(totes)
                    if tote.volume_in3 + unit_volume <= capacity_in3
                ]
                target = min(choices, key=lambda choice: (choice[0], choice[1]))[2] if choices else create_tote()
                _add_allocation(target, item, 1)

    validate_tote_allocations(orders, totes, require_complete=not unallocated)

    allocated_by_item: dict[str, int] = {}
    tote_indexes_by_order: dict[int, set[int]] = {}
    for tote_index, tote in enumerate(totes):
        for allocation in tote.items:
            allocated_by_item[allocation.order_item_id] = (
                allocated_by_item.get(allocation.order_item_id, 0) + allocation.quantity
            )
            tote_indexes_by_order.setdefault(allocation.order_id, set()).add(tote_index)

    complete_order_ids = sorted(
        order.order_id
        for order in orders
        if order.items
        and all(allocated_by_item.get(item.order_item_id, 0) == item.source_quantity for item in order.items)
    )
    split_order_ids = sorted(
        order_id for order_id, assigned_totes in tote_indexes_by_order.items() if len(assigned_totes) > 1
    )
    oversized_order_ids = sorted(
        order.order_id for order in orders if _order_volume_in3(order) > capacity_in3
    )
    if unallocated:
        unallocated_ids = {item.order_id for item in unallocated}
        complete_order_ids = [order_id for order_id in complete_order_ids if order_id not in unallocated_ids]

    total_weight = sum(tote.weight_lb for tote in totes)
    total_volume_in3 = sum(tote.volume_in3 for tote in totes)
    per_tote = [
        ToteMetrics(
            tote_id=tote.tote_id,
            weight_lb=tote.weight_lb,
            volume_ft3=tote.volume_ft3,
            fill_percent=tote.volume_fill_pct,
            order_ids=tote.order_ids,
            item_count=sum(allocation.quantity for allocation in tote.items),
            allocation_count=len(tote.items),
        )
        for tote in totes
    ]
    warnings = [
        "Packing uses summed item-box volume only; it does not verify physical packing geometry."
    ]
    warnings.extend(
        f"Order {item.order_id}, item {item.order_item_id}: {item.reason}; "
        f"{item.quantity} unit(s) left unallocated."
        for item in unallocated
    )
    return ToteOptimizationResult(
        totes=totes,
        total_totes=len(totes),
        total_orders=len(orders),
        complete_orders=len(complete_order_ids),
        split_orders=len(split_order_ids),
        unallocated_items=unallocated,
        total_weight_lb=total_weight,
        total_volume_ft3=total_volume_in3 / 1728.0,
        average_fill_percent=(sum(tote.volume_fill_pct for tote in totes) / len(totes)) if totes else 0.0,
        warnings=warnings,
        complete_order_ids=complete_order_ids,
        split_order_ids=split_order_ids,
        orders_requiring_multiple_totes=split_order_ids.copy(),
        oversized_order_ids=oversized_order_ids,
        per_tote_metrics=per_tote,
    )
