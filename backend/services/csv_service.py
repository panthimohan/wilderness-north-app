"""CSV loading, validation, and normalization for challenge datasets."""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from backend import config
from backend.models import FlightCapacity, HouseholdOrder, OrderItem, Tote


class CSVValidationError(ValueError):
    """Raised when a challenge CSV is missing data or contains invalid rows."""


ORDER_COLUMNS = {
    "batch_id", "order_date", "order_id", "household_id", "destination_community",
    "product_id", "product_name", "weight_lb", "length_in", "width_in", "height_in",
}
CAPACITY_COLUMNS = {
    "departure_id", "departure_date", "available_totes", "available_payload_lb", "available_volume_cuft",
}
_ORDER_INTEGER_FIELDS = ("batch_id", "order_id", "household_id", "product_id")
_ORDER_NONNEGATIVE_FIELDS = ("weight_lb", "length_in", "width_in", "height_in")


def _read_dict_rows(path: str | Path, required: set[str]) -> list[dict[str, str]]:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise CSVValidationError(f"CSV file does not exist: {csv_path}")
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                raise CSVValidationError(f"CSV has no header row: {csv_path}")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise CSVValidationError(f"{csv_path}: duplicate column names")
            missing = sorted(required - set(reader.fieldnames))
            if missing:
                raise CSVValidationError(f"{csv_path}: missing required columns: {', '.join(missing)}")
            rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise CSVValidationError(f"{csv_path}: row {row_number} has extra fields")
                rows.append(dict(row))
            return rows
    except UnicodeDecodeError as exc:
        raise CSVValidationError(f"{csv_path}: expected UTF-8 CSV encoding") from exc
    except csv.Error as exc:
        raise CSVValidationError(f"{csv_path}: could not parse CSV: {exc}") from exc


def _required_text(row: dict[str, str], field: str, row_number: int, path: Path) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise CSVValidationError(f"{path}: row {row_number}: {field} is required")
    return value


