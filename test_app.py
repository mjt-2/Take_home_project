import io
import json
import unittest

try:
    from app import build_record_from_form, parse_uploaded_records
    STREAMLIT_AVAILABLE = True
except ImportError:
    STREAMLIT_AVAILABLE = False

from policy_engine import evaluate_expense
from run_expenses import main, normalize


BASE_RECORD = {
    "id": "APP-01",
    "merchant": "Campus Bistro",
    "date": "2026-09-01",
    "category": "Meal",
    "currency": "USD",
    "subtotal_or_base": 22.0,
    "tax": 2.0,
    "tip": 4.0,
    "tolls": 0.0,
    "nights": 0,
    "room_charge": 0.0,
    "parking": 0.0,
    "minibar": 0.0,
    "claimed_amount": 28.0,
    "receipt_present": True,
    "manager_approval": False,
    "attendees": 1,
}


def _expected_result(record):
    result = evaluate_expense(normalize(record))
    return result["decision"], result["reimbursable_amount"], result["policy_id"]


@unittest.skipUnless(STREAMLIT_AVAILABLE, "streamlit is not installed")
class AppConsistencyTests(unittest.TestCase):
    def test_form_record_matches_existing_engine(self):
        form_values = {
            "id": "APP-01",
            "merchant": "Campus Bistro",
            "date": "2026-09-01",
            "category": "Meal",
            "currency": "USD",
            "claimed_amount": 28.0,
            "receipt_present": True,
            "manager_approval": False,
            "subtotal_or_base": 22.0,
            "tax": 2.0,
            "tip": 4.0,
            "tolls": 0.0,
            "nights": 0,
            "room_charge": 0.0,
            "parking": 0.0,
            "minibar": 0.0,
            "attendees": 1,
        }

        form_record = build_record_from_form(form_values)
        self.assertEqual(_expected_result(form_record), ("APPROVE", 28.0, "P3"))
        self.assertEqual(_expected_result(BASE_RECORD), ("APPROVE", 28.0, "P3"))

    def test_script_and_upload_path_agree_with_form(self):
        form_record = build_record_from_form({
            "id": "APP-01",
            "merchant": "Campus Bistro",
            "date": "2026-09-01",
            "category": "Meal",
            "currency": "USD",
            "claimed_amount": 28.0,
            "receipt_present": True,
            "manager_approval": False,
            "subtotal_or_base": 22.0,
            "tax": 2.0,
            "tip": 4.0,
            "tolls": 0.0,
            "nights": 0,
            "room_charge": 0.0,
            "parking": 0.0,
            "minibar": 0.0,
            "attendees": 1,
        })

        script_result = main(["--only-new", "--row", json.dumps(BASE_RECORD)])
        self.assertEqual(script_result[0]["decision"], "APPROVE")
        self.assertEqual(script_result[0]["reimbursable_amount"], 28.0)

        csv_bytes = (
            "id,merchant,date,category,currency,subtotal_or_base,tax,tip,tolls,nights,room_charge,parking,minibar,claimed_amount,receipt_present,manager_approval,attendees\n"
            "APP-01,Campus Bistro,2026-09-01,Meal,USD,22.0,2.0,4.0,0.0,0,0.0,0.0,0.0,28.0,true,false,1\n"
        )
        uploaded_csv = io.BytesIO(csv_bytes.encode("utf-8"))
        uploaded_csv.name = "sample.csv"
        records = parse_uploaded_records(uploaded_csv)
        self.assertEqual(_expected_result(records[0]), ("APPROVE", 28.0, "P3"))
        self.assertEqual(
            _expected_result(form_record),
            (
                script_result[0]["decision"],
                script_result[0]["reimbursable_amount"],
                script_result[0]["policy_id"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
