from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from db import Database


def _fmt_number(value: float, suffix: str = "") -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


async def build_daily_report(db: Database, report_date: str) -> str:
    datetime.strptime(report_date, "%Y-%m-%d")

    shifts = await db.get_shifts_by_date(report_date)
    stocks = await db.get_latest_stock_by_date(report_date)
    product_orders = await db.get_orders_by_date(report_date, order_type="products")

    revenue_total = sum(float(row["revenue"] or 0) for row in shifts)
    meat_used_total = sum(float(row["meat_used"] or 0) for row in shifts)

    orders_agg: dict[str, float] = defaultdict(float)
    for order in product_orders:
        orders_agg[order["item"]] += float(order["quantity"])

    stock_lines = []
    for item in ("мясо", "лаваш", "картофель"):
        if item in stocks:
            unit = " кг" if item != "лаваш" else " шт"
            stock_lines.append(f"{item}: {_fmt_number(stocks[item], unit)}")

    order_lines = []
    for item, quantity in sorted(orders_agg.items()):
        order_lines.append(f"{item}: {_fmt_number(quantity)}")

    lines = [
        f"Отчёт за {report_date}",
        f"Выручка: {_fmt_number(revenue_total, ' ₽')}",
        f"Расход мяса: {_fmt_number(meat_used_total, ' кг')}",
        "",
        "Остатки:",
    ]

    if stock_lines:
        lines.extend(stock_lines)
    else:
        lines.append("нет данных")

    lines.extend(["", "Заказы продукции:"])
    if order_lines:
        lines.extend(order_lines)
    else:
        lines.append("нет заказов")

    return "\n".join(lines)
