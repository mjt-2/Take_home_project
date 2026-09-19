"""
Tests for the expense policy engine.

Uses unittest from the standard library rather than pytest - nothing to
install, runs anywhere Python runs. On an interview machine that matters.

    python -m unittest test_policy_engine -v
    python test_policy_engine.py
"""

import unittest
from policy_engine import evaluate_expense

# A neutral record. Each test overrides only the fields it cares about, so the
# thing being tested is the only thing that varies - if a test fails you know
# exactly which field caused it.
BASE = dict(
    id="T", merchant="Merchant", date="2026-09-10", category="Meal",
    currency="USD", subtotal_or_base=0.0, tax=0.0, tip=0.0, tolls=0.0,
    nights=0, room_charge=0.0, parking=0.0, minibar=0.0, claimed_amount=0.0,
    receipt_present=True, manager_approval=False, attendees=1,
)


def rec(**overrides):
    return {**BASE, **overrides}


class AssertsMixin:
    def assertVerdict(self, record, decision, amount, policy_id):
        """One assertion helper so failures report all three fields at once,
        instead of stopping at the first mismatch and hiding the others."""
        got = evaluate_expense(record)
        self.assertEqual(
            (got["decision"], got["reimbursable_amount"], got["policy_id"]),
            (decision, amount, policy_id),
            f"\n  {record['id']} reason was: {got['reason']}",
        )


# ---------------------------------------------------------------------------
# The five supplied records. These are the acceptance criteria - if any of
# these five break, the solution is wrong no matter what else passes.
#
# E03's expected decision changed from PARTIAL to REVIEW during the bug-fix
# pass below: the original solution silently treated any unexplained gap in
# a tippable category as an untracked tip, which is the "unexplained amounts"
# bug. See that test's comment and README "Unexplained amounts" for why.
# ---------------------------------------------------------------------------
class TestSampleRecords(AssertsMixin, unittest.TestCase):

    def test_E01_meal_within_all_caps(self):
        # tip 4.00 is under 20% of 22.00 (=4.40), total 28.00 under the $40 cap
        self.assertVerdict(rec(id="E01", category="Meal", subtotal_or_base=22.0,
                               tax=2.0, tip=4.0, claimed_amount=28.0),
                           "APPROVE", 28.00, "P3")

    def test_E02_hotel_parking_and_minibar_trimmed(self):
        # room 370 is under 2 x $200; parking 40 -> 30; minibar 18 -> 0
        self.assertVerdict(rec(id="E02", category="Hotel", tax=44.40, nights=2,
                               room_charge=370.0, parking=40.0, minibar=18.0,
                               claimed_amount=472.40, manager_approval=True),
                           "PARTIAL", 444.40, "P5")

    def test_E03_ground_transport_unexplained_gap_is_reviewed(self):
        # tip column reads 0.00 but claimed is $14.10 over fare+tolls. The
        # POLICY tab's own P6 example happens to use these exact numbers and
        # calls the gap a "tip", but that is an illustration of what an
        # itemized P6 claim looks like, not a stated rule for resolving a
        # mismatch - the policy never says "assume unexplained money is a
        # tip". Per the fix for the auto-tip-guessing bug, an unexplained gap
        # is now flagged for review instead of silently paid out. See
        # README "Unexplained amounts" for the full reasoning.
        self.assertVerdict(rec(id="E03", category="Ground Transport",
                               subtotal_or_base=62.0, tolls=8.50,
                               claimed_amount=84.60),
                           "REVIEW", 0.00, "P6")

    def test_E04_client_meal_missing_approval(self):
        # 3 attendees satisfies one half of P4; manager_approval FALSE fails it
        self.assertVerdict(rec(id="E04", category="Client Meal",
                               subtotal_or_base=48.0, tax=4.0, tip=8.0,
                               claimed_amount=60.0, attendees=3),
                           "REVIEW", 0.00, "P4")

    def test_E05_airfare_no_receipt(self):
        # P2 fires before P7 - both would say NEEDS_RECEIPT, P2 gets there first
        self.assertVerdict(rec(id="E05", category="Airfare",
                               subtotal_or_base=450.0, claimed_amount=450.0,
                               receipt_present=False, manager_approval=True),
                           "NEEDS_RECEIPT", 0.00, "P2")


