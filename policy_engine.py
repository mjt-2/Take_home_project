"""
Expense policy checker - implements P1..P8 from the POLICY tab.

Output per record: id, decision, reimbursable_amount, reason, policy_id
Decisions: APPROVE, PARTIAL, REVIEW, NEEDS_RECEIPT, INVALID

Money is handled with Decimal, quantized to whole cents at every step, so
policy boundaries (like "max $40") are exact. There is no float-tolerance
fudge factor anywhere in this file - $40.01 is over the cap, full stop.

Evaluation runs in two passes over the applicable policies, both in
(priority, id) order:
  1. Every gate (P0, P1, P2, P7's "must have receipt/approval" half, P8)
     must pass before any money is calculated. This is what makes the
     currency check (P8) run even for an airfare claim that already has
     its receipt and approval - a gate that comes first alphabetically
     can no longer short-circuit past a later one.
  2. Only then does the matching category cap (P3-P7) compute the payout.
"""

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Optional

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# 1. Policy table (transcribed from the POLICY tab)
# ---------------------------------------------------------------------------
@dataclass
class Policy:
    id: str
    category: str      # "All" = applies to every record
    rule: str
    priority: int


POLICIES = [
    # P0 is not from the POLICY tab - it is an intake check that runs ahead of
    # the policy rules. The brief says an unclear field should be marked for
    # review rather than invented, and "unclear" is not the same as "absent":
    # a missing amount is INVALID under P1, but an amount that exists and
    # simply could not be read/parsed is a valid expense needing a human.
    Policy("P0", "All", "Fields that could not be read or parsed are marked "
                        "for review, not guessed.", 0),
    Policy("P1", "All", "merchant, date, category, currency and claimed_amount are "
                        "required. Amount cannot be negative.", 1),
    Policy("P2", "All", "Receipt is required when claimed_amount is $75 or more.", 2),
    Policy("P7", "Airfare", "Receipt and manager approval are required.", 2),
    Policy("P8", "All", "Only USD is automatically processed.", 2),
    Policy("P3", "Meal", "Max reimbursable total $40. Tip allowed up to 20% of "
                         "pre-tax subtotal.", 3),
    Policy("P4", "Client Meal", "Max $75; manager approval and at least 2 attendees "
                                "required.", 3),
    Policy("P5", "Hotel", "Room capped at $200/night. Tax allowed. Parking capped "
                          "at $30 total. Minibar not allowed.", 3),
    Policy("P6", "Ground Transport", "Base fare and tolls allowed. Tip up to 20% of "
                                     "base fare. Total cap $100.", 3),
]

REQUIRED_FIELDS = ("merchant", "date", "category", "currency", "claimed_amount")

# Every field derive() will try to parse as money, plus the non-money counts
# (nights, attendees) that go through the same "must be a real number" gate.
NUMERIC_FIELDS = ("subtotal_or_base", "tax", "tip", "tolls", "nights",
                   "room_charge", "parking", "minibar", "claimed_amount",
                   "attendees")
BOOL_FIELDS = ("receipt_present", "manager_approval")

# Which fields actually bear on the decision, per category. A field being
# unreadable/unparseable only matters if some rule would have consulted it -
# that is what stops a photographed solo-meal receipt (or a CSV row for one)
# from becoming REVIEW just because nobody recorded an attendee count.
# This table is engine-owned (not from the POLICY tab) because "what does
# this category's rule actually look at" is a property of the rules, and
# receipt_intake reuses it rather than keeping its own copy.
RELEVANT_FIELDS = {
    "_all": ("merchant", "date", "category", "currency", "claimed_amount",
             "receipt_present"),
    "Meal": ("subtotal_or_base", "tax", "tip"),
    "Client Meal": ("subtotal_or_base", "tax", "tip", "manager_approval", "attendees"),
    "Hotel": ("nights", "room_charge", "tax", "parking", "minibar"),
    "Ground Transport": ("subtotal_or_base", "tolls", "tip"),
    "Airfare": ("manager_approval",),
}


def relevant_for(category: Optional[str]) -> tuple:
    return RELEVANT_FIELDS["_all"] + RELEVANT_FIELDS.get(category, ())


