"""Demonstrates the receipt workflow: image -> extract -> normalize -> engine."""

from receipt_intake import manual_extractor, to_record
from run_expenses import normalize
from policy_engine import evaluate_expense


def process_receipt(image_path, transcribed, unreadable=(), record_id="R01"):
    """The whole workflow, in the order it runs. Four steps, no branching."""
    extraction = manual_extractor(image_path, transcribed, unreadable)  # 2
    raw = to_record(extraction, record_id)                              # 3
    return evaluate_expense(normalize(raw))                             # 4, 5


CASES = [
    ("clean meal receipt, everything legible", dict(
        merchant="Campus Bistro", date="2026-09-01", category="Meal",
        currency="USD", subtotal_or_base=22.0, tax=2.0, tip=4.0,
        claimed_amount=28.0), (), "R01"),

    ("total smudged - unclear, not missing", dict(
        merchant="Campus Bistro", date="2026-09-01", category="Meal",
        currency="USD", subtotal_or_base=22.0, tax=2.0, tip=4.0,
        claimed_amount=28.0), ("claimed_amount",), "R02"),

    ("client meal - receipt can't know approval or headcount", dict(
        merchant="Client Table", date="2026-09-02", category="Client Meal",
        currency="USD", subtotal_or_base=48.0, tax=4.0, tip=8.0,
        claimed_amount=60.0), ("manager_approval", "attendees"), "R03"),

    ("solo meal - same two unknowns, but irrelevant here", dict(
        merchant="Corner Deli", date="2026-09-04", category="Meal",
        currency="USD", subtotal_or_base=12.0, tax=1.0, tip=2.0,
        claimed_amount=15.0), ("manager_approval", "attendees"), "R04"),

    ("hotel, minibar line legible", dict(
        merchant="Harbor View", date="2026-09-07", category="Hotel",
        currency="USD", tax=44.40, nights=2, room_charge=370.0,
        parking=40.0, minibar=18.0, claimed_amount=472.40), (), "R05"),
]

print(f"{'id':<5}{'case':<48}{'decision':<15}{'reimb':>9}  policy  reason")
print("-" * 135)
for label, fields, unreadable, rid in CASES:
    r = process_receipt("receipt.jpg", fields, unreadable, rid)
    print(f"{r['id']:<5}{label:<48}{r['decision']:<15}"
          f"{r['reimbursable_amount']:>9.2f}  {str(r['policy_id'] or '-'):<7} {r['reason']}")
