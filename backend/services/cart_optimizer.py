"""Deterministic cart assignment for shared and split-order totes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import inf
from typing import Sequence

from backend import config
from backend.models import Cart, HouseholdOrder, OrderItem, Tote
from backend.services.csv_service import validate_tote_allocations
from backend.services.plan_service import PlanAssignmentError, PlanService


@dataclass(frozen=True, slots=True)
class CartMetrics:
    cart_id: str
    tote_ids: list[str]
    tote_count: int
    utilization_percent: float
    order_ids: list[int]
    split_order_ids: list[int]


@dataclass(slots=True)
class CartOptimizationResult:
    carts: list[Cart]
    total_carts: int
    total_totes: int
    totes_per_cart: int
    orders_kept_together: int
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    feasible: bool = True
    orders_kept_together_ids: list[int] = field(default_factory=list)
    per_cart_metrics: list[CartMetrics] = field(default_factory=list)
    unassigned_tote_ids: list[str] = field(default_factory=list)


class CartOptimizationError(ValueError):
    """Raised when input data or a produced cart assignment is inconsistent."""


def _tote_order_groups(totes: Sequence[Tote]) -> tuple[dict[int, set[str]], dict[str, set[int]]]:
    order_totes: dict[int, set[str]] = {}
    tote_orders: dict[str, set[int]] = {}
    for tote in totes:
        ids = set(tote.order_ids)
        tote_orders[tote.tote_id] = ids
        for order_id in ids:
            order_totes.setdefault(order_id, set()).add(tote.tote_id)
    return order_totes, tote_orders


def _connected_tote_groups(totes: Sequence[Tote], order_totes: dict[int, set[str]]) -> list[tuple[list[str], list[int]]]:
    """Find indivisible tote components induced by orders sharing totes."""
    parent = {tote.tote_id: tote.tote_id for tote in totes}

    def find(tote_id: str) -> str:
        while parent[tote_id] != tote_id:
            parent[tote_id] = parent[parent[tote_id]]
            tote_id = parent[tote_id]
        return tote_id

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            # Stable root choice makes component formation independent of input order.
            first, second = sorted((root_left, root_right))
            parent[second] = first

    for tote_ids in order_totes.values():
        ordered_ids = sorted(tote_ids)
        for tote_id in ordered_ids[1:]:
            union(ordered_ids[0], tote_id)

    tote_ids_by_root: dict[str, list[str]] = {}
    for tote_id in sorted(parent):
        tote_ids_by_root.setdefault(find(tote_id), []).append(tote_id)
    groups = []
    for ids in tote_ids_by_root.values():
        group_orders = sorted(
            order_id for order_id, order_tote_ids in order_totes.items()
            if not order_tote_ids.isdisjoint(ids)
        )
        groups.append((sorted(ids), group_orders))
    return groups


def _validate_cart_assignment(
    orders: Sequence[HouseholdOrder],
    totes: Sequence[Tote],
    carts: Sequence[Cart],
    totes_per_cart: int,
) -> None:
    tote_ids = [tote.tote_id for tote in totes]
    if len(tote_ids) != len(set(tote_ids)):
        raise CartOptimizationError("input contains duplicate tote IDs")
    flattened = [tote.tote_id for cart in carts for tote in cart.totes]
    if len(flattened) != len(set(flattened)):
        raise CartOptimizationError("a tote is assigned to more than one cart")
    if len(flattened) != len(tote_ids) or set(flattened) != set(tote_ids):
        raise CartOptimizationError("every input tote must be assigned to exactly one cart")
    tote_by_id = {tote.tote_id: tote for tote in totes}
    for cart in carts:
        if len(cart.totes) > totes_per_cart:
            raise CartOptimizationError(f"cart {cart.cart_id} exceeds capacity {totes_per_cart}")
        for cart_tote in cart.totes:
            if tote_by_id[cart_tote.tote_id] is not cart_tote:
                raise CartOptimizationError(f"cart {cart.cart_id} does not contain the canonical tote object")
            if cart_tote.cart_id != cart.cart_id:
                raise CartOptimizationError(f"tote {cart_tote.tote_id} has inconsistent cart_id")

    order_totes, _ = _tote_order_groups(totes)
    tote_cart = {tote.tote_id: cart.cart_id for cart in carts for tote in cart.totes}
    for order_id, ids in order_totes.items():
        cart_ids = {tote_cart[tote_id] for tote_id in ids}
        if len(cart_ids) != 1:
            raise CartOptimizationError(
                f"order {order_id} has totes assigned across multiple carts: {sorted(cart_ids)}"
            )
    # Ensure source orders are unique and every tote allocation belongs to them.
    order_ids = [order.order_id for order in orders]
    if len(order_ids) != len(set(order_ids)):
        raise CartOptimizationError("orders contain duplicate order_id values")
    try:
        validate_tote_allocations(orders, totes, require_complete=False)
    except ValueError as exc:
        raise CartOptimizationError(f"invalid tote/order traceability: {exc}") from exc


def _make_result(
    carts: list[Cart],
    totes: Sequence[Tote],
    orders: Sequence[HouseholdOrder],
    totes_per_cart: int,
    warnings: list[str],
) -> CartOptimizationResult:
    order_totes, _ = _tote_order_groups(totes)
    represented_ids = sorted(order_totes)
    split_ids = {order_id for order_id, ids in order_totes.items() if len(ids) > 1}
    cart_metrics = [
        CartMetrics(
            cart_id=cart.cart_id,
            tote_ids=sorted(tote.tote_id for tote in cart.totes),
            tote_count=len(cart.totes),
            utilization_percent=100.0 * len(cart.totes) / totes_per_cart,
            order_ids=cart.order_ids,
            split_order_ids=sorted(split_ids.intersection(cart.order_ids)),
        )
        for cart in carts
    ]
    missing_order_ids = sorted(order.order_id for order in orders if order.order_id not in order_totes)
    if missing_order_ids:
        warnings = [*warnings, f"Orders with no tote assignment: {missing_order_ids}"]
    return CartOptimizationResult(
        carts=carts,
        total_carts=len(carts),
        total_totes=len(totes),
        totes_per_cart=totes_per_cart,
        orders_kept_together=len(represented_ids),
        warnings=warnings,
        orders_kept_together_ids=represented_ids,
        per_cart_metrics=cart_metrics,
    )


def optimize_carts(
    totes: Sequence[Tote],
    orders: Sequence[HouseholdOrder],
    totes_per_cart: int = config.DEFAULT_TOTES_PER_CART,
) -> CartOptimizationResult:
    """Assign every tote to one cart without splitting any order's tote group.

    Totes linked by a shared order form one indivisible connected group. Groups
    are packed largest-first into the cart with least remaining capacity after
    placement. Repeating the call on an already optimized tote list validates
    and returns its existing assignment without changing it.
    """
    totes, orders = list(totes), list(orders)
    if isinstance(totes_per_cart, bool) or not isinstance(totes_per_cart, int) or totes_per_cart <= 0:
        raise ValueError("totes_per_cart must be a positive integer")
    if len({tote.tote_id for tote in totes}) != len(totes):
        raise CartOptimizationError("input contains duplicate tote IDs")
    order_ids = [order.order_id for order in orders]
    if len(order_ids) != len(set(order_ids)):
        raise CartOptimizationError("orders contain duplicate order_id values")
    try:
        validate_tote_allocations(orders, totes, require_complete=False)
    except ValueError as exc:
        raise CartOptimizationError(f"invalid tote/order traceability: {exc}") from exc

    order_totes, _ = _tote_order_groups(totes)
    for order_id, ids in sorted(order_totes.items()):
        if len(ids) > totes_per_cart:
            return CartOptimizationResult(
                carts=[], total_carts=0, total_totes=len(totes),
                totes_per_cart=totes_per_cart, orders_kept_together=0,
                errors=[
                    f"Order {order_id} requires {len(ids)} totes, exceeding cart capacity "
                    f"{totes_per_cart}; its totes cannot be split across carts."
                ],
                feasible=False,
                unassigned_tote_ids=sorted(tote.tote_id for tote in totes),
            )

    groups = _connected_tote_groups(totes, order_totes)
    oversized_groups = [group for group in groups if len(group[0]) > totes_per_cart]
    if oversized_groups:
        ids, group_orders = sorted(oversized_groups, key=lambda group: (-len(group[0]), group[1], group[0]))[0]
        return CartOptimizationResult(
            carts=[], total_carts=0, total_totes=len(totes),
            totes_per_cart=totes_per_cart, orders_kept_together=0,
            errors=[
                f"Linked orders {group_orders} connect totes {ids} into one indivisible group of {len(ids)}, "
                f"exceeding cart capacity {totes_per_cart}."
            ],
            feasible=False,
            unassigned_tote_ids=sorted(tote.tote_id for tote in totes),
        )

    # If called twice on the same objects, reconstruct and verify the already
    # synchronized memberships. Mixed preassignment is rejected rather than
    # partially overwritten.
    assigned = [tote.cart_id is not None for tote in totes]
    if any(assigned):
        if not all(assigned):
            raise CartOptimizationError("input totes have partial cart assignments; clear them before optimizing")
        cart_ids = sorted({tote.cart_id for tote in totes if tote.cart_id is not None})
        carts = [
            Cart(
                cart_id=cart_id,
                totes=tuple(tote for tote in totes if tote.cart_id == cart_id),
                tote_capacity=totes_per_cart,
            )
            for cart_id in cart_ids
        ]
        service = PlanService(totes, carts, [])
        del service  # Construction validates canonical memberships and synchronizes IDs.
        _validate_cart_assignment(orders, totes, carts, totes_per_cart)
        return _make_result(carts, totes, orders, totes_per_cart, warnings=[])

    # Stable best-fit decreasing over indivisible connected components.
    groups.sort(key=lambda group: (-len(group[0]), group[1], group[0]))
    cart_groups: list[list[str]] = []
    for group_tote_ids, _group_order_ids in groups:
        available = [
            (totes_per_cart - len(cart_ids) - len(group_tote_ids), index)
            for index, cart_ids in enumerate(cart_groups)
            if len(cart_ids) + len(group_tote_ids) <= totes_per_cart
        ]
        if available:
            cart_index = min(available)[1]
            cart_groups[cart_index].extend(group_tote_ids)
        else:
            cart_groups.append(list(group_tote_ids))

    tote_by_id = {tote.tote_id: tote for tote in totes}
    carts = [
        Cart(
            cart_id=f"C{index + 1:03d}",
            tote_capacity=totes_per_cart,
        )
        for index in range(len(cart_groups))
    ]
    service = PlanService(totes, carts, [])
    for cart, group_tote_ids in zip(carts, cart_groups):
        for tote_id in sorted(group_tote_ids):
            service.assign_tote_to_cart(tote_id, cart.cart_id)

    _validate_cart_assignment(orders, totes, carts, totes_per_cart)
    return _make_result(carts, totes, orders, totes_per_cart, warnings=[])
