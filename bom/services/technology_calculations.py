"""Поиск технологического маршрута и расчёт ресурсов для партии."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from graphlib import CycleError, TopologicalSorter

from ..database import db
from ..models.product import Product
from ..models.product_operation import PurchasePosition, RoutePosition


def _get_required(model, object_id, label):
    record = db.session.get(model, object_id)
    if record is None:
        raise LookupError(f"{label} с id={object_id} не найден(а)")
    return record


def _quantity(value):
    try:
        quantity = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("quantity должно быть числом") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("quantity должно быть конечным положительным числом")
    return quantity


def _route_plan(product):
    """Возвращает конечные позиции и граф в порядке от ресурсов к операциям."""
    positions = product.get_operations()
    starting_positions = [
        position for position in positions if isinstance(position, RoutePosition)
    ] or positions
    if not starting_positions:
        raise ValueError(f"Для изделия {product.designation} не заданы позиции ТМ")

    pending = list(reversed(starting_positions))
    graph = {}
    while pending:
        position = pending.pop()
        if position in graph:
            continue
        edges = position.get_input_resources()
        graph[position] = tuple(edge.in_to for edge in edges)
        pending.extend(reversed(graph[position]))

    try:
        ordered = tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError("В технологическом маршруте обнаружен цикл") from exc

    intermediate = {source for sources in graph.values() for source in sources}
    roots = [position for position in starting_positions if position not in intermediate]
    return roots, ordered


def _batch(product_id, quantity):
    product = _get_required(Product, product_id, "Изделие")
    roots, ordered = _route_plan(product)
    amounts = defaultdict(Decimal)
    for root in roots:
        amounts[root] = quantity
    for position in reversed(ordered):
        for edge in position.get_input_resources():
            amounts[edge.in_to] += edge.required_quantity(amounts[position])
    return product, roots, ordered, amounts


def FIND_TM(pIdEl):
    product = _get_required(Product, pIdEl, "Изделие")
    roots, ordered = _route_plan(product)
    operations = []
    purchases = []
    for position in ordered:
        if isinstance(position, RoutePosition):
            operation = position.to_dict()
            operation["input_resources"] = [
                edge.to_dict() for edge in position.get_input_resources()
            ]
            operations.append(operation)
        elif isinstance(position, PurchasePosition):
            purchases.append(position.to_dict())
    return {
        "product": product.to_dict(),
        "terminal_position_ids": [position.id_to for position in roots],
        "route_positions": operations,
        "purchase_positions": purchases,
    }


def MR_Q(pIdTO, pQ):
    operation = _get_required(RoutePosition, pIdTO, "Технологическая операция")
    quantity = _quantity(pQ)
    return {
        "operation": operation.to_dict(),
        "requested_quantity": str(quantity),
        "resources": [
            {**edge.to_dict(), "required_quantity": str(edge.required_quantity(quantity))}
            for edge in operation.get_input_resources()
        ],
    }


def _material_rows(roots, ordered, amounts):
    rows = {}

    def add_purchase(source, unit, quantity):
        price = source.effective_price()
        unit_id = unit.id_ei if unit is not None else None
        key = (source.product_id, unit_id, price)
        if key not in rows:
            rows[key] = {
                "product_id": source.product_id,
                "designation": source.product.designation,
                "name": source.product.name,
                "unit_id": unit_id,
                "unit_short_name": unit.short_name if unit is not None else None,
                "unit_price": price,
                "quantity": Decimal(0),
            }
        rows[key]["quantity"] += quantity

    for consumer in ordered:
        for edge in consumer.get_input_resources():
            if isinstance(edge.in_to, PurchasePosition):
                add_purchase(edge.in_to, edge.unit, edge.required_quantity(amounts[consumer]))

    for root in roots:
        if isinstance(root, PurchasePosition):
            # Самостоятельная закупка оценивается за единицу готового изделия.
            add_purchase(root, None, amounts[root])

    for row in rows.values():
        price = row["unit_price"]
        row["cost"] = None if price is None else row["quantity"] * price
    return list(rows.values())


def _labor_rows(ordered, amounts):
    rows = {}
    for operation in ordered:
        if not isinstance(operation, RoutePosition):
            continue
        key = (operation.profession_class_id, operation.qualification)
        if key not in rows:
            rows[key] = {
                "profession_class_id": operation.profession_class_id,
                "profession_name": operation.profession.name,
                "qualification": operation.qualification.value,
                "hourly_rate": operation.profession.hourly_rate,
                "hours": Decimal(0),
            }
        rows[key]["hours"] += operation.calculate_labor(amounts[operation])

    for row in rows.values():
        rate = row["hourly_rate"]
        row["cost"] = None if rate is None else row["hours"] * rate
    return list(rows.values())


def _total_cost(rows):
    costs = [row["cost"] for row in rows]
    return None if None in costs else sum(costs, Decimal(0))


def SUM_MR(pIdEl, pQ):
    quantity = _quantity(pQ)
    product, roots, ordered, amounts = _batch(pIdEl, quantity)
    rows = _material_rows(roots, ordered, amounts)
    return {
        "product": product.to_dict(),
        "requested_quantity": str(quantity),
        "materials": rows,
        "material_cost": _total_cost(rows),
    }


def TR_TO(pIdEl, pQ):
    quantity = _quantity(pQ)
    product, _, ordered, amounts = _batch(pIdEl, quantity)
    rows = _labor_rows(ordered, amounts)
    return {
        "product": product.to_dict(),
        "requested_quantity": str(quantity),
        "labor": rows,
        "total_hours": sum((row["hours"] for row in rows), Decimal(0)),
        "labor_cost": _total_cost(rows),
    }


def CALCULATE_TM_COST(pIdEl, pQ):
    quantity = _quantity(pQ)
    product, roots, ordered, amounts = _batch(pIdEl, quantity)
    materials = _material_rows(roots, ordered, amounts)
    labor = _labor_rows(ordered, amounts)
    material_cost = _total_cost(materials)
    labor_cost = _total_cost(labor)
    total_cost = None if None in (material_cost, labor_cost) else material_cost + labor_cost
    return {
        "product": product.to_dict(),
        "requested_quantity": str(quantity),
        "materials": materials,
        "labor": labor,
        "material_cost": material_cost,
        "labor_cost": labor_cost,
        "total_cost": total_cost,
    }
