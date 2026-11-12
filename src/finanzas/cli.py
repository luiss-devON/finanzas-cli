"""Interfaz de línea de comandos de finanzas."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .csvio import export_csv, import_csv
from .money import format_amount, parse_amount
from .report import budget_status, movements_table, summary, trend
from .store import EXPENSE, INCOME, KINDS, Store, validate_month

DEFAULT_DB = Path.home() / ".finanzas" / "finanzas.db"


def _amount(text: str) -> int:
    try:
        return parse_amount(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha no válida: {text!r} (formato AAAA-MM-DD)") from None


def _month(text: str) -> str:
    try:
        return validate_month(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError("debe ser un entero mayor que 0")
    return value


def _port(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("el puerto debe estar entre 1 y 65535")
    return value


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finanzas",
        description="Controla tus gastos, ingresos y presupuestos desde la terminal.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", type=Path, default=None,
                        help=f"archivo de base de datos (o variable FINANZAS_DB). Por defecto: {DEFAULT_DB}")
    sub = parser.add_subparsers(dest="comando", required=True)

    for kind, help_text in ((EXPENSE, "registra un gasto"), (INCOME, "registra un ingreso")):
        p = sub.add_parser(kind, help=help_text)
        p.add_argument("monto", type=_amount, help="ej. 12.50")
        p.add_argument("categoria", help="ej. comida, transporte, salario")
        p.add_argument("descripcion", nargs="*", help="texto opcional")
        p.add_argument("--fecha", type=_date, default=None, help="AAAA-MM-DD (por defecto: hoy)")

    p = sub.add_parser("listar", help="muestra los movimientos")
    p.add_argument("--mes", type=_month, help="AAAA-MM (por defecto: mes actual)")
    p.add_argument("--todo", action="store_true", help="todos los meses")
    p.add_argument("--categoria")
    p.add_argument("--tipo", choices=KINDS)

    p = sub.add_parser("borrar", help="elimina un movimiento por su ID")
    p.add_argument("id", type=_positive)

    p = sub.add_parser("resumen", help="balance del mes, gastos por categoría y presupuestos")
    p.add_argument("--mes", type=_month)

    p = sub.add_parser("tendencia", help="ingresos y gastos de los últimos meses")
    p.add_argument("--meses", type=_positive, default=6)
    p.add_argument("--hasta", type=_month, help="último mes a mostrar (por defecto: actual)")

    p = sub.add_parser("presupuesto", help="gestiona límites mensuales por categoría")
    psub = p.add_subparsers(dest="accion", required=True)
    pf = psub.add_parser("fijar", help="fija o actualiza un límite mensual")
    pf.add_argument("categoria")
    pf.add_argument("monto", type=_amount)
    pq = psub.add_parser("quitar", help="elimina un presupuesto")
    pq.add_argument("categoria")
    pv = psub.add_parser("ver", help="muestra cuánto llevas gastado de cada presupuesto")
    pv.add_argument("--mes", type=_month)

    p = sub.add_parser("exportar", help="guarda los movimientos en CSV")
    p.add_argument("archivo", type=Path)
    p.add_argument("--mes", type=_month, help="solo un mes (por defecto: todos)")

    p = sub.add_parser("importar", help="carga movimientos desde un CSV")
    p.add_argument("archivo", type=Path)

    p = sub.add_parser("web", help="abre el panel con gráficos en el navegador")
    p.add_argument("--puerto", type=_port, default=8000, help="puerto local (por defecto: 8000)")
    p.add_argument("--no-abrir", action="store_true", help="no abrir el navegador automáticamente")
    return parser


def run(args: argparse.Namespace, store: Store) -> int:
    cmd = args.comando

    if cmd in KINDS:
        when = args.fecha or date.today()
        new_id = store.add(when, cmd, args.monto, args.categoria, " ".join(args.descripcion))
        print(f"✔ {cmd.capitalize()} #{new_id} registrado: {format_amount(args.monto)} en "
              f"'{args.categoria.strip().lower()}' ({when.isoformat()})")
        if cmd == EXPENSE:
            _warn_budget(store, args.categoria, when.strftime("%Y-%m"))
        return 0

    if cmd == "listar":
        month = None if args.todo else (args.mes or current_month())
        print(movements_table(store.movements(month=month, category=args.categoria, kind=args.tipo)))
        return 0

    if cmd == "borrar":
        if store.delete(args.id):
            print(f"✔ Movimiento #{args.id} eliminado.")
            return 0
        print(f"No existe el movimiento #{args.id}.", file=sys.stderr)
        return 1

    if cmd == "resumen":
        print(summary(store, args.mes or current_month()))
        return 0

    if cmd == "tendencia":
        print(trend(store, args.hasta or current_month(), args.meses))
        return 0

    if cmd == "presupuesto":
        if args.accion == "fijar":
            store.set_budget(args.categoria, args.monto)
            print(f"✔ Presupuesto de '{args.categoria.strip().lower()}': {format_amount(args.monto)} al mes.")
        elif args.accion == "quitar":
            if not store.remove_budget(args.categoria):
                print(f"No hay presupuesto para '{args.categoria}'.", file=sys.stderr)
                return 1
            print(f"✔ Presupuesto de '{args.categoria.strip().lower()}' eliminado.")
        else:
            print(budget_status(store, args.mes or current_month()) or "No hay presupuestos definidos.")
        return 0

    if cmd == "exportar":
        count = export_csv(store, args.archivo, args.mes)
        print(f"✔ {count} movimientos exportados a {args.archivo}")
        return 0

    if cmd == "importar":
        if not args.archivo.is_file():
            print(f"Error: no existe el archivo {args.archivo}", file=sys.stderr)
            return 1
        imported, errors = import_csv(store, args.archivo)
        for err in errors:
            print(f"Aviso: {err}", file=sys.stderr)
        print(f"✔ {imported} movimientos importados, {len(errors)} filas con errores.")
        return 0 if not errors else 2

    raise AssertionError(f"comando no manejado: {cmd}")


def _warn_budget(store: Store, category: str, month: str) -> None:
    limit = store.budgets().get(category.strip().lower())
    if not limit:
        return
    spent = store.spent(category, month)
    if spent > limit:
        print(f"⚠ Te pasaste del presupuesto de '{category.strip().lower()}': "
              f"{format_amount(spent)} de {format_amount(limit)}.")
    elif spent >= limit * 0.8:
        print(f"⚠ Llevas el {100 * spent / limit:.0f}% del presupuesto de '{category.strip().lower()}'.")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    db_path = args.db or Path(os.environ.get("FINANZAS_DB") or DEFAULT_DB)
    try:
        if args.comando == "web":
            from .web import serve
            return serve(db_path, args.puerto, open_browser=not args.no_abrir)
        with Store(db_path) as store:
            return run(args, store)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
