import csv
import io
import json
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from policy_engine import evaluate_expense
from run_expenses import normalize

CATEGORIES = ["Meal", "Client Meal", "Hotel", "Ground Transport", "Airfare"]
CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "Other"]


def _default_id() -> str:
    today = date.today().strftime("%Y%m%d")
    return f"EXP-{today}-01"


def build_record_from_form(values: dict[str, Any]) -> dict[str, Any]:
    """Turn the form values into the same raw-dict shape the CLI expects."""
    record: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if key in {"receipt_present", "manager_approval"}:
            record[key] = bool(value)
        elif key == "date":
            record[key] = str(value)[:10]
        else:
            record[key] = value
    return record


def parse_uploaded_records(file_obj, sheet_name: str | None = None) -> list[dict[str, Any]]:
    """Read uploaded CSV/JSON/XLS/XLSX files into raw input records."""
    if file_obj is None:
        return []

    name = file_obj.name.lower()

    if name.endswith(".csv"):
        text = file_obj.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        records = []
        for row in reader:
            if row and any((v or "").strip() for v in row.values()):
                records.append({k: (None if v in (None, "") else v) for k, v in row.items()})
        return records

    if name.endswith(".json"):
        text = file_obj.read().decode("utf-8-sig")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - app-level handling
            raise ValueError(f"invalid JSON file: {exc}") from exc

        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            if not all(isinstance(item, dict) for item in parsed):
                raise ValueError("JSON upload must contain objects or an array of objects")
            return parsed
        raise ValueError("JSON upload must contain an object or an array of objects")

    if name.endswith((".xlsx", ".xls")):
        excel_file = pd.ExcelFile(file_obj)
        sheets = excel_file.sheet_names
        if not sheets:
            raise ValueError("Excel file contains no sheets")
        chosen_sheet = sheet_name or sheets[0]
        if chosen_sheet not in sheets:
            raise ValueError(f"Sheet '{chosen_sheet}' was not found. Available sheets: {', '.join(sheets)}")
        df = pd.read_excel(excel_file, sheet_name=chosen_sheet)
        if df.empty:
            return []
        rows = df.where(pd.notna(df), None).to_dict("records")
        return [{str(k): v for k, v in row.items()} for row in rows]

    raise ValueError(f"Unsupported file type: {file_obj.name}")


def _validate_form_record(record: dict[str, Any]) -> str | None:
    missing = []
    for field in ("merchant", "date", "category", "currency", "claimed_amount"):
        if record.get(field) in (None, ""):
            missing.append(field)
    if missing:
        return "Missing required fields: " + ", ".join(missing)
    return None


def _display_result(result: dict[str, Any]) -> None:
    st.subheader("Result")
    col1, col2, col3 = st.columns(3)
    col1.metric("Decision", result["decision"])
    col2.metric("Reimbursable amount", f"${result['reimbursable_amount']:.2f}")
    col3.metric("Policy", result["policy_id"] or "-")
    st.write(f"Reason: {result['reason']}")


