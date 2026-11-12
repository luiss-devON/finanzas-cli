"""Reportes de texto con gráficos de barras para la terminal."""

from __future__ import annotations

from .money import format_amount
from .store import EXPENSE, INCOME, Movement, Store

BAR_WIDTH = 24
FULL, EMPTY = "█", "░"


def bar(value: int, maximum: int, width: int = BAR_WIDTH) -> str:
    if maximum <= 0 or value <= 0:
        return EMPTY * width
    filled = min(width, max(1, round(width * value / maximum)))
    return FULL * filled + EMPTY * (width - filled)


def movements_table(movements: list[Movement]) -> str:
    if not movements:
        return "No hay movimientos para mostrar."
    lines = [f"{'ID':>4}  {'FECHA':<10}  {'CATEGORÍA':<14}  {'MONTO':>14}  DESCRIPCIÓN", "─" * 70]
    for m in movements:
        amount = format_amount(m.signed_amount)
        lines.append(f"{m.id:>4}  {m.date.isoformat():<10}  {m.category[:14]:<14}  {amount:>14}  {m.description}")
    total = sum(m.signed_amount for m in movements)
    lines += ["─" * 70, f"{'Neto':<30}  {format_amount(total):>14}   ({len(movements)} movimientos)"]
    return "\n".join(lines)


def summary(store: Store, month: str) -> str:
    totals = store.totals(month)
    income, expense = totals[INCOME], totals[EXPENSE]
    balance = income - expense
    lines = [
        f"Resumen de {month}",
        "═" * 46,
        f"  Ingresos : {format_amount(income):>16}",
        f"  Gastos   : {format_amount(expense):>16}",
        f"  Balance  : {format_amount(balance):>16}",
    ]
    if income > 0:
        lines.append(f"  Ahorro   : {100 * balance / income:>15.1f}%")

    by_category = store.totals_by_category(month, EXPENSE)
    if by_category:
        top = by_category[0][1]
        lines += ["", "Gastos por categoría", "─" * 46]
        for category, total in by_category:
            share = 100 * total / expense
            lines.append(f"  {category[:12]:<12} {bar(total, top)} {format_amount(total):>12} {share:5.1f}%")

    budget_lines = budget_status(store, month)
    if budget_lines:
        lines += ["", budget_lines]
    return "\n".join(lines)


def budget_status(store: Store, month: str) -> str:
    budgets = store.budgets()
    if not budgets:
        return ""
    lines = [f"Presupuestos de {month}", "─" * 46]
    for category, limit in budgets.items():
        spent = store.spent(category, month)
        percent = 100 * spent / limit
        flag = "  ⚠ EXCEDIDO" if spent > limit else ("  ⚠ cerca del límite" if percent >= 80 else "")
        lines.append(
            f"  {category[:12]:<12} {bar(min(spent, limit), limit)} "
            f"{format_amount(spent)} / {format_amount(limit)} ({percent:.0f}%){flag}"
        )
    return "\n".join(lines)


def trend(store: Store, last_month: str, count: int) -> str:
    data = store.monthly_totals(last_month, count)
    top = max([max(i, e) for _, i, e in data] + [0])
    lines = [f"Tendencia de los últimos {count} meses", "═" * 60]
    for month, income, expense in data:
        lines.append(f"  {month}  ingresos {bar(income, top, 18)} {format_amount(income):>12}")
        lines.append(f"  {'':7}  gastos   {bar(expense, top, 18)} {format_amount(expense):>12}")
    total_in = sum(i for _, i, _ in data)
    total_out = sum(e for _, _, e in data)
    lines += [
        "─" * 60,
        f"  Promedio mensual de gastos : {format_amount(total_out // count)}",
        f"  Balance del período        : {format_amount(total_in - total_out)}",
    ]
    return "\n".join(lines)
