"""Importación y exportación CSV (compatible con Excel y Google Sheets)."""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

from .money import parse_amount, plain_amount
from .store import KINDS, Store

FIELDS = ["fecha", "tipo", "monto", "categoria", "descripcion"]


def export_csv(store: Store, path: str | os.PathLike, month: str | None = None) -> int:
    """Escribe los movimientos en un CSV. Devuelve cuántas filas se escribieron."""
    movements = store.movements(month=month)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel reconoce las tildes correctamente
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for m in movements:
            writer.writerow([m.date.isoformat(), m.kind, plain_amount(m.amount), m.category, m.description])
    return len(movements)


def import_csv(store: Store, path: str | os.PathLike) -> tuple[int, list[str]]:
    """Importa un CSV con columnas fecha,tipo,monto,categoria[,descripcion].

    Acepta separador ',' o ';'. Las filas inválidas se omiten y se reportan con su
    número de línea; las válidas se guardan todas juntas (transacción única).
    Devuelve (filas_importadas, errores).
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
        missing = [f for f in FIELDS[:4] if f not in headers]
        if missing:
            raise ValueError(f"faltan columnas en el CSV: {', '.join(missing)}")

        rows, errors = [], []
        for line, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
            if not any(row.values()):
                continue
            try:
                kind = row["tipo"].lower()
                if kind not in KINDS:
                    raise ValueError(f"tipo debe ser 'gasto' o 'ingreso', no {row['tipo']!r}")
                when = date.fromisoformat(row["fecha"])
                rows.append((when, kind, parse_amount(row["monto"]), row["categoria"], row.get("descripcion", "")))
            except (ValueError, KeyError) as exc:
                errors.append(f"línea {line}: {exc}")

    for when, kind, amount, category, description in rows:
        store.add(when, kind, amount, category, description)
    return len(rows), errors
