# Expense Policy Checker

A Python script that decides whether an expense claim should be paid.

Give it expense records (Excel, CSV, or JSON) and it tells you, for each one:
- **decision** - `APPROVE`, `PARTIAL`, `REVIEW`, `NEEDS_RECEIPT`, or `INVALID`
- **amount** - how much to reimburse
- **reason** - a plain-English explanation
- **policy** - which rule made the call

No database, no server - just a CLI script and an optional local Streamlit
app, both driven by the same policy engine.

## Streamlit app

This project also includes `app.py`, a small Streamlit browser app that wraps
the same policy engine - enter one expense in a form or upload a file and see
the decision without touching the terminal.

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app wasn't required by the brief, but it demonstrates the same engine
driving a different interface.

## Setup

Requires Python 3.10+.

```bash
pip install pandas openpyxl
```

## Run it

```bash
python run_expenses.py
```

This processes the 5 sample records and prints a table:

```
id    decision            reimb  policy  reason
--------------------------------------------------------------------------------------------------------
E01   APPROVE             28.00  P3      Within policy
E02   PARTIAL            444.40  P5      parking 40.00 trimmed to $30 cap; minibar 18.00 not reimbursable
E03   REVIEW               0.00  P6      claimed 84.60 exceeds itemized fare+tolls+tip (70.50) by 14.10; ...
E04   REVIEW               0.00  P4      Client meal requires manager approval
E05   NEEDS_RECEIPT        0.00  P2      Receipt required for claims of $75 or more
```

Save results to files instead of just printing them:
```bash
python run_expenses.py --out results     # writes results.csv and results.json
```

Run the tests:
```bash
python -m unittest discover -v
```

## Using your own data

```bash
# one record on the command line (bash / PowerShell)
python run_expenses.py --only-new --row '{"id":"E06","merchant":"Berlin Taxi","date":"2026-09-20","category":"Ground Transport","currency":"EUR","claimed_amount":45,"receipt_present":true}'
```

In `cmd.exe`, single quotes aren't a quoting character, so wrap the JSON in
double quotes instead and double up the ones inside it:

```cmd
python run_expenses.py --only-new --row "{""id"":""E06"",""merchant"":""Berlin Taxi"",""date"":""2026-09-20"",""category"":""Ground Transport"",""currency"":""EUR"",""claimed_amount"":45,""receipt_present"":true}"
```

```bash
# a JSON file
python run_expenses.py --only-new --json new_rows.json

# a CSV file
python run_expenses.py --only-new --rows new_rows.csv

# a different spreadsheet
python run_expenses.py --workbook other_pack.xlsx --sheet "SAMPLE DATA"
```

## The policy rules

| Rule | Applies to | What it checks |
|------|-----------|-----------------|
| P1 | Every record | required fields must be present; amount can't be negative |
| P2 | Every record | receipt required once the claim is $75+ |
| P3 | Meal | max $40 total; tip up to 20% of pre-tax subtotal |
| P4 | Client Meal | max $75; needs manager approval and 2+ attendees |
| P5 | Hotel | room capped at $200/night; parking capped at $30; no minibar |
| P6 | Ground Transport | fare + tolls allowed; tip up to 20% of fare; $100 total cap |
| P7 | Airfare | needs receipt and manager approval to be fully paid |
| P8 | Every record | only USD is auto-processed; other currencies go to a human |

Evaluation runs in two passes, not one top-to-bottom pass through P1-P8:

1. **Gates** - P1 (required fields), P2 (receipt threshold), P7's receipt/approval
   check, and P8 (currency) all must pass first, in that order. None of them
   look at money, and any one of them can stop the record before a cent is
   calculated.
2. **Caps** - only once every gate has cleared does the one category cap that
   applies (P3-P7) compute the payout.

So a gate later in the table can still block a record a gate earlier in the
table already let through - e.g. an airfare claim with its receipt and
approval both present is still sent to REVIEW by P8 if it was filed in a
non-USD currency.

There's also an internal **P0** check, which runs before every other gate: if
a field couldn't be read or parsed cleanly, the record goes to a human for
`REVIEW` instead of guessing.

**Why E03 is REVIEW:** E03 is a Ground Transport claim with base fare $62.00 +
tolls $8.50 = $70.50 itemized, but `claimed_amount` is $84.60 - $14.10 more
than the fields on the record add up to. Rather than assume the gap is an
untracked tip, P6 sends it to a human instead of guessing (same rule as P3's
meal reconciliation).

## Project layout

| File | What's in it |
|------|---------------|
| [`policy_engine.py`](policy_engine.py) | The rules and the decision logic. |
| [`run_expenses.py`](run_expenses.py) | Reads Excel/CSV/JSON, cleans it up, and runs the CLI. |
| [`app.py`](app.py) | Streamlit browser app - a form and file-upload front end for the same engine. |
| [`receipt_intake.py`](receipt_intake.py) | Optional: turns a manually-transcribed receipt into a record. |
| [`demo_receipt.py`](demo_receipt.py) | Runnable demo of `receipt_intake.py`. |
| `test_*.py` | Unit tests. |
| `Graduate_Expense_Challenge_Candidate_Pack.xlsx` | Source spreadsheet with the rules and sample records. |

Start with `policy_engine.py` (the rules) and `run_expenses.py` (everything else).

## Assumptions

- Negative amounts in fields other than `claimed_amount` (e.g. a negative
  tip) aren't individually flagged, though the final safety check still
  guarantees nothing gets paid out negative or over the claim.
- Extra, unrecognized columns in your input are silently ignored rather
  than flagged - only the columns the rules actually look at matter.
- `nights` must be a non-negative whole number - a value like `1.001` is
  flagged for review rather than silently rounded to `1`, since rounding
  it into a money field first would hide the fraction from the whole-number
  check.
- Any numeric field can be flagged for review rather than crash the
  program, no matter how extreme - an amount like `1e100` is treated as
  unparseable money, not a valid claim.