# ---------------------------------------------------------------------------
# 2. Parsing - the only place raw input becomes Decimal/bool
# ---------------------------------------------------------------------------
TRUE_WORDS = {"true", "t", "yes", "y", "1"}
FALSE_WORDS = {"false", "f", "no", "n", "0"}


def to_decimal(v):
    """Parse a value into a money-safe Decimal, quantized to cents.

    Returns (value, ok):
      (None, True)     - blank/absent; the caller decides the default
                          (0 for a component, None for claimed_amount).
      (Decimal, True)  - a clean, finite number.
      (None, False)    - present but malformed: unparseable text, NaN,
                          +/-Infinity, or a bool where a number was
                          expected. Never silently treated as zero.
    """
    if v is None:
        return None, True
    if isinstance(v, bool):
        return None, False
    if isinstance(v, Decimal):
        d = v
    else:
        s = str(v).strip()
        if s == "":
            return None, True
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None, False
    if not d.is_finite():
        return None, False
    return d.quantize(CENTS, rounding=ROUND_HALF_UP), True


def to_bool(v):
    """Parse true/false, numeric 1/0, and common text equivalents.

    Returns (value, ok) - ok is False for anything unrecognised (e.g. "2",
    "maybe"), so the caller can flag it for review instead of guessing.
    Blank/absent defaults to False and counts as recognised, since most
    records simply don't mention approval or a receipt at all.
    """
    if v is None:
        return False, True
    if isinstance(v, bool):
        return v, True
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return False, False
        if v == 1:
            return True, True
        if v == 0:
            return False, True
        return False, False
    s = str(v).strip().lower()
    if s == "":
        return False, True
    if s in TRUE_WORDS:
        return True, True
    if s in FALSE_WORDS:
        return False, True
    try:
        f = float(s)
    except ValueError:
        return False, False
    if f == 1:
        return True, True
    if f == 0:
        return False, True
    return False, False


def num(exp, key):
    """Read an already-derived numeric field as a Decimal. Only meaningful
    after derive() has run - a blank/invalid field reads as zero here, but
    by that point either the field didn't matter or P0/P1 already stopped
    the record."""
    v = exp.get(key)
    return v if isinstance(v, Decimal) else ZERO


# ---------------------------------------------------------------------------
# 3. Derived fields - computed once, before any rule runs
# ---------------------------------------------------------------------------
def derive(raw: dict) -> dict:
    exp = dict(raw)  # never mutate the caller's row
    unclear = set(exp.get("_unclear") or [])

    for f in NUMERIC_FIELDS:
        value, ok = to_decimal(exp.get(f))
        if not ok:
            unclear.add(f)
            exp[f] = None if f == "claimed_amount" else ZERO
        elif value is None:  # blank
            exp[f] = None if f == "claimed_amount" else ZERO
        else:
            exp[f] = value

    # nights is a count, not an arbitrary amount: negative or fractional
    # nights can't feed the nightly cap, and used to either go negative or
    # (for math.inf) crash the int() conversion this replaces.
    nights = exp.get("nights")
    if nights is not None and (nights != nights.to_integral_value() or nights < 0):
        unclear.add("nights")
        exp["nights"] = ZERO

    for f in BOOL_FIELDS:
        value, ok = to_bool(exp.get(f))
        if not ok:
            unclear.add(f)
        exp[f] = value

    category = exp.get("category")
    exp["_unclear"] = sorted(f for f in unclear if f in relevant_for(category))
    return exp


# ---------------------------------------------------------------------------
# 4. Gates - rules that can stop a record dead (P0, P1, P2, P7, P8)
# ---------------------------------------------------------------------------
# Each returns (decision, reason) to halt, or None to let the record continue.
GATES: dict[str, Callable[[dict], Optional[tuple]]] = {}
CAPS: dict[str, Callable[[dict], tuple]] = {}


def gate(pid):
    def reg(fn):
        GATES[pid] = fn
        return fn
    return reg


def cap(pid):
    def reg(fn):
        CAPS[pid] = fn
        return fn
    return reg


@gate("P0")
def p0_unclear_fields(exp):
    # Only fields the category actually depends on land in _unclear, so an
    # unreadable attendee count stops a client meal but not a solo coffee.
    unclear = exp.get("_unclear") or []
    if unclear:
        return "REVIEW", ("Could not read: " + ", ".join(unclear) +
                          " - marked for review rather than assumed")
    return None


