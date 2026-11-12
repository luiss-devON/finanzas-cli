import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from finanzas import Store
from finanzas.web import App, make_server, svg_categories, svg_trend

HOST = "127.0.0.1:8000"


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "web.db"
        self.app = App(self.db, port=8000, token="tok")

    def tearDown(self):
        self._tmp.cleanup()

    def get(self, path, host=HOST):
        return self.app.handle("GET", path, host)

    def post(self, path, token="tok", **data):
        return self.app.handle("POST", path, HOST, urlencode({"token": token, **data}).encode())

    def flash(self, resp):
        self.assertEqual(resp.status, 303)
        query = parse_qs(urlsplit(dict(resp.headers)["Location"]).query)
        return query["msg"][0], query["ok"][0] == "1", query["mes"][0]

    def test_dashboard_vacio(self):
        resp = self.get("/")
        self.assertEqual(resp.status, 200)
        self.assertIn("No hay movimientos este mes", resp.body.decode())

    def test_flujo_completo(self):
        msg, ok, mes = self.flash(self.post("/movimiento", tipo="ingreso", monto="1500", categoria="Salario",
                                             fecha="2026-05-01", mes="2026-05"))
        self.assertTrue(ok, msg)
        self.assertEqual(mes, "2026-05")
        self.assertTrue(self.flash(self.post("/presupuesto", categoria="comida", monto="100", mes="2026-05"))[1])
        msg, ok, _ = self.flash(self.post("/movimiento", tipo="gasto", monto="85,50", categoria="comida",
                                          descripcion="<b>super</b>", fecha="2026-05-02", mes="2026-05"))
        self.assertTrue(ok)
        self.assertIn("86%", msg)

        html = self.get("/?mes=2026-05").body.decode()
        self.assertIn("$1,414.50", html)  # balance
        self.assertIn("&lt;b&gt;super&lt;/b&gt;", html)  # escapado
        self.assertNotIn("<b>super</b>", html)
        self.assertIn("<svg", html)

        csv = self.get("/exportar.csv?mes=2026-05")
        self.assertEqual(csv.status, 200)
        self.assertTrue(csv.body.startswith("﻿fecha,tipo,monto".encode()))
        self.assertIn(b"85.50", csv.body)

        with Store(self.db) as store:
            gasto_id = store.movements(kind="gasto")[0].id
        self.assertTrue(self.flash(self.post("/borrar", id=str(gasto_id), mes="2026-05"))[1])
        self.assertFalse(self.flash(self.post("/borrar", id=str(gasto_id), mes="2026-05"))[1])
        self.assertTrue(self.flash(self.post("/presupuesto/quitar", categoria="comida"))[1])
        with Store(self.db) as store:
            self.assertEqual(store.totals(), {"ingreso": 150000, "gasto": 0})
            self.assertEqual(store.budgets(), {})

    def test_errores_de_formulario(self):
        for data in ({"tipo": "gasto", "monto": "abc", "categoria": "x", "fecha": "2026-05-01"},
                     {"tipo": "gasto", "monto": "5", "categoria": "x", "fecha": "ayer"},
                     {"tipo": "robo", "monto": "5", "categoria": "x", "fecha": "2026-05-01"},
                     {"tipo": "gasto", "monto": "5", "categoria": "  ", "fecha": "2026-05-01"}):
            with self.subTest(data=data):
                msg, ok, _ = self.flash(self.post("/movimiento", **data))
                self.assertFalse(ok)
                self.assertTrue(msg.startswith("Error:"))
        self.assertFalse(self.flash(self.post("/borrar", id="no-numero"))[1])

    def test_seguridad(self):
        self.assertEqual(self.post("/movimiento", token="malo", tipo="gasto", monto="5",
                                   categoria="x", fecha="2026-05-01").status, 403)
        self.assertEqual(self.get("/", host="evil.com").status, 403)
        self.assertEqual(self.get("/", host="127.0.0.1:9999").status, 403)
        self.assertEqual(self.get("/no-existe").status, 404)
        self.assertEqual(self.app.handle("POST", "/", HOST).status, 405)
        self.assertEqual(self.get("/exportar.csv?mes=2026-13").status, 400)
        self.assertEqual(self.get("/?mes=basura").status, 200)  # vuelve al mes actual

    def test_graficos(self):
        self.assertIn("Sin gastos", svg_categories([]))
        svg = svg_categories([("comida", 1000), ("<x>", 500)])
        self.assertIn("&lt;x&gt;", svg)
        self.assertIn("Aún no hay", svg_trend([("2026-01", 0, 0)]))
        self.assertIn("ene 26", svg_trend([("2026-01", 100, 50), ("2026-02", 0, 0)]))


class WebServerTest(unittest.TestCase):
    def test_servidor_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd, app = make_server(Path(tmp) / "s.db", port=0)
            hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
            hilo.start()
            # Sin proxy: la conexión debe ir directo a 127.0.0.1.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                base = f"http://127.0.0.1:{app.port}"
                with opener.open(base + "/") as r:
                    self.assertEqual(r.status, 200)
                    self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
                    self.assertIn(app.token, r.read().decode())
                datos = urlencode({"token": app.token, "tipo": "gasto", "monto": "3.20", "categoria": "bus",
                                   "fecha": date.today().isoformat()}).encode()
                with opener.open(base + "/movimiento", data=datos) as r:  # sigue la redirección 303
                    self.assertIn("$3.20", r.read().decode())
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    opener.open(base + "/movimiento", data=b"token=malo")
                self.assertEqual(ctx.exception.code, 403)
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
