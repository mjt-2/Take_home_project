"""
Integration tests for the input paths: CSV, JSON, and Excel, exercised
through the real readers and normalize(), not just the engine.

    python -m unittest test_run_expenses -v

These complement test_policy_engine.py, which only ever calls
evaluate_expense() on hand-built dicts. This file proves that the readers
plus normalize() feed the engine the same thing regardless of format - the
brief's "equivalent records produce identical results across formats" and
"new records require no code changes" requirements.
"""

import csv
import json
import os
import shutil
import tempfile
import unittest

from policy_engine import evaluate_expense
from run_expenses import normalize, read_csv, read_json_arg, read_json_file, read_workbook


# A record with one of every column type, expressed as native Python values -
# the shape normalize()/read_json_arg would see from a hand-built --row.
SAMPLE_RECORD = dict(
    id="X01", merchant="Campus Bistro", date="2026-09-01", category="Meal",
    currency="USD", subtotal_or_base=22.0, tax=2.0, tip=4.0, tolls=0,
    nights=0, room_charge=0, parking=0, minibar=0, claimed_amount=28.0,
    receipt_present=True, manager_approval=False, attendees=1,
)
EXPECTED = ("APPROVE", 28.00, "P3")


def verdict(record):
    got = evaluate_expense(normalize(record))
    return got["decision"], got["reimbursable_amount"], got["policy_id"]


class TestJSONPath(unittest.TestCase):
    def test_single_json_object_via_row_arg(self):
        text = json.dumps(SAMPLE_RECORD)
        records = read_json_arg(text)
        self.assertEqual(len(records), 1)
        self.assertEqual(verdict(records[0]), EXPECTED)

    def test_multiple_json_records_via_row_arg(self):
        text = json.dumps([SAMPLE_RECORD, {**SAMPLE_RECORD, "id": "X02"}])
        records = read_json_arg(text)
        self.assertEqual(len(records), 2)
        self.assertEqual(verdict(records[0]), EXPECTED)
        self.assertEqual(verdict(records[1]), EXPECTED)

    def test_json_file_with_array(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rows.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([SAMPLE_RECORD], fh)
            records = read_json_file(path)
        self.assertEqual(verdict(records[0]), EXPECTED)

    def test_invalid_json_raises_a_clear_error(self):
        with self.assertRaises(ValueError):
            read_json_arg("{not valid json")

    def test_json_array_of_non_objects_raises_a_clear_error(self):
        with self.assertRaises(ValueError):
            read_json_arg("[1, 2, 3]")

    def test_missing_json_file_raises_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            read_json_file("does_not_exist_12345.json")

    def test_json_nan_claimed_amount_is_not_approved(self):
        # Python's json module accepts the non-standard NaN/Infinity
        # literals, which is exactly how a NaN claimed_amount can arrive
        # from a JSON record in practice.
        text = '{"id":"X03","merchant":"M","date":"2026-09-01","category":"Meal",' \
               '"currency":"USD","subtotal_or_base":22.0,"claimed_amount":NaN}'
        records = read_json_arg(text)
        got = evaluate_expense(normalize(records[0]))
        self.assertNotIn(got["decision"], ("APPROVE", "PARTIAL"))


class TestCSVPath(unittest.TestCase):
    def _write_csv(self, rows, fieldnames=None):
        fieldnames = fieldnames or list(rows[0].keys())
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                         newline="", encoding="utf-8")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_csv_row_matches_json_equivalent(self):
        path = self._write_csv([{k: str(v) for k, v in SAMPLE_RECORD.items()}])
        records = read_csv(path)
        self.assertEqual(verdict(records[0]), EXPECTED)

    def test_blank_csv_row_is_skipped(self):
        path = self._write_csv([
            {k: str(v) for k, v in SAMPLE_RECORD.items()},
            {k: "" for k in SAMPLE_RECORD},
        ])
        records = read_csv(path)
        self.assertEqual(len(records), 1)

    def test_csv_row_with_missing_id_is_kept(self):
        row = {k: str(v) for k, v in SAMPLE_RECORD.items()}
        row["id"] = ""
        path = self._write_csv([row])
        records = read_csv(path)
        self.assertEqual(len(records), 1)  # not silently dropped
        got = evaluate_expense(normalize(records[0]))
        self.assertIsNone(got["id"])
        self.assertEqual(got["decision"], "APPROVE")  # the rest still evaluates

    def test_missing_csv_file_raises_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            read_csv("does_not_exist_12345.csv")

    def test_empty_csv_file_raises_a_clear_error(self):
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        with self.assertRaises(ValueError):
            read_csv(fh.name)

    def test_csv_missing_a_column_is_treated_as_missing_not_a_crash(self):
        row = {k: str(v) for k, v in SAMPLE_RECORD.items() if k != "claimed_amount"}
        path = self._write_csv([row], fieldnames=list(row.keys()))
        records = read_csv(path)
        got = evaluate_expense(normalize(records[0]))
        self.assertEqual(got["decision"], "INVALID")
        self.assertIn("claimed_amount", got["reason"])


