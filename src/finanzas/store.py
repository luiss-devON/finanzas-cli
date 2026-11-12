"""Almacenamiento en SQLite de movimientos y presupuestos."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

INCOME = "ingreso"
EXPENSE = "gasto"
KINDS = (INCOME, EXPENSE)
_MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")

SCHEMA = """
CREATE TABLE IF NOT EXISTS movimientos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha       TEXT    NOT NULL,
    tipo        TEXT    NOT NULL CHECK (tipo IN ('gasto', 'ingreso')),
    monto       INTEGER NOT NULL CHECK (monto > 0),
    categoria   TEXT    NOT NULL,
    descripcion TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_movimientos_fecha ON movimientos (fecha);
CREATE TABLE IF NOT EXISTS presupuestos (
    categoria TEXT PRIMARY KEY,
    limite    INTEGER NOT NULL CHECK (limite > 0)
);
"""


@dataclass(frozen=True)
class Movement:
    id: int
    date: date
    kind: str
    amount: int  # centavos
    category: str
    description: str

    @property
    def signed_amount(self) -> int:
        return self.amount if self.kind == INCOME else -self.amount


def normalize_category(name: str) -> str:
    category = " ".join(str(name).split()).lower()
    if not category:
        raise ValueError("la categoría no puede estar vacía")
    return category


def validate_month(month: str) -> str:
    if not _MONTH_RE.fullmatch(month):
        raise ValueError(f"mes no válido: {month!r} (formato AAAA-MM, ej. 2026-03)")
    return month


def shift_month(month: str, delta: int) -> str:
    """shift_month('2026-01', -1) -> '2025-12'."""
    year, mon = map(int, validate_month(month).split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


class Store:
    """Acceso a la base de datos. Usar como context manager para cerrarla siempre."""

    def __init__(self, path: str | os.PathLike = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ---------- movimientos ----------
    def add(self, when: date, kind: str, amount: int, category: str, description: str = "") -> int:
        if kind not in KINDS:
            raise ValueError(f"tipo no válido: {kind!r}")
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("el monto debe ser un entero positivo en centavos")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO movimientos (fecha, tipo, monto, categoria, descripcion) VALUES (?, ?, ?, ?, ?)",
                (when.isoformat(), kind, amount, normalize_category(category), description.strip()),
            )
        return int(cur.lastrowid)

    def delete(self, movement_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM movimientos WHERE id = ?", (movement_id,))
        return cur.rowcount > 0

    def movements(
        self, month: str | None = None, category: str | None = None, kind: str | None = None
    ) -> list[Movement]:
        sql, params = self._filters(month, category, kind)
        rows = self.conn.execute(
            f"SELECT * FROM movimientos {sql} ORDER BY fecha, id", params
        ).fetchall()
        return [
            Movement(r["id"], date.fromisoformat(r["fecha"]), r["tipo"], r["monto"], r["categoria"], r["descripcion"])
            for r in rows
        ]

    def totals(self, month: str | None = None) -> dict[str, int]:
        sql, params = self._filters(month, None, None)
        rows = self.conn.execute(
            f"SELECT tipo, SUM(monto) AS total FROM movimientos {sql} GROUP BY tipo", params
        ).fetchall()
        result = {INCOME: 0, EXPENSE: 0}
        result.update({r["tipo"]: r["total"] for r in rows})
        return result

    def totals_by_category(self, month: str | None = None, kind: str = EXPENSE) -> list[tuple[str, int]]:
        sql, params = self._filters(month, None, kind)
        rows = self.conn.execute(
            f"SELECT categoria, SUM(monto) AS total FROM movimientos {sql} "
            "GROUP BY categoria ORDER BY total DESC, categoria",
            params,
        ).fetchall()
        return [(r["categoria"], r["total"]) for r in rows]

    def monthly_totals(self, last_month: str, count: int) -> list[tuple[str, int, int]]:
        """[(mes, ingresos, gastos)] de los `count` meses que terminan en `last_month`."""
        if count < 1:
            raise ValueError("la cantidad de meses debe ser al menos 1")
        months = [shift_month(last_month, -i) for i in range(count - 1, -1, -1)]
        rows = self.conn.execute(
            "SELECT substr(fecha, 1, 7) AS mes, tipo, SUM(monto) AS total FROM movimientos "
            "WHERE substr(fecha, 1, 7) BETWEEN ? AND ? GROUP BY mes, tipo",
            (months[0], months[-1]),
        ).fetchall()
        data = {m: {INCOME: 0, EXPENSE: 0} for m in months}
        for r in rows:
            data[r["mes"]][r["tipo"]] = r["total"]
        return [(m, data[m][INCOME], data[m][EXPENSE]) for m in months]

    # ---------- presupuestos ----------
    def set_budget(self, category: str, limit: int) -> None:
        if limit <= 0:
            raise ValueError("el presupuesto debe ser mayor que cero")
        with self.conn:
            self.conn.execute(
                "INSERT INTO presupuestos (categoria, limite) VALUES (?, ?) "
                "ON CONFLICT (categoria) DO UPDATE SET limite = excluded.limite",
                (normalize_category(category), limit),
            )

    def remove_budget(self, category: str) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM presupuestos WHERE categoria = ?", (normalize_category(category),))
        return cur.rowcount > 0

    def budgets(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT categoria, limite FROM presupuestos ORDER BY categoria").fetchall()
        return {r["categoria"]: r["limite"] for r in rows}

    def spent(self, category: str, month: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(monto), 0) AS total FROM movimientos "
            "WHERE tipo = 'gasto' AND categoria = ? AND substr(fecha, 1, 7) = ?",
            (normalize_category(category), validate_month(month)),
        ).fetchone()
        return int(row["total"])

    # ---------- utilidades ----------
    @staticmethod
    def _filters(month: str | None, category: str | None, kind: str | None) -> tuple[str, list]:
        clauses, params = [], []
        if month:
            clauses.append("substr(fecha, 1, 7) = ?")
            params.append(validate_month(month))
        if category:
            clauses.append("categoria = ?")
            params.append(normalize_category(category))
        if kind:
            if kind not in KINDS:
                raise ValueError(f"tipo no válido: {kind!r}")
            clauses.append("tipo = ?")
            params.append(kind)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params
