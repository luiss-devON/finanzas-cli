"""Panel web local: gráficos, formularios y presupuestos en el navegador.

Usa solo la biblioteca estándar. El servidor escucha únicamente en 127.0.0.1,
valida el encabezado Host y exige un token en cada formulario (protección CSRF).
"""

from __future__ import annotations

import hmac
import os
import secrets
import webbrowser
from dataclasses import dataclass, field
from datetime import date
from html import escape as e
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .csvio import csv_text
from .money import format_amount, parse_amount
from .store import EXPENSE, INCOME, KINDS, Store, shift_month, validate_month

MAX_BODY = 100_000
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
PALETA = ("#6366f1", "#f59e0b", "#10b981", "#ef4444", "#06b6d4", "#a855f7", "#84cc16", "#ec4899")
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)


def _html(status: int, text: str) -> Response:
    return Response(status, text.encode("utf-8"), [("Content-Type", "text/html; charset=utf-8")])


def _first(data: dict[str, list[str]], key: str) -> str:
    return (data.get(key) or [""])[0].strip()


def _month_label(month: str) -> str:
    year, mon = month.split("-")
    return f"{MESES[int(mon) - 1]} {year[2:]}"


# ---------- gráficos SVG ----------

def svg_categories(data: list[tuple[str, int]]) -> str:
    if not data:
        return '<p class="empty">Sin gastos este mes.</p>'
    top, row, width, label_w, amount_w = data[0][1], 30, 340, 90, 78
    bar_w = width - label_w - amount_w
    parts = []
    for i, (category, total) in enumerate(data):
        y = i * row
        w = max(3, round(bar_w * total / top))
        parts.append(
            f'<text x="0" y="{y + 20}" class="lbl">{e(category[:11])}</text>'
            f'<rect x="{label_w}" y="{y + 6}" width="{w}" height="20" rx="5" fill="{PALETA[i % len(PALETA)]}">'
            f"<title>{e(category)}: {e(format_amount(total))}</title></rect>"
            f'<text x="{width}" y="{y + 20}" text-anchor="end" class="val">{e(format_amount(total))}</text>'
        )
    return (f'<svg viewBox="0 0 {width} {row * len(data)}" role="img" '
            f'aria-label="Gastos por categoría">{"".join(parts)}</svg>')


def svg_trend(data: list[tuple[str, int, int]]) -> str:
    width, height, base, top_pad = 340, 200, 175, 10
    top = max([max(i, x) for _, i, x in data] + [0])
    if top == 0:
        return '<p class="empty">Aún no hay movimientos en estos meses.</p>'
    group = width / len(data)
    bar = group * 0.32
    parts = [f'<line x1="0" y1="{base}" x2="{width}" y2="{base}" class="axis"/>']
    for i, (month, income, expense) in enumerate(data):
        x = i * group + group * 0.16
        for j, (value, cls, name) in enumerate(((income, "inc", "Ingresos"), (expense, "exp", "Gastos"))):
            h = round((base - top_pad) * value / top, 1)
            parts.append(
                f'<rect x="{x + j * bar:.1f}" y="{base - h}" width="{bar:.1f}" height="{h}" rx="4" class="{cls}">'
                f"<title>{name} {_month_label(month)}: {e(format_amount(value))}</title></rect>"
            )
        parts.append(f'<text x="{i * group + group / 2:.1f}" y="{base + 18}" text-anchor="middle" class="lbl">'
                     f"{_month_label(month)}</text>")
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Ingresos y gastos por mes">{"".join(parts)}</svg>')


# ---------- página ----------