# ---------------------------------------------------------------------------
# P1 / P2 / P8 - the gates that stop a record before any arithmetic
# ---------------------------------------------------------------------------
class TestGates(AssertsMixin, unittest.TestCase):

    def test_missing_required_field_is_invalid(self):
        self.assertVerdict(rec(id="G1", merchant=None, subtotal_or_base=20.0,
                               claimed_amount=20.0),
                           "INVALID", 0.00, "P1")

    def test_negative_amount_is_invalid(self):
        self.assertVerdict(rec(id="G2", subtotal_or_base=-5.0, claimed_amount=-5.0),
                           "INVALID", 0.00, "P1")

    def test_non_usd_goes_to_review(self):
        self.assertVerdict(rec(id="G3", currency="EUR", subtotal_or_base=20.0,
                               claimed_amount=20.0),
                           "REVIEW", 0.00, "P8")

    def test_receipt_required_at_exactly_75(self):
        # P2 says "$75 or more" - the boundary itself must trip it
        self.assertVerdict(rec(id="G4", subtotal_or_base=75.0, claimed_amount=75.0,
                               receipt_present=False),
                           "NEEDS_RECEIPT", 0.00, "P2")

    def test_no_receipt_needed_just_under_75(self):
        # 74.99 clears P2, then falls through to the meal cap
        self.assertVerdict(rec(id="G5", subtotal_or_base=74.99,
                               claimed_amount=74.99, receipt_present=False),
                           "PARTIAL", 40.00, "P3")

    def test_invalid_beats_missing_receipt(self):
        # both P1 and P2 would fire; P1 is priority 1 and must win
        self.assertVerdict(rec(id="G6", merchant=None, subtotal_or_base=500.0,
                               claimed_amount=500.0, receipt_present=False),
                           "INVALID", 0.00, "P1")


# ---------------------------------------------------------------------------
# P3..P7 - category rules
# ---------------------------------------------------------------------------
class TestCategoryRules(AssertsMixin, unittest.TestCase):

    def test_meal_tip_exactly_at_20_percent_is_allowed(self):
        # 10.00 is exactly 20% of 50.00 - must not be trimmed
        self.assertVerdict(rec(id="M1", subtotal_or_base=50.0, tip=10.0,
                               claimed_amount=60.0),
                           "PARTIAL", 40.00, "P3")  # trimmed by the $40 cap only

    def test_meal_tip_and_cap_both_apply(self):
        self.assertVerdict(rec(id="M2", subtotal_or_base=50.0, tax=5.0, tip=15.0,
                               claimed_amount=70.0),
                           "PARTIAL", 40.00, "P3")

    def test_client_meal_over_cap(self):
        self.assertVerdict(rec(id="C1", category="Client Meal",
                               subtotal_or_base=80.0, tax=5.0, claimed_amount=85.0,
                               manager_approval=True, attendees=4),
                           "PARTIAL", 75.00, "P4")

    def test_client_meal_solo_diner_rejected(self):
        # approval present but only 1 attendee - the other half of P4
        self.assertVerdict(rec(id="C2", category="Client Meal",
                               subtotal_or_base=40.0, claimed_amount=40.0,
                               manager_approval=True, attendees=1),
                           "REVIEW", 0.00, "P4")

    def test_hotel_room_over_nightly_cap(self):
        self.assertVerdict(rec(id="H1", category="Hotel", nights=1,
                               room_charge=260.0, tax=30.0, claimed_amount=290.0,
                               manager_approval=True),
                           "PARTIAL", 230.00, "P5")

    def test_hotel_exactly_at_nightly_cap(self):
        self.assertVerdict(rec(id="H2", category="Hotel", nights=3,
                               room_charge=600.0, claimed_amount=600.0,
                               manager_approval=True),
                           "APPROVE", 600.00, "P5")

    def test_ground_transport_over_total_cap(self):
        # base 95 + tolls 20 = 115, trimmed to the $100 total cap
        self.assertVerdict(rec(id="GT1", category="Ground Transport",
                               subtotal_or_base=95.0, tolls=20.0,
                               claimed_amount=115.0),
                           "PARTIAL", 100.00, "P6")

    def test_airfare_missing_approval_only(self):
        # under $75 so P2 stays quiet; P7 catches the missing approval
        self.assertVerdict(rec(id="A1", category="Airfare", subtotal_or_base=60.0,
                               claimed_amount=60.0, manager_approval=False),
                           "REVIEW", 0.00, "P7")

    def test_airfare_under_75_still_needs_receipt(self):
        # P7 requires a receipt regardless of amount, unlike P2
        self.assertVerdict(rec(id="A2", category="Airfare", subtotal_or_base=60.0,
                               claimed_amount=60.0, receipt_present=False,
                               manager_approval=True),
                           "NEEDS_RECEIPT", 0.00, "P7")

    def test_airfare_fully_compliant(self):
        self.assertVerdict(rec(id="A3", category="Airfare", subtotal_or_base=320.0,
                               claimed_amount=320.0, receipt_present=True,
                               manager_approval=True),
                           "APPROVE", 320.00, "P7")


