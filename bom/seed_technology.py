"""Дополняет спецификацию СБ-001 учебной маршрутной технологией."""

import argparse
from collections import Counter
from decimal import Decimal

from .app import app
from .database import db
from .models.bom_position import BOMPosition
from .models.input_resource_position import InputResourcePosition
from .models.operation_classifier import OperationClassifier
from .models.product import Product
from .models.product_classifier import ProductClassifier
from .models.product_operation import PurchasePosition, Qualification, RoutePosition
from .models.profession_classifier import ProfessionClassifier
from .models.tool_requirement import ToolRequirement
from .models.unit import Unit
from .models.work_center import WorkCenter

CAR = "СБ-001"
ELECTRONICS = {"Д-001", "Д-002", "Д-003"}
FLUIDS = {"М-001", "М-003"}


def _unit(symbol, name):
    unit = Unit.query.filter_by(short_name=symbol).one_or_none()
    if unit is None:
        unit = Unit(short_name=symbol, name=name)
        db.session.add(unit)
    return unit


def _demo_bom():
    """Воссоздаёт опубликованный в отчёте фрагмент BOM в пустой базе."""
    parts = ProductClassifier(name="Детали и комплектующие")
    fluids = ProductClassifier(name="Расходники")
    assemblies = ProductClassifier(name="Сборочные единицы")
    each = _unit("шт", "штуки")
    liters = _unit("л", "литры")
    car = Product(
        name="Автомобиль Стандарт", designation=CAR, product_type="assembly",
        classifier=assemblies, price=1500000,
    )
    modification = Product(
        name="Автомобиль Стандарт Плюс", designation="СБ-001-М4",
        product_type="assembly", classifier=assemblies, base_product=car,
        price=1500000,
    )
    lines = (
        ("Д-001", "Видеорегистратор", parts, "part", 5000, 1, each),
        ("Д-002", "GPS-трекер", parts, "part", 3000, 1, each),
        ("Д-003", "USB-зарядка", parts, "part", 500, 5, each),
        ("М-001", "Омывающая жидкость", fluids, "material", 300, 3, liters),
        ("М-003", "Топливо АИ-95", fluids, "material", 60, 40, liters),
    )
    for code, name, classifier, kind, price, consumption, unit in lines:
        component = Product(
            name=name, designation=code, classifier=classifier,
            product_type=kind, price=price,
        )
        for parent in (car, modification):
            db.session.add(BOMPosition(
                parent_product=parent, component_product=component,
                consumption=consumption, for_quantity=1, is_base=True, unit=unit,
            ))
    db.session.flush()


def _named(model, name, **fields):
    record = model.query.filter_by(name=name).one_or_none()
    if record is None:
        record = model(name=name, **fields)
        db.session.add(record)
    return record


def _purchase_for(component):
    positions = component.get_operations()
    if not positions:
        purchase = PurchasePosition(product=component, sequence_number=1)
        db.session.add(purchase)
        return purchase
    if len(positions) == 1 and isinstance(positions[0], PurchasePosition):
        return positions[0]
    raise ValueError(
        f"У компонента {component.designation} уже настроены технологические позиции. "
        "Для него нужно явно выбрать источник ресурса."
    )


def seed_technology(demo=False):
    if demo and Product.query.first() is None:
        _demo_bom()

    car = Product.query.filter_by(designation=CAR).one_or_none()
    if car is None:
        raise ValueError(f"В базе отсутствует {CAR}. Укажите базу задания №5 или используйте --demo.")
    if car.get_operations():
        return False

    lines = list(car.bom_positions)
    if Counter(line.component_product.designation for line in lines) != Counter(ELECTRONICS | FLUIDS):
        raise ValueError(
            f"Состав {CAR} отличается от примера отчёта №5. "
            "Сопоставьте позиции с операциями перед заполнением."
        )

    each = _unit("шт", "штуки")
    center = _named(WorkCenter, "Участок предпродажной подготовки")
    profession = _named(ProfessionClassifier, "Автомеханик", hourly_rate=900)
    group = _named(OperationClassifier, "Подготовка комплектации")
    install = _named(OperationClassifier, "Установка электроники", parent=group)
    refill = _named(OperationClassifier, "Заправка и контроль", parent=group)

    mounting = RoutePosition(
        product=car, sequence_number=10, setup_time=Decimal("0.25"),
        unit_time=Decimal("0.5"), work_center=center, operation_class=install,
        profession=profession, qualification=Qualification.Grade4,
    )
    finish = RoutePosition(
        product=car, sequence_number=20, setup_time=Decimal("0.10"),
        unit_time=Decimal("0.25"), work_center=center, operation_class=refill,
        profession=profession, qualification=Qualification.Grade3,
    )
    db.session.add_all([mounting, finish])
    for line in lines:
        component = line.component_product
        source = _purchase_for(component)
        consumer = mounting if component.designation in ELECTRONICS else finish
        db.session.add(InputResourcePosition(
            out_to=consumer, in_to=source, unit=line.unit,
            consumption=line.consumption, for_quantity=line.for_quantity,
        ))
    db.session.add(InputResourcePosition(
        out_to=finish, in_to=mounting, unit=each, consumption=1, for_quantity=1,
    ))

    tool_code = "ТМ-ДЕМО-ИНСТР-01"
    tool = Product.query.filter_by(designation=tool_code).one_or_none()
    if tool is None:
        classifier = _named(ProductClassifier, "Инструменты и оснастка")
        tool = Product(
            designation=tool_code, name="Набор инструмента для монтажа электроники",
            product_type="part", classifier=classifier,
        )
    db.session.add(ToolRequirement(route_position=mounting, product=tool, quantity=1))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Воссоздать пример BOM, если база пуста")
    args = parser.parse_args()
    with app.app_context():
        db.create_all()
        try:
            created = seed_technology(demo=args.demo)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            parser.exit(1, f"{exc}\n")
    print(f"Маршрут {CAR} добавлен." if created else f"У {CAR} уже есть маршрутные позиции.")


if __name__ == "__main__":
    main()