class TestExcelPath(unittest.TestCase):
    """Requires pandas + openpyxl, same as the rest of the project."""

    @classmethod
    def setUpClass(cls):
        try:
            import pandas as pd  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pandas/openpyxl not installed")
        cls.tmpdir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _write_workbook(self, columns, rows, sheet="DATA", preamble_rows=0):
        import pandas as pd

        path = os.path.join(self.tmpdir, f"wb_{len(os.listdir(self.tmpdir))}.xlsx")
        df = pd.DataFrame(rows, columns=columns)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name=sheet, index=False,
                        startrow=preamble_rows)
        return path

    def test_excel_row_matches_json_equivalent(self):
        columns = list(SAMPLE_RECORD.keys())
        path = self._write_workbook(columns, [list(SAMPLE_RECORD.values())])
        records = read_workbook(path, sheet="DATA")
        self.assertEqual(len(records), 1)
        self.assertEqual(verdict(records[0]), EXPECTED)

    def test_header_row_is_found_regardless_of_preamble(self):
        columns = list(SAMPLE_RECORD.keys())
        path = self._write_workbook(columns, [list(SAMPLE_RECORD.values())],
                                    preamble_rows=3)
        records = read_workbook(path, sheet="DATA")
        self.assertEqual(verdict(records[0]), EXPECTED)

    def test_header_is_found_regardless_of_column_order(self):
        # id is not the first column here, unlike the candidate pack.
        reordered = dict(sorted(SAMPLE_RECORD.items(), key=lambda kv: kv[0]))
        path = self._write_workbook(list(reordered.keys()), [list(reordered.values())])
        records = read_workbook(path, sheet="DATA")
        self.assertEqual(verdict(records[0]), EXPECTED)

    def test_blank_row_is_skipped_but_missing_id_is_kept(self):
        import math
        columns = list(SAMPLE_RECORD.keys())
        blank = [None] * len(columns)
        no_id = list(SAMPLE_RECORD.values())
        no_id[columns.index("id")] = None
        path = self._write_workbook(columns, [list(SAMPLE_RECORD.values()),
                                              blank, no_id])
        records = read_workbook(path, sheet="DATA")
        self.assertEqual(len(records), 2)  # blank row dropped, no-id row kept
        self.assertIsNone(records[1]["id"])

    def test_unknown_sheet_raises_a_clear_error_listing_available_sheets(self):
        columns = list(SAMPLE_RECORD.keys())
        path = self._write_workbook(columns, [list(SAMPLE_RECORD.values())])
        with self.assertRaises(ValueError) as ctx:
            read_workbook(path, sheet="NOT A REAL SHEET")
        self.assertIn("DATA", str(ctx.exception))

    def test_missing_workbook_raises_a_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            read_workbook("does_not_exist_12345.xlsx")


class TestCrossFormatConsistency(unittest.TestCase):
    """The same logical record must reach the same verdict from every
    reader - this is what makes "no separate code path for the new row"
    actually true rather than just a comment."""

    def test_json_csv_and_direct_dict_agree(self):
        direct = verdict(SAMPLE_RECORD)

        json_records = read_json_arg(json.dumps(SAMPLE_RECORD))
        self.assertEqual(verdict(json_records[0]), direct)

        row = {k: str(v) for k, v in SAMPLE_RECORD.items()}
        fh = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                         newline="", encoding="utf-8")
        writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)
        fh.close()
        try:
            csv_records = read_csv(fh.name)
            self.assertEqual(verdict(csv_records[0]), direct)
        finally:
            os.unlink(fh.name)


class TestCandidatePackWorkbook(unittest.TestCase):
    """Runs the actual shipped workbook end to end, if present."""

    WORKBOOK = os.path.join(os.path.dirname(__file__),
                            "Graduate_Expense_Challenge_Candidate_Pack.xlsx")

    def setUp(self):
        if not os.path.exists(self.WORKBOOK):
            self.skipTest("candidate pack workbook not present")

    def test_five_sample_records_match_the_acceptance_criteria(self):
        expected = {
            "E01": ("APPROVE", 28.00, "P3"),
            "E02": ("PARTIAL", 444.40, "P5"),
            "E03": ("REVIEW", 0.00, "P6"),
            "E04": ("REVIEW", 0.00, "P4"),
            "E05": ("NEEDS_RECEIPT", 0.00, "P2"),
        }
        records = read_workbook(self.WORKBOOK)
        results = {r["id"]: r for r in
                  (evaluate_expense(normalize(row)) for row in records)}
        for rid, exp in expected.items():
            got = results[rid]
            self.assertEqual((got["decision"], got["reimbursable_amount"], got["policy_id"]),
                             exp, f"{rid}: {got['reason']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