# ---------------------------------------------------------------------------
# Behaviour a reviewer is likely to probe
# ---------------------------------------------------------------------------
class TestFailureModes(AssertsMixin, unittest.TestCase):

    def test_unknown_category_is_not_auto_approved(self):
        # the important one: no rule covers this, so it must NOT pay out
        self.assertVerdict(rec(id="F1", category="Parking",
                               subtotal_or_base=12.0, claimed_amount=12.0),
                           "REVIEW", 0.00, None)

    def test_under_claim_pays_the_lower_amount(self):
        # allowed would be 30.00 but only 25.00 was claimed
        self.assertVerdict(rec(id="F2", subtotal_or_base=30.0, claimed_amount=25.0),
                           "APPROVE", 25.00, "P3")

    def test_residual_not_treated_as_tip_for_hotels(self):
        # a hotel with an unexplained gap must not have it relabelled as a tip
        got = evaluate_expense(rec(id="F3", category="Hotel", nights=1,
                                   room_charge=100.0, claimed_amount=150.0,
                                   manager_approval=True))
        self.assertEqual(got["reimbursable_amount"], 100.00)
        self.assertEqual(got["decision"], "PARTIAL")

    def test_output_has_all_required_fields(self):
        got = evaluate_expense(rec(id="F4", subtotal_or_base=10.0,
                                   claimed_amount=10.0))
        self.assertEqual(set(got), {"id", "decision", "reimbursable_amount",
                                    "reason", "policy_id"})

    def test_every_decision_is_from_the_allowed_set(self):
        allowed = {"APPROVE", "PARTIAL", "REVIEW", "NEEDS_RECEIPT", "INVALID"}
        samples = [
            rec(id="S1", subtotal_or_base=10.0, claimed_amount=10.0),
            rec(id="S2", currency="GBP", claimed_amount=10.0),
            rec(id="S3", merchant=None, claimed_amount=10.0),
            rec(id="S4", category="Parking", claimed_amount=10.0),
        ]
        for r in samples:
            self.assertIn(evaluate_expense(r)["decision"], allowed)

    def test_engine_does_not_mutate_the_input_record(self):
        # derive() adds helper fields; they must not leak back into the caller's
        # row, or a second pass over the same data would behave differently
        original = rec(id="F5", subtotal_or_base=10.0, claimed_amount=10.0)
        snapshot = dict(original)
        evaluate_expense(original)
        self.assertEqual(original, snapshot)


