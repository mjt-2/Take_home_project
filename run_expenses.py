"""
Input path + CLI for the expense checker.

Every record - whether it comes from the workbook, a JSON string on the command
line, a JSON file, or a CSV file - goes through normalize() and then the same
engine. There is deliberately no separate code path for "the new row".

    python run_expenses.py
    python run_expenses.py --out results
    python run_expenses.py --row '{"id":"E06","merchant":"Taxi Co","date":"2026-09-20","category":"Ground Transport","currency":"EUR","subtotal_or_base":40,"claimed_amount":40,"receipt_present":true}'
    python run_expenses.py --rows new_rows.csv
    python run_expenses.py --json new_rows.json

normalize() only reshapes text/date formatting so every reader looks the
same to the engine (Excel Timestamps -> ISO strings, blank cells -> None).
Turning raw values into validated Decimal/bool - and flagging anything
malformed - is policy_engine.derive()'s job, not this module's, so there is
exactly one place that decides what counts as a valid number.
"""

import argparse
import csv
import json
import os
import sys

from policy_engine import evaluate_all

DEFAULT_WORKBOOK = "Graduate_Expense_Challenge_Candidate_Pack.xlsx"

TEXT_COLS = ("id", "merchant", "date", "category", "currency")

OUTPUT_FIELDS = ("id", "decision", "reimbursable_amount", "reason", "policy_id")


# ---------------------------------------------------------------------------
# Normalisation - the single front door for every record
# ---------------------------------------------------------------------------
def normalize(row: dict) -> dict:
    """Coerce one raw record's text/date fields into a consistent shape.

    Excel hands back numpy floats, real booleans and Timestamps; CSV hands
    back strings; JSON hands back a mix. Numeric and boolean fields are left
    as-is here and validated by policy_engine.derive() when the record is
    evaluated, so there is a single definition of "valid" regardless of
    where the row came from.
    """
    out = dict(row)

    for c in TEXT_COLS:
        v = row.get(c)
        out[c] = None if v is None or str(v).strip() in ("", "nan", "None") \
            else str(v).strip()

    # Dates arrive as Timestamps from Excel and strings elsewhere; keep the
    # ISO date portion so both look the same downstream.
    if out.get("date"):
        out["date"] = str(out["date"])[:10]

    return out


# ---------------------------------------------------------------------------
# Readers - each one only has to produce raw dicts
# ---------------------------------------------------------------------------
def read_workbook(path, sheet="SAMPLE DATA"):
    import pandas as pd

    try:
        xl = pd.ExcelFile(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"workbook not found: {path}")
    except Exception as e:
        raise ValueError(f"could not open workbook '{path}': {e}") from e

    with xl:
        if sheet not in xl.sheet_names:
            raise ValueError(
                f"sheet '{sheet}' not found in '{path}'. "
                f"Available sheets: {', '.join(xl.sheet_names)}. "
                f"Pass --sheet to pick one of them."
            )

        # The tab has preamble rows above the real header and the count of
        # them is not guaranteed, so find the row that contains an "id"
        # column instead of hardcoding an offset - and check every column,
        # not just the first, so the header is found regardless of order.
        probe = pd.read_excel(xl, sheet_name=sheet, header=None)
        header_row = None
        for i in range(len(probe)):
            cells = [str(v).strip().lower() for v in probe.iloc[i]]
            if "id" in cells:
                header_row = i
                break
        if header_row is None:
            raise ValueError(
                f"no header row with an 'id' column found in sheet '{sheet}' of '{path}'"
            )

        df = pd.read_excel(xl, sheet_name=sheet, header=header_row)
        df = df.dropna(how="all")  # completely blank rows only - a missing
                                    # id is still a row and must not be
                                    # silently dropped
        return [{k: (None if pd.isna(v) else v) for k, v in r.items()}
                for r in df.to_dict("records")]


def read_csv(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except FileNotFoundError:
        raise FileNotFoundError(f"CSV file not found: {path}")
    if not rows:
        raise ValueError(f"CSV file '{path}' has no data rows")
    # Skip rows that are entirely blank; keep rows with a missing id.
    return [r for r in rows if any((v or "").strip() for v in r.values())]


def _parse_json_records(text, source):
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in {source}: {e}") from e
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        if not all(isinstance(r, dict) for r in parsed):
            raise ValueError(f"{source}: every item in a JSON array must be an object")
        return parsed
    raise ValueError(f"{source} must be a JSON object or an array of objects, "
                     f"got {type(parsed).__name__}")


def read_json_arg(text):
    return _parse_json_records(text, "--row")


def read_json_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"JSON file not found: {path}")
    return _parse_json_records(text, path)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_table(results):
    print(f"{'id':<6}{'decision':<15}{'reimb':>10}  {'policy':<8}reason")
    print("-" * 104)
    for r in results:
        print(f"{str(r['id']):<6}{r['decision']:<15}"
              f"{r['reimbursable_amount']:>10.2f}  "
              f"{str(r['policy_id'] or '-'):<8}{r['reason']}")


def write_outputs(results, prefix):
    with open(f"{prefix}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        w.writeheader()
        w.writerows(results)
    with open(f"{prefix}.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nWrote {prefix}.csv and {prefix}.json")


# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="Apply expense policy P1-P8.")
    p.add_argument("--workbook", default=DEFAULT_WORKBOOK,
                   help="path to the candidate pack xlsx")
    p.add_argument("--sheet", default="SAMPLE DATA")
    p.add_argument("--row", help="one record, or a JSON array of records")
    p.add_argument("--rows", help="a CSV file of extra records")
    p.add_argument("--json", dest="json_file",
                   help="a JSON file: one record object or an array of records")
    p.add_argument("--only-new", action="store_true",
                   help="skip the workbook and run only --row / --rows / --json")
    p.add_argument("--out", help="write <prefix>.csv and <prefix>.json")
    args = p.parse_args(argv)

    raw = []
    try:
        if not args.only_new:
            if not os.path.exists(args.workbook):
                p.error(f"workbook not found: {args.workbook}\n"
                        f"       pass --workbook <path>, or use --only-new")
            raw += read_workbook(args.workbook, args.sheet)
        if args.rows:
            raw += read_csv(args.rows)
        if args.json_file:
            raw += read_json_file(args.json_file)
        if args.row:
            raw += read_json_arg(args.row)
    except (ValueError, FileNotFoundError) as e:
        p.error(str(e))

    if not raw:
        p.error("no records to process")

    results = evaluate_all([normalize(r) for r in raw])
    print_table(results)
    if args.out:
        write_outputs(results, args.out)
    return results


if __name__ == "__main__":
    main()