def _integer(row: dict[str, str], field: str, row_number: int, path: Path) -> int:
    raw = _required_text(row, field, row_number, path)
    try:
        value = int(raw)
    except ValueError as exc:
        raise CSVValidationError(f"{path}: row {row_number}: {field} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise CSVValidationError(f"{path}: row {row_number}: {field} must be non-negative")
    return value


def _number(row: dict[str, str], field: str, row_number: int, path: Path) -> float:
    raw = _required_text(row, field, row_number, path)
    try:
        value = float(raw)
    except ValueError as exc:
        raise CSVValidationError(f"{path}: row {row_number}: {field} must be numeric, got {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise CSVValidationError(f"{path}: row {row_number}: {field} must be a finite non-negative number")
    return value


def _iso_date(row: dict[str, str], field: str, row_number: int, path: Path) -> date:
    raw = _required_text(row, field, row_number, path)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise CSVValidationError(f"{path}: row {row_number}: {field} must be an ISO date (YYYY-MM-DD), got {raw!r}") from exc


def _normalize_order_rows(
    rows: list[dict[str, str]], *, source_label: str, source_key: str | None = None
) -> list[HouseholdOrder]:
    csv_path = Path(source_label)
    if not rows:
        raise CSVValidationError(f"{csv_path}: order CSV contains no item rows")
    identity_prefix = source_key or csv_path.name
    orders: dict[int, HouseholdOrder] = {}
    seen_item_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        batch_id, order_id, household_id, product_id = (
            _integer(row, name, row_number, csv_path) for name in _ORDER_INTEGER_FIELDS
        )
        order_date = _iso_date(row, "order_date", row_number, csv_path)
        destination = _required_text(row, "destination_community", row_number, csv_path)
        product_name = _required_text(row, "product_name", row_number, csv_path)
        weight, length, width, height = (
            _number(row, name, row_number, csv_path) for name in _ORDER_NONNEGATIVE_FIELDS
        )
        item_id = f"{identity_prefix}:row:{row_number}:order:{order_id}"
        if item_id in seen_item_ids:
            raise CSVValidationError(f"{csv_path}: duplicate source item identity {item_id}")
        seen_item_ids.add(item_id)
        item = OrderItem(
            order_id=order_id, household_id=household_id, batch_id=batch_id,
            order_date=order_date, destination_community=destination,
            product_id=product_id, product_name=product_name, length_in=length,
            width_in=width, height_in=height, weight_lb=weight,
            order_item_id=item_id, source_quantity=1,
        )
        prior = orders.get(order_id)
        if prior is None:
            orders[order_id] = HouseholdOrder(
                order_id=order_id, household_id=household_id, batch_id=batch_id,
                order_date=order_date, destination_community=destination, items=[item],
            )
        else:
            if prior.household_id != household_id:
                raise CSVValidationError(f"{csv_path}: row {row_number}: order_id {order_id} maps to multiple household_ids")
            if prior.destination_community != destination:
                raise CSVValidationError(f"{csv_path}: row {row_number}: order_id {order_id} has multiple destinations")
            if prior.order_date != order_date:
                raise CSVValidationError(f"{csv_path}: row {row_number}: order_id {order_id} has multiple order dates")
            if prior.batch_id != batch_id:
                raise CSVValidationError(f"{csv_path}: row {row_number}: order_id {order_id} has multiple batch IDs")
            prior.items.append(item)
    return list(orders.values())


def _dict_rows_from_stream(stream, source_label: str, required: set[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise CSVValidationError(f"{source_label}: CSV has no header row")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise CSVValidationError(f"{source_label}: duplicate column names")
    missing = sorted(required - set(reader.fieldnames))
    if missing:
        raise CSVValidationError(f"{source_label}: missing required columns: {', '.join(missing)}")
    rows = []
    try:
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise CSVValidationError(f"{source_label}: row {row_number} has extra fields")
            rows.append(dict(row))
    except csv.Error as exc:
        raise CSVValidationError(f"{source_label}: could not parse CSV: {exc}") from exc
    return rows


def load_orders_csv(path: str | Path, *, source_key: str | None = None) -> list[HouseholdOrder]:
    """Normalize one CSV row per item; order_item_id includes source and row context."""
    csv_path = Path(path)
    if not csv_path.is_file():
        raise CSVValidationError(f"CSV file does not exist: {csv_path}")
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = _dict_rows_from_stream(stream, str(csv_path), ORDER_COLUMNS)
    except UnicodeDecodeError as exc:
        raise CSVValidationError(f"{csv_path}: expected UTF-8 CSV encoding") from exc
    return _normalize_order_rows(rows, source_label=str(csv_path), source_key=source_key)


def load_orders_csv_text(
    content: str, *, filename: str, source_key: str | None = None
) -> list[HouseholdOrder]:
    """Validate and normalize uploaded CSV text using the same rules as file loading."""
    source_label = Path(filename).name or "uploaded.csv"
    try:
        rows = _dict_rows_from_stream(io.StringIO(content.lstrip("\ufeff"), newline=""), source_label, ORDER_COLUMNS)
    except csv.Error as exc:
        raise CSVValidationError(f"{source_label}: could not parse CSV: {exc}") from exc
    return _normalize_order_rows(rows, source_label=source_label, source_key=source_key)

def load_stage1_orders() -> list[HouseholdOrder]:
    return load_orders_csv(config.STAGE1_ORDERS_FILE)


def load_stage2_orders() -> list[HouseholdOrder]:
    return load_orders_csv(config.STAGE2_ORDERS_FILE)


def load_flight_capacities_csv(path: str | Path) -> list[FlightCapacity]:
    csv_path = Path(path)
    rows = _read_dict_rows(csv_path, CAPACITY_COLUMNS)
    if not rows:
        raise CSVValidationError(f"{csv_path}: capacity CSV contains no departures")
    result: list[FlightCapacity] = []
    seen_ids: set[int] = set()
    for row_number, row in enumerate(rows, start=2):
        departure_id = _integer(row, "departure_id", row_number, csv_path)
        if departure_id in seen_ids:
            raise CSVValidationError(f"{csv_path}: row {row_number}: duplicate departure_id {departure_id}")
        seen_ids.add(departure_id)
        result.append(FlightCapacity(
            departure_id=departure_id,
            departure_date=_iso_date(row, "departure_date", row_number, csv_path),
            available_totes=_integer(row, "available_totes", row_number, csv_path),
            available_payload_lb=_number(row, "available_payload_lb", row_number, csv_path),
            available_volume_cuft=_number(row, "available_volume_cuft", row_number, csv_path),
        ))
    return result


def load_stage2_flight_capacities() -> list[FlightCapacity]:
    return load_flight_capacities_csv(config.FLIGHT_CAPACITY_FILE)


def orders_by_date(orders: Iterable[HouseholdOrder]) -> dict[date, list[HouseholdOrder]]:
    grouped: dict[date, list[HouseholdOrder]] = defaultdict(list)
    for order in orders:
        grouped[order.order_date].append(order)
    return dict(sorted(grouped.items()))


def oversized_orders(orders: Iterable[HouseholdOrder], tote_volume_in3: float | None = None) -> list[HouseholdOrder]:
    """Find orders exceeding nominal tote volume by summed item-box volume only."""
    tote_capacity = config.TOTE_VOLUME_IN3 if tote_volume_in3 is None else tote_volume_in3
    if not math.isfinite(tote_capacity) or tote_capacity < 0:
        raise ValueError("tote_volume_in3 must be finite and non-negative")
    return [order for order in orders if order.volume_in3 > tote_capacity]


def summarize_orders(orders: Sequence[HouseholdOrder]) -> dict[str, object]:
    grouped_dates = orders_by_date(orders)
    return {
        "order_count": len(orders),
        "household_count": len({order.household_id for order in orders}),
        "item_row_count": sum(len(order.items) for order in orders),
        "total_weight_lb": sum(order.weight_lb for order in orders),
        "total_volume_ft3": sum(order.volume_ft3 for order in orders),
        "orders_by_date": {day.isoformat(): len(day_orders) for day, day_orders in grouped_dates.items()},
        "oversized_order_ids": [order.order_id for order in oversized_orders(orders)],
    }


def validate_tote_allocations(
    orders: Sequence[HouseholdOrder], totes: Sequence[Tote], *, require_complete: bool = False
) -> None:
    """Validate item references, integer totals, and optional full-order packing.

    A source CSV row is one item unit. Its allocation may be distributed across
    totes only up to source_quantity; full packing requires exact conservation.
    """
    source: dict[str, OrderItem] = {}
    for order in orders:
        for item in order.items:
            if item.order_item_id in source:
                raise ValueError(f"duplicate source order_item_id: {item.order_item_id}")
            if item.order_id != order.order_id:
                raise ValueError(f"item {item.order_item_id} does not belong to order {order.order_id}")
            source[item.order_item_id] = item
    allocated: dict[str, int] = defaultdict(int)
    for tote in totes:
        for allocation in tote.items:
            item_id = allocation.order_item_id
            item = source.get(item_id)
            if item is None or item != allocation.item:
                raise ValueError(f"tote {tote.tote_id} references an unknown or inconsistent source item {item_id}")
            if isinstance(allocation.quantity, bool) or not isinstance(allocation.quantity, int) or allocation.quantity < 0:
                raise ValueError(f"allocation for {item_id} must be a non-negative integer")
            allocated[item_id] += allocation.quantity
            if allocated[item_id] > item.source_quantity:
                raise ValueError(f"totes over-allocate source item {item_id}")
    if require_complete:
        missing = [item_id for item_id, item in source.items() if allocated[item_id] != item.source_quantity]
        if missing:
            raise ValueError(f"items are not fully allocated across totes: {', '.join(missing[:10])}")