# ---------------------------------------------------------------------------
# P0 - the receipt-intake gate
# ---------------------------------------------------------------------------
class TestIntakeGate(AssertsMixin, unittest.TestCase):

    def test_unclear_field_is_review_not_invalid(self):
        # the distinction the brief asks for: an amount that exists but could
        # not be read is a valid expense needing a human, not an invalid one
        self.assertVerdict(rec(id="I1", subtotal_or_base=22.0, tax=2.0,
                               tip=4.0, claimed_amount=28.0,
                               _unclear=["claimed_amount"]),
                           "REVIEW", 0.00, "P0")

    def test_absent_field_is_still_invalid(self):
        # contrast with the above - nothing was unclear, the field just is not
        # there, so P1 still owns it
        self.assertVerdict(rec(id="I2", claimed_amount=None,
                               subtotal_or_base=22.0),
                           "INVALID", 0.00, "P1")

    def test_intake_runs_before_every_policy(self):
        # P0 is priority 0, so it outranks even P1
        self.assertVerdict(rec(id="I3", merchant=None, claimed_amount=28.0,
                               _unclear=["merchant"]),
                           "REVIEW", 0.00, "P0")

    def test_empty_unclear_list_changes_nothing(self):
        # spreadsheet rows have no _unclear at all; an empty one must behave
        # identically so the two input paths stay interchangeable
        self.assertVerdict(rec(id="I4", subtotal_or_base=22.0, tax=2.0,
                               tip=4.0, claimed_amount=28.0, _unclear=[]),
                           "APPROVE", 28.00, "P3")

    def test_reason_names_the_unreadable_fields(self):
        # category must actually depend on the unclear fields, since
        # relevance filtering now applies uniformly to every input path
        got = evaluate_expense(rec(id="I5", category="Client Meal",
                                   claimed_amount=60.0,
                                   _unclear=["attendees", "manager_approval"]))
        self.assertIn("attendees", got["reason"])
        self.assertIn("manager_approval", got["reason"])


class TestReceiptRelevance(unittest.TestCase):
    """Only fields the category depends on should be able to trigger P0."""

    def test_unknown_attendees_blocks_a_client_meal(self):
        from receipt_intake import relevant_for
        self.assertIn("attendees", relevant_for("Client Meal"))

    def test_unknown_attendees_does_not_block_a_solo_meal(self):
        from receipt_intake import relevant_for
        self.assertNotIn("attendees", relevant_for("Meal"))

    def test_low_confidence_value_is_dropped_not_passed_through(self):
        from receipt_intake import Extraction, to_record
        e = Extraction(values={"category": "Meal", "claimed_amount": 28.0},
                       confidence={"category": 1.0, "claimed_amount": 0.4})
        r = to_record(e, "X1")
        self.assertIsNone(r["claimed_amount"])   # the guess is discarded
        self.assertIn("claimed_amount", r["_unclear"])

    def test_holding_a_receipt_means_receipt_present(self):
        from receipt_intake import Extraction, to_record
        r = to_record(Extraction(values={"category": "Meal"},
                                 confidence={"category": 1.0}), "X2")
        self.assertTrue(r["receipt_present"])

    def test_manual_extractor_never_invents_manager_approval_or_attendees(self):
        # Bug 7: manual transcription must not silently default fields a
        # receipt image can never show - they stay unknown, not guessed.
        from receipt_intake import manual_extractor, to_record
        extraction = manual_extractor("receipt.jpg", {"category": "Client Meal",
                                                       "claimed_amount": 60.0})
        self.assertEqual(extraction.source, "manual")
        r = to_record(extraction, "X3")
        self.assertNotIn("manager_approval", r)
        self.assertNotIn("attendees", r)


# ---------------------------------------------------------------------------
# Regression tests for the bug-fix pass
# ---------------------------------------------------------------------------
class TestGateOrdering(AssertsMixin, unittest.TestCase):
    """Bug 1: every gate must clear before a category cap can pay out."""

    def test_eur_airfare_with_receipt_and_approval_is_still_reviewed(self):
        # Previously P7 (receipt + approval, both satisfied here) paid out
        # immediately and P8's currency check never ran.
        self.assertVerdict(rec(id="B1", category="Airfare", currency="EUR",
                               subtotal_or_base=450.0, claimed_amount=450.0,
                               receipt_present=True, manager_approval=True),
                           "REVIEW", 0.00, "P8")

    def test_usd_airfare_with_receipt_and_approval_still_pays(self):
        # Same record, USD - confirms the fix didn't just break P7 outright.
        self.assertVerdict(rec(id="B2", category="Airfare", currency="USD",
                               subtotal_or_base=450.0, claimed_amount=450.0,
                               receipt_present=True, manager_approval=True),
                           "APPROVE", 450.00, "P7")


