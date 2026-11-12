"""Manejo de dinero sin errores de redondeo.

Los montos se guardan como enteros en centavos (nunca como float), igual que
hacen los sistemas bancarios: 0.1 + 0.2 siempre da 0.30.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_ALLOWED = re.compile(r"[0-9.,]+")


def parse_amount(text: str) -> int:
    """Convierte un texto en centavos. Acepta '12', '12.5', '12,50', '$1,234.56', '1.234,56'.

    Si aparecen punto y coma a la vez, el último es el separador decimal.
    Si solo aparece la coma, se interpreta como decimal (formato latino).
    """
    cleaned = str(text).strip().replace("$", "").replace(" ", "").replace("_", "")
    if not cleaned or not _ALLOWED.fullmatch(cleaned):
        raise ValueError(f"monto no válido: {text!r}")

    if "," in cleaned and "." in cleaned:
        decimal_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        cleaned = cleaned.replace(thousands, "").replace(decimal_sep, ".")
    else:
        cleaned = cleaned.replace(",", ".")

    if cleaned.count(".") > 1:
        raise ValueError(f"monto no válido: {text!r}")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"monto no válido: {text!r}") from None

    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise ValueError("el monto debe ser mayor que cero")
    if cents > 10**15:
        raise ValueError("monto demasiado grande")
    return cents


def format_amount(cents: int, symbol: str = "$") -> str:
    """12345 -> '$123.45' ; -500 -> '-$5.00'."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{symbol}{whole:,}.{frac:02d}"


def plain_amount(cents: int) -> str:
    """Formato sin símbolo ni miles, apto para CSV: 123456 -> '1234.56'."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"
