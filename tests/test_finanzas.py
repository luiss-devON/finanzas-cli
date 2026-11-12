import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from finanzas import Store, format_amount, parse_amount
from finanzas.cli import main
from finanzas.csvio import export_csv, import_csv
from finanzas.money import plain_amount
from finanzas.report import bar, summary, trend
from finanzas.store import shift_month


class MoneyTest(unittest.TestCase):
    def test_parse_amount_formats(self):
        cases = {
            "12": 1200,
            "12.5": 1250,
            "12,50": 1250,
            "$1,234.56": 123456,
            "1.234,56": 123456,
            "0.01": 1,
            "0.005": 1,  # redondeo bancario hacia arriba
            " 7 ": 700,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), expected)

    def test_parse_amount_rejects_invalid(self):
        for bad in ("", "abc", "0", "-5", "1.2.3", "12$a", "0.001"):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                parse_amount(bad)

    def test_no_float_errors(self):
        self.assertEqual(parse_amount("0.1") + parse_amount("0.2"), parse_amount("0.3"))

    def test_format(self):
        self.assertEqual(format_amount(123456), "$1,234.56")
        self.assertEqual(format_amount(-500), "-$5.00")
        self.assertEqual(format_amount(0), "$0.00")
        self.assertEqual(plain_amount(123456), "1234.56")
        self.assertEqual(plain_amount(5), "0.05")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.store.add(date(2026, 3, 1), "ingreso", 100000, "Salario", "marzo")
        self.store.add(date(2026, 3, 5), "gasto", 2500, " Comida ", "almuerzo")
        self.store.add(date(2026, 3, 9), "gasto", 1500, "comida")
        self.store.add(date(2026, 3, 10), "gasto", 4000, "transporte")
        self.store.add(date(2026, 4, 2), "gasto", 999, "comida")

    def tearDown(self):
        self.store.close()

    def test_categories_are_normalized(self):
        self.assertEqual({m.category for m in self.store.movements()}, {"salario", "comida", "transporte"})

    def test_filters(self):
        self.assertEqual(len(self.store.movements(month="2026-03")), 4)
        self.assertEqual(len(self.store.movements(category="COMIDA")), 3)
        self.assertEqual(len(self.store.movements(month="2026-03", kind="gasto")), 3)

    def test_totals(self):
        self.assertEqual(self.store.totals("2026-03"), {"ingreso": 100000, "gasto": 8000})
        self.assertEqual(self.store.totals("2030-01"), {"ingreso": 0, "gasto": 0})
        self.assertEqual(
            self.store.totals_by_category("2026-03"), [("comida", 4000), ("transporte", 4000)]
        )

    def test_monthly_totals_includes_empty_months(self):
        data = self.store.monthly_totals("2026-04", 3)
        self.assertEqual(data, [("2026-02", 0, 0), ("2026-03", 100000, 8000), ("2026-04", 0, 999)])

    def test_delete(self):
        first = self.store.movements()[0].id
        self.assertTrue(self.store.delete(first))
        self.assertFalse(self.store.delete(first))

    def test_budgets(self):
        self.store.set_budget("Comida", 5000)
        self.store.set_budget("comida", 3000)  # actualiza
        self.assertEqual(self.store.budgets(), {"comida": 3000})
        self.assertEqual(self.store.spent("comida", "2026-03"), 4000)
        self.assertTrue(self.store.remove_budget("comida"))
        self.assertEqual(self.store.budgets(), {})

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.store.add(date.today(), "regalo", 100, "x")
        with self.assertRaises(ValueError):
            self.store.add(date.today(), "gasto", 0, "x")
        with self.assertRaises(ValueError):
            self.store.add(date.today(), "gasto", 100, "   ")
        with self.assertRaises(ValueError):
            self.store.movements(month="2026-13")

    def test_shift_month(self):
        self.assertEqual(shift_month("2026-01", -1), "2025-12")
        self.assertEqual(shift_month("2026-12", 1), "2027-01")
        self.assertEqual(shift_month("2026-05", -17), "2024-12")

    def test_reports_render(self):
        self.store.set_budget("comida", 3000)
        text = summary(self.store, "2026-03")
        self.assertIn("$1,000.00", text)
        self.assertIn("EXCEDIDO", text)
        self.assertIn("Ahorro", text)
        self.assertIn("2026-04", trend(self.store, "2026-04", 2))
        self.assertEqual(bar(0, 0, 4), "░░░░")
        self.assertEqual(bar(5, 10, 4), "██░░")


class CsvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        with Store() as src:
            src.add(date(2026, 1, 3), "gasto", 1234, "café", "con leche, sin azúcar")
            src.add(date(2026, 1, 4), "ingreso", 50000, "freelance")
            path = self.dir / "datos.csv"
            self.assertEqual(export_csv(src, path), 2)
        with Store() as dst:
            imported, errors = import_csv(dst, path)
            self.assertEqual((imported, errors), (2, []))
            first = dst.movements()[0]
            self.assertEqual((first.category, first.amount, first.description), ("café", 1234, "con leche, sin azúcar"))

    def test_semicolon_and_bad_rows(self):
        path = self.dir / "banco.csv"
        path.write_text(
            "Fecha;Tipo;Monto;Categoria;Descripcion\n"
            "2026-02-01;gasto;15,90;super;compras\n"
            "2026-02-31;gasto;10;super;fecha inválida\n"
            "2026-02-02;regalo;10;otros;tipo inválido\n"
            "\n"
            "2026-02-03;ingreso;1.500,00;salario;\n",
            encoding="utf-8",
        )
        with Store() as store:
            imported, errors = import_csv(store, path)
            self.assertEqual(imported, 2)
            self.assertEqual(len(errors), 2)
            self.assertTrue(errors[0].startswith("línea 3"))
            self.assertEqual(store.totals(), {"ingreso": 150000, "gasto": 1590})

    def test_missing_columns(self):
        path = self.dir / "malo.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        with Store() as store, self.assertRaises(ValueError):
            import_csv(store, path)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "test.db")

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(["--db", self.db, *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self):
        self.assertEqual(self.run_cli("ingreso", "2000", "salario", "--fecha", "2026-05-01")[0], 0)
        self.assertEqual(self.run_cli("presupuesto", "fijar", "comida", "100")[0], 0)

        code, out, _ = self.run_cli("gasto", "85", "comida", "super", "del", "mes", "--fecha", "2026-05-02")
        self.assertEqual(code, 0)
        self.assertIn("85%", out)  # aviso de presupuesto

        code, out, _ = self.run_cli("gasto", "20,5", "comida", "--fecha", "2026-05-03")
        self.assertIn("Te pasaste", out)

        code, out, _ = self.run_cli("listar", "--mes", "2026-05")
        self.assertIn("super del mes", out)
        self.assertIn("$1,894.50", out)  # neto

        code, out, _ = self.run_cli("resumen", "--mes", "2026-05")
        self.assertEqual(code, 0)
        self.assertIn("Balance  :        $1,894.50", out)

        code, out, _ = self.run_cli("tendencia", "--meses", "3", "--hasta", "2026-05")
        self.assertIn("2026-03", out)

        csv_path = str(Path(self._tmp.name) / "export.csv")
        code, out, _ = self.run_cli("exportar", csv_path)
        self.assertIn("3 movimientos", out)

        self.assertEqual(self.run_cli("borrar", "1")[0], 0)
        self.assertEqual(self.run_cli("borrar", "1")[0], 1)

        code, out, _ = self.run_cli("importar", csv_path)
        self.assertEqual(code, 0)
        self.assertIn("3 movimientos importados", out)

    def test_invalid_input_gives_clean_error(self):
        code, _, err = self.run_cli("gasto", "abc", "comida")
        self.assertEqual(code, 2)
        self.assertIn("monto no válido", err)
        code, _, err = self.run_cli("resumen", "--mes", "2026-99")
        self.assertEqual(code, 2)
        code, _, err = self.run_cli("importar", "no-existe.csv")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