class TestMoneyBoundaries(AssertsMixin, unittest.TestCase):
    """Bug 2: caps are exact cent boundaries, not "cap + a cent of slack"."""

    def test_meal_one_cent_over_cap_is_partial_not_approved(self):
        self.assertVerdict(rec(id="B3", subtotal_or_base=40.01,
                               claimed_amount=40.01),
                           "PARTIAL", 40.00, "P3")

    def test_meal_exactly_at_cap_is_approved(self):
        self.assertVerdict(rec(id="B4", subtotal_or_base=40.00,
                               claimed_amount=40.00),
                           "APPROVE", 40.00, "P3")

    def test_client_meal_one_cent_over_cap_is_partial_not_approved(self):
        self.assertVerdict(rec(id="B5", category="Client Meal",
                               subtotal_or_base=75.01, claimed_amount=75.01,
                               manager_approval=True, attendees=2),
                           "PARTIAL", 75.00, "P4")

    def test_client_meal_exactly_at_cap_is_approved(self):
        self.assertVerdict(rec(id="B6", category="Client Meal",
                               subtotal_or_base=75.00, claimed_amount=75.00,
                               manager_approval=True, attendees=2),
                           "APPROVE", 75.00, "P4")


class TestInvalidNumericInputs(AssertsMixin, unittest.TestCase):
    """Bug 3: bad numbers are validated before arithmetic, never guessed."""

    def test_negative_nights_is_reviewed_not_negative_reimbursement(self):
        got = evaluate_expense(rec(id="B7", category="Hotel", nights=-2,
                                   room_charge=100.0, claimed_amount=100.0,
                                   manager_approval=True))
        self.assertEqual(got["decision"], "REVIEW")
        self.assertGreaterEqual(got["reimbursable_amount"], 0.0)

    def test_infinite_nights_does_not_crash_and_is_reviewed(self):
        got = evaluate_expense(rec(id="B8", category="Hotel", nights=float("inf"),
                                   room_charge=100.0, claimed_amount=100.0,
                                   manager_approval=True))
        self.assertEqual(got["decision"], "REVIEW")

    def test_fractional_nights_is_reviewed(self):
        got = evaluate_expense(rec(id="B9", category="Hotel", nights=1.5,
                                   room_charge=100.0, claimed_amount=100.0,
                                   manager_approval=True))
        self.assertEqual(got["decision"], "REVIEW")

    def test_nan_claimed_amount_is_not_approved(self):
        got = evaluate_expense(rec(id="B10", subtotal_or_base=22.0,
                                   claimed_amount=float("nan")))
        self.assertNotEqual(got["decision"], "APPROVE")
        self.assertNotEqual(got["decision"], "PARTIAL")
        self.assertEqual(got["reimbursable_amount"], 0.0)

    def test_infinite_claimed_amount_is_not_approved(self):
        got = evaluate_expense(rec(id="B11", subtotal_or_base=22.0,
                                   claimed_amount=float("inf")))
        self.assertNotIn(got["decision"], ("APPROVE", "PARTIAL"))

    def test_malformed_component_is_flagged_not_zeroed(self):
        # A garbage string in a relevant component must not silently become
        # 0 and slide through the cap arithmetic.
        got = evaluate_expense(rec(id="B12", subtotal_or_base="not-a-number",
                                   claimed_amount=20.0))
        self.assertEqual(got["decision"], "REVIEW")
        self.assertIn("subtotal_or_base", got["reason"])

    def test_malformed_irrelevant_component_does_not_block_the_record(self):
        # minibar is irrelevant to Ground Transport, so garbage there must
        # not block an otherwise-clean claim.
        self.assertVerdict(rec(id="B13", category="Ground Transport",
                               subtotal_or_base=50.0, claimed_amount=50.0,
                               minibar="garbage"),
                           "APPROVE", 50.00, "P6")


