import streamlit as st
import pdfplumber
import pandas as pd
import re
import datetime
from io import BytesIO
import json
import os
from rapidfuzz import fuzz

# --- App Setup ---
st.set_page_config(page_title="Bank Statement Tool", layout="wide")

# --- Clean Text for Faster and Better Matching ---
def clean_text(text):
    return re.sub(r'[^a-zA-Z0-9 ]', '', text.lower()).strip()

# --- Save Mapping to JSON ---
def save_mapping(account_info, custom_map, trend_map):
    user_id = f"{account_info['name'].replace(' ', '_')}_{account_info['account_number'][-4:]}"
    file_path = f"{user_id}_mapping.json"

    data = {"custom_map": custom_map, "trend_map": trend_map}
    with open(file_path, "w") as f:
        json.dump(data, f)
    st.success(f"✅ Mappings saved for {user_id}")

# --- Load Mapping from JSON ---
def load_mapping(account_info):
    user_id = f"{account_info['name'].replace(' ', '_')}_{account_info['account_number'][-4:]}"
    file_path = f"{user_id}_mapping.json"
    
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            data = json.load(f)
        return data["custom_map"], data["trend_map"]
    return {}, {}

# --- Default Ledger Mapping Function ---
def default_mapping(narration):
    narration_lower = narration.lower()
    drawings_keywords = [
        'swiggy', 'instamart', 'zomato', 'amazon',
        'flipkart', 'groceries', 'grocery',
        'blinkit', 'zepto'
    ]
    cash_keywords = ['cash from atm', 'atm withdrawal', 'atm wdl', 'cash withdrawal']

    if 'cash' in narration_lower or 'atm' in narration_lower:
        return "Cash"
    if any(keyword in narration_lower for keyword in drawings_keywords):
        return "Drawings"
    elif any(keyword in narration_lower for keyword in cash_keywords):
        return "Cash"
    return ""

# --- Apply Custom Mapping (case-insensitive) ---
def apply_custom_mapping(narration, custom_map):
    narration_lower = narration.lower()
    for keyword, ledger in custom_map.items():
        if keyword.lower() in narration_lower:
            return ledger
    return ""

# --- Apply Trend Mapping with Pre-cleaning ---
def apply_trend_mapping(narration, trend_map):
    narration_clean = clean_text(narration)
    best_match = ""
    best_score = 0
    for keyword, ledger in trend_map.items():
        keyword_clean = clean_text(keyword)
        score = fuzz.partial_ratio(narration_clean, keyword_clean)
        if score > best_score:
            best_match = ledger
            best_score = score
    return best_match if best_score > 70 else ""

# --- Parse a Single Transaction Line ---
def parse_transaction_line(line, prev_balance):
    date_match = re.match(r'^(\d{2}-\d{2}-\d{2,4})', line)
    if not date_match:
        return None, prev_balance

    date_str = date_match.group(1)
    try:
        date_obj = datetime.datetime.strptime(date_str, "%d-%m-%y" if len(date_str.split('-')[2]) == 2 else "%d-%m-%Y")
        date = date_obj.strftime("%d-%m-%Y")
    except:
        return None, prev_balance

    rest = line[len(date_str):].strip()
    balance_match = re.search(r'(\d[\d,]*\.\d{2})(Cr|Dr)?$', rest)
    if not balance_match:
        return None, prev_balance

    balance_amt = balance_match.group(1)
    balance_type = balance_match.group(2) or "Cr"
    balance_val = float(balance_amt.replace(",", ""))
    balance_val = balance_val if balance_type == "Cr" else -balance_val

    deposit = withdrawal = 0.0
    if prev_balance is not None:
        diff = balance_val - prev_balance
        if diff > 0:
            deposit = diff
        elif diff < 0:
            withdrawal = -diff

    narration = rest[:balance_match.start()].strip()

    return {
        "Date": date,
        "Particulars": narration,
        "Deposit": round(deposit, 2),
        "Withdrawals": round(withdrawal, 2),
        "Closing Balance": f"{balance_amt}{balance_type}"
    }, balance_val