def _result_dataframe(results: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(results, columns=["id", "decision", "reimbursable_amount", "reason", "policy_id"])


def _clear_category_fields() -> None:
    for key in (
        "meal_subtotal", "meal_tax", "meal_tip",
        "client_meal_subtotal", "client_meal_tax", "client_meal_tip", "attendees",
        "hotel_nights", "hotel_room_charge", "hotel_tax", "hotel_parking", "hotel_minibar",
        "transport_subtotal", "transport_tolls", "transport_tip",
    ):
        st.session_state.pop(key, None)


def _render_single_expense_form() -> None:
    category_key = "selected_category"
    previous_category = st.session_state.get(category_key)
    selected_category = st.selectbox(
        "Category",
        options=CATEGORIES,
        index=CATEGORIES.index(previous_category) if previous_category in CATEGORIES else 0,
        key=category_key,
    )

    if previous_category != selected_category:
        _clear_category_fields()

    default_id = st.session_state.get("default_expense_id", _default_id())
    with st.form("single_expense_form"):
        st.subheader("Expense details")
        col1, col2 = st.columns(2)
        expense_id = col1.text_input("Expense ID", value=default_id, key="expense_id")
        merchant = col1.text_input("Merchant", key="merchant")
        date_value = col1.date_input("Date", value=date.today(), key="date_value")
        currency = col2.selectbox("Currency", options=CURRENCIES, index=0, key="currency")
        claimed_amount = col2.number_input("Claimed amount", step=0.01, value=0.0, key="claimed_amount")
        receipt_present = st.checkbox("Receipt present", key="receipt_present")
        manager_approval = st.checkbox("Manager approval", key="manager_approval")

        subtotal_or_base = 0.0
        tax_value = 0.0
        tip_value = 0.0
        tolls_value = 0.0
        nights_value = 0
        room_charge_value = 0.0
        parking_value = 0.0
        minibar_value = 0.0
        attendees_value = 1

        if selected_category == "Meal":
            subtotal, tax, tip = st.columns(3)
            subtotal_or_base = subtotal.number_input("Subtotal / base", min_value=0.0, step=0.01, value=0.0, key="meal_subtotal")
            tax_value = tax.number_input("Tax", min_value=0.0, step=0.01, value=0.0, key="meal_tax")
            tip_value = tip.number_input("Tip", min_value=0.0, step=0.01, value=0.0, key="meal_tip")
            attendees_value = 1
            nights_value = 0
            room_charge_value = 0.0
            parking_value = 0.0
            minibar_value = 0.0
            tolls_value = 0.0
        elif selected_category == "Client Meal":
            subtotal, tax, tip, attendees = st.columns(4)
            subtotal_or_base = subtotal.number_input("Subtotal / base", min_value=0.0, step=0.01, value=0.0, key="client_meal_subtotal")
            tax_value = tax.number_input("Tax", min_value=0.0, step=0.01, value=0.0, key="client_meal_tax")
            tip_value = tip.number_input("Tip", min_value=0.0, step=0.01, value=0.0, key="client_meal_tip")
            attendees_value = attendees.number_input("Attendees", min_value=0, step=1, value=1, key="attendees")
            nights_value = 0
            room_charge_value = 0.0
            parking_value = 0.0
            minibar_value = 0.0
            tolls_value = 0.0
        elif selected_category == "Hotel":
            nights, room_charge, tax, parking, minibar = st.columns(5)
            nights_value = nights.number_input("Nights", min_value=0, step=1, value=0, key="hotel_nights")
            room_charge_value = room_charge.number_input("Total room charge", min_value=0.0, step=0.01, value=0.0, key="hotel_room_charge")
            tax_value = tax.number_input("Tax", min_value=0.0, step=0.01, value=0.0, key="hotel_tax")
            parking_value = parking.number_input("Parking", min_value=0.0, step=0.01, value=0.0, key="hotel_parking")
            minibar_value = minibar.number_input("Minibar", min_value=0.0, step=0.01, value=0.0, key="hotel_minibar")
            subtotal_or_base = 0.0
            attendees_value = 1
            tolls_value = 0.0
        elif selected_category == "Ground Transport":
            subtotal, tolls, tip = st.columns(3)
            subtotal_or_base = subtotal.number_input("Base fare", min_value=0.0, step=0.01, value=0.0, key="transport_subtotal")
            tolls_value = tolls.number_input("Tolls", min_value=0.0, step=0.01, value=0.0, key="transport_tolls")
            tip_value = tip.number_input("Tip", min_value=0.0, step=0.01, value=0.0, key="transport_tip")
            tax_value = 0.0
            attendees_value = 1
            nights_value = 0
            room_charge_value = 0.0
            parking_value = 0.0
            minibar_value = 0.0
        else:
            subtotal_or_base = 0.0
            tax_value = 0.0
            tip_value = 0.0
            tolls_value = 0.0
            nights_value = 0
            room_charge_value = 0.0
            parking_value = 0.0
            minibar_value = 0.0
            attendees_value = 1

        submitted = st.form_submit_button("Check expense")

    if submitted:
        st.session_state["default_expense_id"] = expense_id
        record = {
            "id": expense_id,
            "merchant": merchant,
            "date": str(date_value),
            "category": selected_category,
            "currency": currency,
            "subtotal_or_base": subtotal_or_base,
            "tax": tax_value,
            "tip": tip_value,
            "tolls": tolls_value,
            "nights": nights_value,
            "room_charge": room_charge_value,
            "parking": parking_value,
            "minibar": minibar_value,
            "claimed_amount": claimed_amount,
            "receipt_present": receipt_present,
            "manager_approval": manager_approval,
            "attendees": attendees_value,
        }

        result = evaluate_expense(normalize(record))
        _display_result(result)


def _render_upload_tab() -> None:
    uploaded_file = st.file_uploader("Upload a CSV, JSON, or Excel file", type=["csv", "json", "xlsx", "xls"])
    if uploaded_file is None:
        return

    sheet_name = None
    if uploaded_file.name.lower().endswith((".xlsx", ".xls")):
        try:
            excel_file = pd.ExcelFile(uploaded_file)
            sheet_names = excel_file.sheet_names
            sheet_name = st.selectbox("Choose worksheet", options=sheet_names)
        except Exception as exc:  # pragma: no cover - app-level validation
            st.error(f"Could not read Excel file: {exc}")
            return

    try:
        records = parse_uploaded_records(uploaded_file, sheet_name=sheet_name)
    except Exception as exc:
        st.error(f"Could not read this file: {exc}")
        return

    if not records:
        st.info("No rows were found in the uploaded file.")
        return

    preview_df = pd.DataFrame(records)
    st.subheader("Preview")
    st.dataframe(preview_df, use_container_width=True)

    if st.button("Check expenses"):
        results = [evaluate_expense(normalize(record)) for record in records]
        if not results:
            st.warning("No records to evaluate.")
            return

        result_df = _result_dataframe(results)
        st.subheader("Results")
        st.dataframe(result_df, use_container_width=True)

        csv_data = result_df.to_csv(index=False).encode("utf-8")
        json_data = json.dumps(results, indent=2).encode("utf-8")
        col1, col2 = st.columns(2)
        col1.download_button("Download CSV", data=csv_data, file_name="expense_results.csv", mime="text/csv")
        col2.download_button("Download JSON", data=json_data, file_name="expense_results.json", mime="application/json")


def main() -> None:
    st.set_page_config(page_title="Expense Checker", layout="wide")
    st.title("Expense Policy Checker")
    st.caption("Simple local expense review app — no login, no database, no cloud setup.")

    tab1, tab2 = st.tabs(["Enter an expense", "Upload a file"])
    with tab1:
        _render_single_expense_form()
    with tab2:
        _render_upload_tab()


if __name__ == "__main__":
    main()