class TestBooleanParsing(AssertsMixin, unittest.TestCase):
    """Bug 4: numeric 1/0, real bools, and text equivalents are all
    recognised; anything else is flagged rather than guessed."""

    def test_numeric_one_point_zero_is_true(self):
        self.assertVerdict(rec(id="B14", category="Airfare",
                               subtotal_or_base=200.0, claimed_amount=200.0,
                               receipt_present=1.0, manager_approval=1.0),
                           "APPROVE", 200.00, "P7")

    def test_numeric_zero_is_false(self):
        got = evaluate_expense(rec(id="B15", category="Airfare",
                                   subtotal_or_base=200.0, claimed_amount=200.0,
                                   receipt_present=True, manager_approval=0))
        self.assertEqual(got["decision"], "REVIEW")

    def test_text_yes_no_are_recognised(self):
        self.assertVerdict(rec(id="B16", category="Airfare",
                               subtotal_or_base=200.0, claimed_amount=200.0,
                               receipt_present="Yes", manager_approval="No"),
                           "REVIEW", 0.00, "P7")

    def test_unrecognized_boolean_text_is_flagged_not_guessed(self):
        got = evaluate_expense(rec(id="B17", category="Airfare",
                                   subtotal_or_base=200.0, claimed_amount=200.0,
                                   receipt_present=True,
                                   manager_approval="maybe"))
        self.assertEqual(got["decision"], "REVIEW")
        self.assertEqual(got["policy_id"], "P0")
        self.assertIn("manager_approval", got["reason"])


class TestUnexplainedAmounts(AssertsMixin, unittest.TestCase):
    """Bug 6: an unexplained gap between components and claimed_amount is
    flagged, never silently folded into tip - for any record, not just E03."""

    def test_new_meal_record_with_unexplained_gap_is_reviewed(self):
        self.assertVerdict(rec(id="B18", subtotal_or_base=20.0, tax=2.0, tip=0.0,
                               claimed_amount=30.0),
                           "REVIEW", 0.00, "P3")

    def test_new_ground_transport_record_with_unexplained_gap_is_reviewed(self):
        self.assertVerdict(rec(id="B19", category="Ground Transport",
                               subtotal_or_base=40.0, tolls=5.0,
                               claimed_amount=60.0),
                           "REVIEW", 0.00, "P6")

    def test_declared_tip_is_still_honored(self):
        # the fix removes *guessed* tips, not declared ones
        self.assertVerdict(rec(id="B20", subtotal_or_base=20.0, tax=2.0, tip=3.0,
                               claimed_amount=25.0),
                           "APPROVE", 25.00, "P3")


class TestOutputInvariants(unittest.TestCase):
    """Every record, however malformed, must produce a well-formed result."""

    RECORDS = [
        rec(id="V1", category="Hotel", nights=-5, room_charge=500.0,
            claimed_amount=500.0, manager_approval=True),
        rec(id="V2", claimed_amount=float("nan")),
        rec(id="V3", category="Ground Transport", subtotal_or_base=float("inf"),
            claimed_amount=50.0),
        rec(id="V4", category="Client Meal", attendees="two",
            claimed_amount=60.0, manager_approval=True),
        rec(id="V5", subtotal_or_base=10.0, claimed_amount=5.0),
    ]

    def test_every_result_has_the_five_required_fields(self):
        for r in self.RECORDS:
            got = evaluate_expense(r)
            self.assertEqual(set(got), {"id", "decision", "reimbursable_amount",
                                        "reason", "policy_id"})

    def test_every_decision_is_allowed(self):
        allowed = {"APPROVE", "PARTIAL", "REVIEW", "NEEDS_RECEIPT", "INVALID"}
        for r in self.RECORDS:
            self.assertIn(evaluate_expense(r)["decision"], allowed)

    def test_reimbursement_is_always_finite_nonnegative_and_capped(self):
        import math
        for r in self.RECORDS:
            got = evaluate_expense(r)
            amount = got["reimbursable_amount"]
            self.assertTrue(math.isfinite(amount), f"{r['id']}: {amount}")
            self.assertGreaterEqual(amount, 0.0, r["id"])
            claimed = r.get("claimed_amount")
            if isinstance(claimed, (int, float)) and math.isfinite(claimed) and claimed >= 0:
                self.assertLessEqual(amount, claimed, r["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