STYLE = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f1f5f9;color:#0f172a}
header{background:#0f172a;color:#fff;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem}
header h1{font-size:1.3rem;margin:0}.nav a{color:#fff;text-decoration:none;padding:.3rem .7rem;border-radius:8px;background:#1e293b;margin-left:.3rem}
main{max-width:1100px;margin:auto;padding:1.5rem;display:grid;gap:1.2rem;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.card{background:#fff;border-radius:14px;padding:1.2rem;box-shadow:0 1px 3px #0001}.wide{grid-column:1/-1}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem}
.kpi{background:#fff;border-radius:14px;padding:1rem 1.2rem;box-shadow:0 1px 3px #0001}.kpi span{color:#64748b;font-size:.85rem}
.kpi b{display:block;font-size:1.6rem;margin-top:.2rem}.pos{color:#059669}.neg{color:#dc2626}
h2{font-size:1.05rem;margin:0 0 .8rem}svg{width:100%;height:auto}.lbl{font-size:13px;fill:#334155}.val{font-size:13px;font-weight:600;fill:#0f172a}
.axis{stroke:#cbd5e1}.inc{fill:#10b981}.exp{fill:#ef4444}.legend span{display:inline-block;width:10px;height:10px;border-radius:3px;margin:0 .3rem 0 .8rem}
form.grid{display:grid;gap:.6rem;grid-template-columns:1fr 1fr}form.grid .full{grid-column:1/-1}
input,select,button{font:inherit;padding:.55rem .7rem;border:1px solid #cbd5e1;border-radius:8px;width:100%}
button{background:#4f46e5;color:#fff;border:none;cursor:pointer;font-weight:600}button:hover{background:#4338ca}
table{width:100%;border-collapse:collapse;font-size:.92rem}td,th{padding:.5rem;border-bottom:1px solid #e2e8f0;text-align:left}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}.del{background:none;color:#dc2626;border:none;padding:0;width:auto;font-size:1.1rem}
.budget{margin-bottom:.8rem}.budget .row{display:flex;justify-content:space-between;font-size:.9rem}
.track{background:#e2e8f0;border-radius:99px;height:10px;margin-top:.3rem;overflow:hidden}.fill{height:100%;border-radius:99px}
.msg{grid-column:1/-1;padding:.8rem 1rem;border-radius:10px}.ok{background:#dcfce7;color:#166534}.error{background:#fee2e2;color:#991b1b}
.empty{color:#64748b}.actions{margin-top:.8rem;font-size:.9rem}
"""


def _budgets_html(store: Store, month: str, token: str) -> str:
    items = []
    for category, limit in store.budgets().items():
        spent = store.spent(category, month)
        pct = 100 * spent / limit
        color = "#dc2626" if spent > limit else "#f59e0b" if pct >= 80 else "#10b981"
        items.append(
            f'<div class="budget"><div class="row"><b>{e(category)}</b>'
            f"<span>{e(format_amount(spent))} / {e(format_amount(limit))} ({pct:.0f}%)</span></div>"
            f'<div class="track"><div class="fill" style="width:{min(pct, 100):.1f}%;background:{color}"></div></div>'
            f'<form method="post" action="/presupuesto/quitar" style="text-align:right">'
            f'<input type="hidden" name="token" value="{token}"><input type="hidden" name="mes" value="{month}">'
            f'<input type="hidden" name="categoria" value="{e(category)}">'
            f'<button class="del" title="Quitar presupuesto" style="font-size:.8rem">quitar</button></form></div>'
        )
    return "".join(items) or '<p class="empty">No hay presupuestos. Crea uno abajo.</p>'


def render_dashboard(store: Store, month: str, token: str, msg: str = "", ok: bool = True) -> str:
    totals = store.totals(month)
    income, expense = totals[INCOME], totals[EXPENSE]
    balance = income - expense
    saving = f"{100 * balance / income:.1f}%" if income else "—"
    movements = store.movements(month=month)
    categories = sorted({c for c, _ in store.totals_by_category(None, EXPENSE)}
                        | {c for c, _ in store.totals_by_category(None, INCOME)})
    hidden = f'<input type="hidden" name="token" value="{token}"><input type="hidden" name="mes" value="{month}">'

    rows = "".join(
        f"<tr><td>{m.date.isoformat()}</td><td>{e(m.category)}</td><td>{e(m.description)}</td>"
        f'<td class="num {"pos" if m.kind == INCOME else "neg"}">{e(format_amount(m.signed_amount))}</td>'
        f'<td><form method="post" action="/borrar" onsubmit="return confirm(\'¿Borrar este movimiento?\')">'
        f'{hidden}<input type="hidden" name="id" value="{m.id}"><button class="del" title="Borrar">✕</button></form></td></tr>'
        for m in reversed(movements)
    ) or '<tr><td colspan="5" class="empty">No hay movimientos este mes.</td></tr>'

    today = date.today()
    default_date = today.isoformat() if today.strftime("%Y-%m") == month else f"{month}-01"
    options = "".join(f'<option value="{e(c)}">' for c in categories)
    banner = f'<div class="msg {"ok" if ok else "error"}">{e(msg)}</div>' if msg else ""

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finanzas · {_month_label(month)}</title><style>{STYLE}</style></head><body>
<header><h1>💰 Finanzas · {_month_label(month)}</h1><nav class="nav">
<a href="/?mes={shift_month(month, -1)}">◀ anterior</a><a href="/">hoy</a><a href="/?mes={shift_month(month, 1)}">siguiente ▶</a></nav></header>
<main>{banner}
<section class="kpis wide">
<div class="kpi"><span>Ingresos</span><b class="pos">{e(format_amount(income))}</b></div>
<div class="kpi"><span>Gastos</span><b class="neg">{e(format_amount(expense))}</b></div>
<div class="kpi"><span>Balance</span><b class="{"pos" if balance >= 0 else "neg"}">{e(format_amount(balance))}</b></div>
<div class="kpi"><span>Ahorro</span><b>{saving}</b></div>
</section>
<section class="card"><h2>Gastos por categoría</h2>{svg_categories(store.totals_by_category(month, EXPENSE))}</section>
<section class="card"><h2>Últimos 6 meses</h2><p class="legend" style="margin:-.4rem 0 .6rem;font-size:.85rem;color:#475569"><span style="background:#10b981;margin-left:0"></span>ingresos<span style="background:#ef4444"></span>gastos</p>
{svg_trend(store.monthly_totals(month, 6))}</section>
<section class="card"><h2>Nuevo movimiento</h2>
<form class="grid" method="post" action="/movimiento">{hidden}
<select name="tipo"><option value="gasto">Gasto</option><option value="ingreso">Ingreso</option></select>
<input name="monto" placeholder="Monto, ej. 12.50" inputmode="decimal" required>
<input name="categoria" placeholder="Categoría" list="cats" required><datalist id="cats">{options}</datalist>
<input type="date" name="fecha" value="{default_date}" required>
<input class="full" name="descripcion" placeholder="Descripción (opcional)" maxlength="200">
<button class="full">Guardar</button></form></section>
<section class="card"><h2>Presupuestos</h2>{_budgets_html(store, month, token)}
<form class="grid" method="post" action="/presupuesto">{hidden}
<input name="categoria" placeholder="Categoría" list="cats" required><input name="monto" placeholder="Límite mensual" inputmode="decimal" required>
<button class="full">Fijar presupuesto</button></form></section>
<section class="card wide"><h2>Movimientos de {_month_label(month)} ({len(movements)})</h2>
<table><thead><tr><th>Fecha</th><th>Categoría</th><th>Descripción</th><th class="num">Monto</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="actions"><a href="/exportar.csv?mes={month}">⬇ Descargar CSV de este mes</a> · <a href="/exportar.csv">⬇ Todo</a></p></section>
</main></body></html>"""


# ---------- aplicación ----------

class App:
    def __init__(self, db_path: str | os.PathLike, port: int = 8000, token: str | None = None):
        self.db_path = db_path
        self.port = port
        self.token = token or secrets.token_urlsafe(24)

    def _host_ok(self, host: str) -> bool:
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def handle(self, method: str, target: str, host: str, body: bytes = b"") -> Response:
        if not self._host_ok(host):
            return _html(403, "Host no permitido.")
        url = urlsplit(target)
        query = parse_qs(url.query)
        try:
            with Store(self.db_path) as store:
                if method == "GET" and url.path == "/":
                    month = _first(query, "mes") or date.today().strftime("%Y-%m")
                    try:
                        validate_month(month)
                    except ValueError:
                        month = date.today().strftime("%Y-%m")
                    page = render_dashboard(store, month, self.token, _first(query, "msg"), _first(query, "ok") != "0")
                    return _html(200, page)
                if method == "GET" and url.path == "/exportar.csv":
                    month = _first(query, "mes") or None
                    if month:
                        validate_month(month)
                    text, _ = csv_text(store, month)
                    name = f"finanzas-{month or 'todo'}.csv"
                    return Response(200, ("﻿" + text).encode("utf-8"), [
                        ("Content-Type", "text/csv; charset=utf-8"),
                        ("Content-Disposition", f'attachment; filename="{name}"'),
                    ])
                if method == "POST" and url.path in ("/movimiento", "/borrar", "/presupuesto", "/presupuesto/quitar"):
                    return self._post(store, url.path, parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True))
                if url.path in ("/", "/exportar.csv", "/movimiento", "/borrar", "/presupuesto", "/presupuesto/quitar"):
                    return _html(405, "Método no permitido.")
                return _html(404, "Página no encontrada.")
        except ValueError as exc:
            return _html(400, f"Solicitud no válida: {e(str(exc))}")

    def _post(self, store: Store, path: str, form: dict[str, list[str]]) -> Response:
        if not hmac.compare_digest(_first(form, "token").encode(), self.token.encode()):
            return _html(403, "Token no válido. Recarga la página.")
        month = _first(form, "mes") or date.today().strftime("%Y-%m")
        try:
            validate_month(month)
        except ValueError:
            month = date.today().strftime("%Y-%m")
        try:
            if path == "/movimiento":
                kind = _first(form, "tipo")
                if kind not in KINDS:
                    raise ValueError("tipo no válido")
                amount = parse_amount(_first(form, "monto"))
                try:
                    when = date.fromisoformat(_first(form, "fecha"))
                except ValueError:
                    raise ValueError("fecha no válida") from None
                store.add(when, kind, amount, _first(form, "categoria"), _first(form, "descripcion")[:200])
                month = when.strftime("%Y-%m")
                msg = f"{kind.capitalize()} de {format_amount(amount)} guardado."
                if kind == EXPENSE:
                    msg += _budget_warning(store, _first(form, "categoria"), month)
            elif path == "/borrar":
                if not store.delete(int(_first(form, "id") or 0)):
                    raise ValueError("ese movimiento ya no existe")
                msg = "Movimiento borrado."
            elif path == "/presupuesto":
                amount = parse_amount(_first(form, "monto"))
                store.set_budget(_first(form, "categoria"), amount)
                msg = f"Presupuesto fijado en {format_amount(amount)} al mes."
            else:
                if not store.remove_budget(_first(form, "categoria")):
                    raise ValueError("ese presupuesto no existe")
                msg = "Presupuesto eliminado."
            ok = True
        except ValueError as exc:
            msg, ok = f"Error: {exc}", False
        location = "/?" + urlencode({"mes": month, "ok": "1" if ok else "0", "msg": msg}, quote_via=quote)
        return Response(303, b"", [("Location", location)])


def _budget_warning(store: Store, category: str, month: str) -> str:
    key = " ".join(category.split()).lower()
    limit = store.budgets().get(key)
    if not limit:
        return ""
    spent = store.spent(key, month)
    if spent > limit:
        return f" ⚠ Te pasaste del presupuesto de '{key}' ({format_amount(spent)} de {format_amount(limit)})."
    if spent >= limit * 0.8:
        return f" ⚠ Llevas el {100 * spent / limit:.0f}% del presupuesto de '{key}'."
    return ""


# ---------- servidor ----------

def make_server(db_path: str | os.PathLike, port: int = 8000) -> tuple[ThreadingHTTPServer, App]:
    app = App(db_path, port)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._run("GET")

        def do_POST(self) -> None:
            self._run("POST")

        def _run(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                resp = _html(413, "Solicitud demasiado grande.")
            else:
                body = self.rfile.read(length) if length else b""
                resp = app.handle(method, self.path, self.headers.get("Host", ""), body)
            self.send_response(resp.status)
            for name, value in resp.headers + [("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
                                               ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")]:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            self.wfile.write(resp.body)

        def log_message(self, *args) -> None:
            pass

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        raise OSError(f"el puerto {port} está ocupado; prueba con --puerto {port + 1}") from None
    app.port = httpd.server_address[1]
    return httpd, app


def serve(db_path: str | os.PathLike, port: int = 8000, open_browser: bool = True) -> int:
    httpd, app = make_server(db_path, port)
    url = f"http://127.0.0.1:{app.port}/"
    print(f"Panel web en {url}  (Ctrl+C para detener)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        httpd.server_close()
    return 0
