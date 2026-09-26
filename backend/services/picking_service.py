"""Canonical picking progress derived from source item IDs and tote allocations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from backend.models import HouseholdOrder, OrderItem, Tote


@dataclass(frozen=True, slots=True)
class PickOutcome:
    outcome: str
    message: str
    order_id: int
    order_item_id: str | None = None
    product_id: int | str | None = None
    tote_id: str | None = None
    picked_quantity: int = 0
    ordered_quantity: int = 0
    remaining_quantity: int = 0


def _allocation_index(totes: Sequence[Tote]) -> tuple[dict[str, int], dict[str, list[str]]]:
    allocated: dict[str, int] = {}
    tote_ids: dict[str, list[str]] = {}
    for tote in totes:
        for allocation in tote.items:
            if allocation.quantity <= 0:
                continue
            allocated[allocation.order_item_id] = allocated.get(allocation.order_item_id, 0) + allocation.quantity
            tote_ids.setdefault(allocation.order_item_id, []).append(tote.tote_id)
    for item_id in tote_ids:
        tote_ids[item_id].sort()
    return allocated, tote_ids


def progress_for_orders(
    orders: Sequence[HouseholdOrder], totes: Sequence[Tote], picked_quantities: Mapping[str, int]
) -> list[dict]:
    """Build the one canonical order/item status projection used by all views."""
    allocated, tote_ids_by_item = _allocation_index(totes)
    result = []
    for order in orders:
        item_rows = []
        for item in order.items:
            picked = picked_quantities.get(item.order_item_id, 0)
            allocated_quantity = allocated.get(item.order_item_id, 0)
            remaining = item.source_quantity - picked
            status = "PICKED" if picked == item.source_quantity else "PARTIAL" if picked else "NOT_PICKED"
            item_rows.append({
                "order_item_id": item.order_item_id,
                "order_id": item.order_id,
                "household_id": item.household_id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "ordered_quantity": item.source_quantity,
                "picked_quantity": picked,
                "remaining_quantity": remaining,
                "allocated_quantity": allocated_quantity,
                "tote_ids": tote_ids_by_item.get(item.order_item_id, []),
                "picking_status": status,
            })
        ordered_total = sum(item.source_quantity for item in order.items)
        picked_total = sum(picked_quantities.get(item.order_item_id, 0) for item in order.items)
        fully_allocated = bool(order.items) and all(
            allocated.get(item.order_item_id, 0) == item.source_quantity for item in order.items
        )
        any_allocated = any(allocated.get(item.order_item_id, 0) for item in order.items)
        order_status = (
            "PICKED" if ordered_total and picked_total == ordered_total
            else "IN_PROGRESS" if picked_total
            else "READY_FOR_PICKING" if fully_allocated
            else "ENTERED"
        )
        if not any_allocated:
            packing_status = "NOT_PACKED"
        elif fully_allocated:
            packing_status = "FULLY_ALLOCATED"
        else:
            packing_status = "PARTIALLY_ALLOCATED"
        result.append({
            "order_id": order.order_id,
            "household_id": order.household_id,
            "ordered_quantity": ordered_total,
            "picked_quantity": picked_total,
            "remaining_quantity": ordered_total - picked_total,
            "picking_status": order_status,
            "packing_status": packing_status,
            "items": item_rows,
        })
    return result


def scan_item(
    orders: Sequence[HouseholdOrder],
    totes: Sequence[Tote],
    picked_quantities: dict[str, int],
    *,
    order_id: int,
    identifier: str,
    quantity: int = 1,
    tote_id: str,
) -> PickOutcome:
    """Match a scan in order/tote context and increment source-item progress."""
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("scan quantity must be a positive integer")
    order_by_id = {order.order_id: order for order in orders}
    order = order_by_id.get(order_id)
    if order is None:
        return PickOutcome("unknown_order", f"Order {order_id} was not found.", order_id, tote_id=tote_id)
    tote = next((candidate for candidate in totes if candidate.tote_id == tote_id), None)
    if tote is None:
        return PickOutcome("wrong_tote", f"Tote {tote_id} is not in the current plan.", order_id, tote_id=tote_id)

    raw_identifier = str(identifier).strip()
    order_matches = [
        item for item in order.items
        if item.order_item_id == raw_identifier or str(item.product_id) == raw_identifier
    ]
    if not order_matches:
        matches_elsewhere = [
            item for candidate_order in orders for item in candidate_order.items
            if item.order_item_id == raw_identifier or str(item.product_id) == raw_identifier
        ]
        outcome = "wrong_order" if matches_elsewhere else "unknown_item"
        message = f"Item {raw_identifier} belongs to a different order." if matches_elsewhere else f"Unknown item {raw_identifier}."
        return PickOutcome(outcome, message, order_id, tote_id=tote_id)
    if len(order_matches) > 1:
        return PickOutcome(
            "ambiguous_item", f"Identifier {raw_identifier} matches multiple lines in order {order_id}; scan the source item ID.",
            order_id, tote_id=tote_id,
        )
    item = order_matches[0]
    allocation = next((line for line in tote.items if line.order_item_id == item.order_item_id), None)
    if allocation is None or allocation.quantity <= 0:
        actual_totes = [t.tote_id for t in totes if any(line.order_item_id == item.order_item_id and line.quantity > 0 for line in t.items)]
        suffix = f" Item is allocated to {', '.join(actual_totes)}." if actual_totes else " Item has no tote allocation."
        return PickOutcome("wrong_tote", f"Item {raw_identifier} is not in tote {tote_id}.{suffix}", order_id, item.order_item_id, item.product_id, tote_id, picked_quantities.get(item.order_item_id, 0), item.source_quantity, item.source_quantity - picked_quantities.get(item.order_item_id, 0))
    current = picked_quantities.get(item.order_item_id, 0)
    if current >= item.source_quantity:
        return PickOutcome("already_complete", f"{item.product_name} is already complete.", order_id, item.order_item_id, item.product_id, tote_id, current, item.source_quantity, 0)
    if current + quantity > item.source_quantity:
        raise ValueError(f"pick quantity would exceed ordered quantity for item {item.order_item_id}")
    picked = current + quantity
    picked_quantities[item.order_item_id] = picked
    outcome = "quantity_complete" if picked == item.source_quantity else "scanned"
    message = f"Quantity complete: {item.product_name}." if outcome == "quantity_complete" else f"Scanned {item.product_name}."
    return PickOutcome(outcome, message, order_id, item.order_item_id, item.product_id, tote_id, picked, item.source_quantity, item.source_quantity - picked)