@gate("P1")
def p1_required_fields(exp):
    missing = [f for f in REQUIRED_FIELDS if exp.get(f) in (None, "")]
    if missing:
        return "INVALID", f"Missing required field(s): {', '.join(missing)}"
    if num(exp, "claimed_amount") < 0:
        return "INVALID", "claimed_amount is negative"
    return None


@gate("P2")
def p2_receipt_threshold(exp):
    # Only bites at $75+. Below that a missing receipt is fine under P2.
    if num(exp, "claimed_amount") >= 75 and not exp.get("receipt_present"):
        return "NEEDS_RECEIPT", "Receipt required for claims of $75 or more"
    return None


@gate("P7")
def p7_airfare(exp):
    # Order matters and the policy spells it out: receipt first, approval second.
    if not exp.get("receipt_present"):
        return "NEEDS_RECEIPT", "Airfare requires a receipt"
    if not exp.get("manager_approval"):
        return "REVIEW", "Airfare requires manager approval"
    return None


@gate("P8")
def p8_currency(exp):
    if (exp.get("currency") or "").upper() != "USD":
        return "REVIEW", f"Only USD is processed automatically (got {exp.get('currency')})"
    return None


# ---------------------------------------------------------------------------
# 5. Caps - category rules that compute the reimbursable amount (P3..P7)
# ---------------------------------------------------------------------------
# Each returns (allowed_amount, [notes explaining every deduction]), or
# (None, [reason]) when the category's own conditions were never met.

@cap("P7")
def p7_airfare_amount(exp):
    # P7 has no monetary cap - once receipt and approval are both present the
    # claimed amount is payable in full. This is the "APPROVE claimed amount"
    # half of the rule; the gate above is the "if not met" half.
    return num(exp, "claimed_amount"), []


@cap("P3")
def p3_meal(exp):
    sub, tax, tip = num(exp, "subtotal_or_base"), num(exp, "tax"), num(exp, "tip")
    claimed = num(exp, "claimed_amount")

    # The policy only itemizes subtotal + tax + tip. If the claim is higher
    # than that, the gap is unexplained - it is flagged for review rather
    # than assumed to be an untracked tip. See README "Unexplained amounts".
    itemized = sub + tax + tip
    if claimed > itemized:
        gap = claimed - itemized
        return None, [f"claimed {claimed:.2f} exceeds itemized subtotal+tax+tip "
                      f"({itemized:.2f}) by {gap:.2f}; flagged for review rather "
                      f"than assumed to be an untracked tip"]

    tip_cap = (sub * Decimal("0.20")).quantize(CENTS, rounding=ROUND_HALF_UP)
    tip_ok = min(tip, tip_cap)
    notes = []
    if tip > tip_ok:
        notes.append(f"tip {tip:.2f} trimmed to 20% of subtotal ({tip_cap:.2f})")

    allowed = sub + tax + tip_ok
    if allowed > Decimal("40.00"):
        notes.append(f"total {allowed:.2f} trimmed to $40 meal cap")
        allowed = Decimal("40.00")
    return allowed, notes


@cap("P4")
def p4_client_meal(exp):
    # This one is pass/fail on its conditions before any money is worked out.
    if not exp.get("manager_approval"):
        return None, ["Client meal requires manager approval"]
    if num(exp, "attendees") < 2:
        return None, ["Client meal requires at least 2 attendees"]

    claimed = num(exp, "claimed_amount")
    notes = []
    allowed = claimed
    if allowed > Decimal("75.00"):
        notes.append(f"total {allowed:.2f} trimmed to $75 client meal cap")
        allowed = Decimal("75.00")
    return allowed, notes


