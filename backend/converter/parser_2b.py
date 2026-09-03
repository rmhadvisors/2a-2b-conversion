import pandas as pd
import logging
from datetime import datetime

logger = logging.getLogger("converter.parser_2b")

VALID_RATES = [0, 5, 12, 18, 28]

# Keywords used to locate each column by its header text (case-insensitive substring match).
# GSTR-2B exports vary in column offset (some have an extra leading flag column) and in
# whether a genuine "Rate(%)" column exists, so columns are located by header text rather
# than fixed position.
COLUMN_KEYWORDS = {
    "gstin": ["gstin of supplier"],
    "trade_name": ["trade/legal name"],
    "invoice_num": ["invoice number"],
    "invoice_type": ["invoice type"],
    "invoice_date": ["invoice date"],
    "invoice_value": ["invoice value"],
    "place_of_supply": ["place of supply"],
    "reverse_charge": ["reverse charge"],
    "taxable_value": ["taxable value"],
    "igst": ["integrated tax"],
    "cgst": ["central tax"],
    "sgst": ["state/ut tax", "state tax"],
    "cess": ["cess"],
}


def _find_header_columns(df):
    """
    GSTR-2B headers are split across two merged rows. Scan the first ~10 rows and
    combine text across them to build a column-name -> column-index map.
    Returns (column_map, data_start_row_index).
    """
    max_scan_rows = min(10, len(df))
    combined_header = {}

    for col_idx in range(df.shape[1]):
        parts = []
        for row_idx in range(max_scan_rows):
            val = df.iat[row_idx, col_idx]
            if pd.notna(val):
                parts.append(str(val).strip())
        combined_header[col_idx] = " ".join(parts).lower()

    column_map = {}
    for field, keywords in COLUMN_KEYWORDS.items():
        for col_idx, header_text in combined_header.items():
            if any(kw in header_text for kw in keywords):
                column_map[field] = col_idx
                break

    # Data starts at the first row where the "gstin" column contains a plausible GSTIN-like value.
    data_start_row = max_scan_rows
    gstin_col = column_map.get("gstin")
    if gstin_col is not None:
        for row_idx in range(max_scan_rows, len(df)):
            val = df.iat[row_idx, gstin_col]
            if pd.notna(val) and len(str(val).strip()) >= 15:
                data_start_row = row_idx
                break

    return column_map, data_start_row


def _clean_float(val):
    if pd.isna(val):
        return 0.0
    val_str = str(val).strip().replace(",", "")
    if val_str in ("", "-"):
        return 0.0
    try:
        return float(val_str)
    except ValueError:
        return str(val)


def _parse_date(raw):
    if pd.isna(raw):
        return ""
    if isinstance(raw, (datetime, pd.Timestamp)):
        return raw.strftime("%d-%m-%Y")
    date_str = str(raw).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d-%b-%y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return date_str


def _compute_rate(taxable_value, igst, cgst, sgst):
    """
    GSTR-2B exports do not reliably expose an explicit rate/cess column (position and
    presence vary by export version), so the effective rate is always derived from the
    tax amounts and snapped to the nearest supported Tally slab.
    """
    if not isinstance(taxable_value, (int, float)) or taxable_value <= 0:
        return 0
    if isinstance(igst, (int, float)) and igst > 0:
        raw_rate = (igst / taxable_value) * 100
    elif isinstance(cgst, (int, float)) and isinstance(sgst, (int, float)):
        raw_rate = ((cgst + sgst) / taxable_value) * 100
    else:
        return 0
    return min(VALID_RATES, key=lambda r: abs(r - raw_rate))