# --- Extract Transactions from PDF ---
def extract_transactions(pdf_file):
    entries = []
    prev_balance = None
    narration_buffer = ""

    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            lines = page.extract_text().split("\n")
            for line in lines:
                line = line.strip()
                if (
                    not line
                    or "account" in line.lower()
                    or "page" in line.lower()
                    or "total" in line.lower()
                    or re.match(r'^[\.\-_=]{5,}$', line)
                ):
                    continue

                if re.match(r'^\d{2}-\d{2}-\d{2,4}', line):
                    if narration_buffer and entries:
                        entries[-1]["Particulars"] += " " + narration_buffer.strip()
                        narration_buffer = ""

                    parsed, prev_balance = parse_transaction_line(line, prev_balance)
                    if parsed:
                        entries.append(parsed)
                else:
                    narration_buffer += " " + line.strip()

    if narration_buffer and entries:
        entries[-1]["Particulars"] += " " + narration_buffer.strip()

    return entries

# --- Streamlit UI ---
st.title("📄 Bank Statement Tool")
st.markdown("Upload a **Bank of Baroda PDF statement**, choose mapping, and download as Excel.")

pdf_file = st.file_uploader("📤 Upload PDF Statement", type=["pdf"])
account_info = {"name": "", "account_number": ""}

if pdf_file:
    # User manually inputs account holder's name and account number
    st.subheader("Enter Account Holder's Information")
    account_info["name"] = st.text_input("Account Holder's Name")
    account_info["account_number"] = st.text_input("Account Number")

    mapping_type = st.radio("Choose Mapping Type", ("Custom + Default Mapping", "Trend Mapping"))

    custom_map = {}
    trend_map = {}

    if mapping_type == "Custom + Default Mapping":
        st.subheader("⚙️ Custom + Default Mapping")
        enable_default_mapping = st.checkbox("Enable Default Mapping (Cash, Drawings)", value=True)
        enable_custom_mapping = st.checkbox("Enable Custom Mapping")

        if enable_custom_mapping:
            with st.expander("🔧 Enter custom keyword → ledger mappings"):
                num_rows = st.number_input("Number of custom mappings", min_value=1, max_value=20, value=2)
                for i in range(num_rows):
                    col1, col2 = st.columns(2)
                    with col1:
                        keyword = st.text_input(f"Keyword {i+1}", key=f"keyword_{i}")
                    with col2:
                        ledger = st.text_input(f"Ledger {i+1}", key=f"ledger_{i}")
                    if keyword and ledger:
                        custom_map[keyword] = ledger

    elif mapping_type == "Trend Mapping":
        st.subheader("⚙️ Trend Mapping")
        previous_excel_file = st.file_uploader("📤 Upload Previous Statement with Ledger Names (Excel)", type=["xlsx"])
        if previous_excel_file:
            df_prev = pd.read_excel(previous_excel_file)
            if "Particulars" in df_prev.columns and "Ledger Name" in df_prev.columns:
                df_prev = df_prev.drop_duplicates(subset=["Particulars"])
                trend_map = dict(zip(df_prev["Particulars"], df_prev["Ledger Name"]))
                st.success(f"Loaded {len(trend_map)} unique mappings from previous file.")

    if pdf_file and st.button("🚀 Extract & Apply Mapping"):
        with st.spinner("⏳ Processing..."):
            data = extract_transactions(pdf_file)
            if data:
                df = pd.DataFrame(data)
                df["Ledger Name"] = ""

                if mapping_type == "Custom + Default Mapping":
                    if enable_default_mapping:
                        df["Ledger Name"] = df["Particulars"].apply(default_mapping)
                    if enable_custom_mapping and custom_map:
                        mapped_ledgers = df["Particulars"].apply(lambda x: apply_custom_mapping(x, custom_map))
                        df["Ledger Name"] = df["Ledger Name"].where(df["Ledger Name"] != "", mapped_ledgers)

                elif mapping_type == "Trend Mapping" and trend_map:
                    df["Ledger Name"] = df["Particulars"].apply(lambda x: apply_trend_mapping(x, trend_map))

                st.success(f"✅ Extracted and mapped {len(df)} transactions.")
                st.dataframe(df)

                output = BytesIO()
                df.to_excel(output, index=False)
                output.seek(0)

                st.download_button(
                    "📥 Download Excel",
                    data=output,
                    file_name="bank_statement_mapped.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                save_mapping(account_info, custom_map, trend_map)
            else:
                st.warning("⚠️ No valid transactions found in this PDF.")