@cap("P5")
def p5_hotel(exp):
    nights = num(exp, "nights")
    room, tax = num(exp, "room_charge"), num(exp, "tax")
    parking, minibar = num(exp, "parking"), num(exp, "minibar")
    notes = []

    # room_charge is the total for the stay, so the cap scales with nights.
    room_cap = Decimal("200.00") * nights
    if nights == 0 and room > 0:
        notes.append("room charged but nights is 0 - cannot apply nightly cap")
        room_ok = ZERO
    else:
        room_ok = min(room, room_cap)
        if room > room_ok:
            notes.append(f"room {room:.2f} trimmed to {room_cap:.2f} "
                        f"({int(nights)} nights x $200)")

    park_ok = min(parking, Decimal("30.00"))
    if parking > park_ok:
        notes.append(f"parking {parking:.2f} trimmed to $30 cap")
    if minibar > 0:
        notes.append(f"minibar {minibar:.2f} not reimbursable")

    return room_ok + tax + park_ok, notes


@cap("P6")
def p6_ground(exp):
    base, tolls, tip = num(exp, "subtotal_or_base"), num(exp, "tolls"), num(exp, "tip")
    claimed = num(exp, "claimed_amount")

    # Same reconciliation as P3: base + tolls + tip is everything the policy
    # itemizes. A claim above that is a discrepancy, not a hidden tip.
    itemized = base + tolls + tip
    if claimed > itemized:
        gap = claimed - itemized
        return None, [f"claimed {claimed:.2f} exceeds itemized fare+tolls+tip "
                      f"({itemized:.2f}) by {gap:.2f}; flagged for review rather "
                      f"than assumed to be an untracked tip"]

    tip_cap = (base * Decimal("0.20")).quantize(CENTS, rounding=ROUND_HALF_UP)
    tip_ok = min(tip, tip_cap)
    notes = []
    if tip > tip_ok:
        notes.append(f"tip {tip:.2f} trimmed to 20% of base fare ({tip_cap:.2f})")

    allowed = base + tolls + tip_ok
    if allowed > Decimal("100.00"):
        notes.append(f"total {allowed:.2f} trimmed to $100 cap")
        allowed = Decimal("100.00")
    return allowed, notes


# ---------------------------------------------------------------------------
# 6. The engine
# ---------------------------------------------------------------------------
def applicable(exp):
    """Policies targeting this record, in evaluation order.

    Sorted by (priority, id) so the POLICY tab's stated order holds: required
    fields, then receipt/approval, then category caps. Ties inside a priority
    break by policy id, which is why E05 reports P2 rather than P7.
    """
    cat = exp.get("category")
    hits = [p for p in POLICIES if p.category == "All" or p.category == cat]
    return sorted(hits, key=lambda p: (p.priority, p.id))


def evaluate_expense(raw: dict) -> dict:
    exp = derive(raw)
    claimed = num(exp, "claimed_amount")
    policies = applicable(exp)

    result = {
        "id": exp.get("id"),
        "decision": None,
        "reimbursable_amount": 0.0,
        "reason": "",
        "policy_id": None,
    }

    # Pass 1: every gate must clear before any money is calculated. This is
    # what makes P8's currency check run even when P7 (which sorts earlier)
    # has already been satisfied - nothing downstream can rescue a record
    # that a later gate is about to reject.
    for policy in policies:
        if policy.id in GATES:
            outcome = GATES[policy.id](exp)
            if outcome:
                decision, reason = outcome
                result.update(decision=decision, reimbursable_amount=0.0,
                              reason=reason, policy_id=policy.id)
                return result

    # Pass 2: the one category cap that actually applies computes the payout.
    for policy in policies:
        if policy.id in CAPS:
            allowed, notes = CAPS[policy.id](exp)
            if allowed is None:  # the cap's own conditions were not met
                result.update(decision="REVIEW", reimbursable_amount=0.0,
                              reason=notes[0], policy_id=policy.id)
                return result

            # Never pay more than claimed, and never pay a negative amount -
            # this holds regardless of what a cap function computed above.
            allowed = max(ZERO, min(allowed, claimed))
            partial = allowed < claimed
            result.update(
                decision="PARTIAL" if partial else "APPROVE",
                reimbursable_amount=float(allowed),
                reason="; ".join(notes) if notes else "Within policy",
                policy_id=policy.id,
            )
            return result

    # No category rule matched - don't silently approve something unrecognised.
    result.update(decision="REVIEW", reimbursable_amount=0.0,
                  reason=f"No policy covers category '{exp.get('category')}'",
                  policy_id=None)
    return result


def evaluate_all(records):
    return [evaluate_expense(r) for r in records]