def parse_gstr2b_b2b(file_path):
    """
    Parses the B2B sheet of a GSTR-2B Excel file.
    Returns a list of parsed record dictionaries (same shape as parse_gstr2a_b2b)
    and a list of structural parsing errors.
    """
    parsing_errors = []
    records = []

    logger.info(f"Loading GSTR-2B workbook from {file_path}...")
    try:
        xl = pd.ExcelFile(file_path)
        if "B2B" not in xl.sheet_names:
            raise ValueError(f"Sheet 'B2B' not found in GSTR-2B file. Available sheets: {xl.sheet_names}")
        df = pd.read_excel(xl, sheet_name="B2B", header=None)
    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        return [], [{"row": 0, "invoice": "N/A", "gstin": "N/A", "error": f"Failed to read file: {str(e)}"}]

    if len(df) < 7:
        logger.warning("B2B sheet has too few rows. No data to parse.")
        return [], []

    column_map, data_start_row = _find_header_columns(df)

    required = ["gstin", "invoice_num", "invoice_date", "taxable_value", "cgst", "sgst"]
    missing = [f for f in required if f not in column_map]
    if missing:
        logger.error(f"Could not locate required columns in GSTR-2B header: {missing}")
        return [], [{"row": 0, "invoice": "N/A", "gstin": "N/A",
                      "error": f"Could not locate required columns in B2B sheet header: {missing}"}]

    logger.info(f"Resolved column map: {column_map}. Data starts at row index {data_start_row}.")

    for idx in range(data_start_row, len(df)):
        row = df.iloc[idx]
        excel_row_num = idx + 1

        if row.isna().all():
            continue

        def get(field, default=None):
            col = column_map.get(field)
            if col is None:
                return default
            val = row.iat[col]
            return val if pd.notna(val) else default

        gstin_raw = get("gstin")
        inv_num_raw = get("invoice_num")
        if pd.isna(gstin_raw) if gstin_raw is not None else True:
            if pd.isna(inv_num_raw) if inv_num_raw is not None else True:
                continue

        gstin = str(gstin_raw).strip() if gstin_raw is not None else ""
        inv_num = str(inv_num_raw).strip() if inv_num_raw is not None else ""

        if inv_num.lower().endswith("-total") or inv_num.lower() in ("total", "grand total", "summary"):
            continue

        trade_name = str(get("trade_name", "")).strip()
        inv_type = str(get("invoice_type", "")).strip()
        inv_date_str = _parse_date(get("invoice_date"))
        inv_value = _clean_float(get("invoice_value", 0))
        place_of_supply = str(get("place_of_supply", "")).strip()
        reverse_charge = str(get("reverse_charge", "N")).strip()
        taxable_value = _clean_float(get("taxable_value", 0))
        igst = _clean_float(get("igst", 0))
        cgst = _clean_float(get("cgst", 0))
        sgst = _clean_float(get("sgst", 0))
        cess_raw = _clean_float(get("cess", 0))

        rate = _compute_rate(taxable_value, igst, cgst, sgst)

        # The "Cess" column in several GSTR-2B export versions actually holds the
        # effective rate (a portal export quirk), not a genuine cess amount. If the
        # raw value is suspiciously close to the computed rate, treat real cess as 0.
        if isinstance(cess_raw, (int, float)) and abs(cess_raw - rate) < 1:
            cess = 0.0
        else:
            cess = cess_raw

        records.append({
            "excel_row_num": excel_row_num,
            "gstin": gstin,
            "supplier_name_2a": trade_name,
            "invoice_num": inv_num,
            "invoice_type": inv_type,
            "invoice_date": inv_date_str,
            "invoice_value": inv_value,
            "place_of_supply": place_of_supply,
            "reverse_charge": reverse_charge,
            "rate": rate,
            "taxable_value": taxable_value,
            "igst": igst,
            "cgst": cgst,
            "sgst": sgst,
            "cess": cess,
        })

    logger.info(f"Finished parsing. Extracted {len(records)} active rows from GSTR-2B.")
    return records, parsing_errors


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    recs, errs = parse_gstr2b_b2b("PURCHASE ANALYZER/2B/MAHAVIR DECOR/052026_27ACGPK4033P1ZB_GSTR2B_15062026.xlsx")
    print(f"Parsed {len(recs)} records")
    for r in recs[:3]:
        print(r)
    print("Errors:", errs)
