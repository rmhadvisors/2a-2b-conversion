import pandas as pd
# pyrefly: ignore [missing-import]
import numpy as np
import logging
from datetime import datetime

logger = logging.getLogger("converter.parser")

def _fmt_date(val):
    if pd.isna(val):
        return ""
    if isinstance(val, (datetime, pd.Timestamp)):
        return val.strftime("%d-%m-%Y")
    s = str(val).strip()
    for fmt in ("%d-%m-%Y", "%d-%b-%y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return s


def _num(val):
    if pd.isna(val):
        return 0.0
    s = str(val).strip().replace(",", "")
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return str(val)


def _parse_flat_b2b(df):
    """
    Parses the flat export layout: a single header row (row 1) with named
    columns such as 'GSTIN/UIN', 'Invoice No', 'Taxable Value', 'IGST', ...
    """
    records, errors = [], []
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    def col(row, *names):
        for n in names:
            if n in row.index:
                return row[n]
        return None

    for idx, row in df.iterrows():
        excel_row_num = idx + 2  # header is row 1
        if row.isna().all():
            continue
        gstin_raw = col(row, "gstin/uin", "gstin of supplier", "gstin")
        inv_raw = col(row, "invoice no", "invoice number", "invoice no.")
        gstin = str(gstin_raw).strip().upper() if pd.notna(gstin_raw) else ""
        inv_num = str(inv_raw).strip() if pd.notna(inv_raw) else ""
        if not gstin and not inv_num:
            errors.append({
                "row": excel_row_num, "invoice": "BLANK", "gstin": "BLANK",
                "party_name": "",
                "error": "GSTIN and Invoice Number are both blank but the row contains other data; row not converted",
            })
            continue
        if inv_num.lower().endswith("-total"):
            continue
        name = col(row, "supplier name", "trade/legal name of the supplier")
        rate = _num(col(row, "rate", "rate (%)"))
        rr = col(row, "reverse charge")
        pos = col(row, "place of supply")
        itype = col(row, "invoice type")
        records.append({
            "excel_row_num": excel_row_num,
            "gstin": gstin,
            "supplier_name_2a": str(name).strip() if pd.notna(name) else "",
            "invoice_num": inv_num,
            "invoice_type": str(itype).strip() if pd.notna(itype) else "",
            "invoice_date": _fmt_date(col(row, "invoice date")),
            "invoice_value": _num(col(row, "invoice value")),
            "place_of_supply": str(pos).strip() if pd.notna(pos) else "",
            "reverse_charge": str(rr).strip() if pd.notna(rr) else "N",
            "rate": int(rate) if isinstance(rate, float) else rate,
            "taxable_value": _num(col(row, "taxable value")),
            "igst": _num(col(row, "igst", "integrated tax")),
            "cgst": _num(col(row, "cgst", "central tax")),
            "sgst": _num(col(row, "sgst", "state/ut tax")),
            "cess": _num(col(row, "cess")),
        })
    logger.info(f"Finished parsing flat layout. Extracted {len(records)} rows.")
    return records, errors


def parse_gstr2a_b2b(file_path):
    """
    Parses the B2B sheet of a GSTR-2A Excel file.
    Returns a list of parsed record dictionaries and a list of structural parsing errors.
    """
    records = []
    parsing_errors = []

    logger.info(f"Loading GSTR-2A workbook from {file_path}...")
    try:
        # Load the sheet without a header to parse starting row and headers manually
        xl = pd.ExcelFile(file_path)
        if "B2B" not in xl.sheet_names:
            flat = next((s for s in xl.sheet_names if s.strip().lower() in ("b2b_data", "b2b data")), None)
            if flat:
                logger.info(f"Using flat-layout sheet '{flat}'")
                return _parse_flat_b2b(pd.read_excel(xl, sheet_name=flat, header=0))
        if "B2B" not in xl.sheet_names:
            raise ValueError(f"Sheet 'B2B' not found in GSTR-2A file. Available sheets: {xl.sheet_names}")
            
        df = pd.read_excel(xl, sheet_name="B2B", header=None)
    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        return [], [{"row": 0, "invoice": "N/A", "gstin": "N/A", "error": f"Failed to read file: {str(e)}"}]
    
    logger.info(f"Successfully loaded B2B sheet. Total raw rows: {len(df)}")
    
    # GSTR-2A headers usually occupy rows 3 to 5 (0-indexed indices 3 to 5)
    # The first data row starts at row index 6 (1-indexed row 7)
    # Let's inspect the headers to find the row indices of key columns
    # We will locate headers using specific keywords in Row 4 (index 4) and Row 5 (index 5)
    
    if len(df) < 7:
        logger.warning("B2B sheet has less than 7 rows. No data to parse.")
        return [], []

    # Map column index to names based on GSTR-2A layout
    # 0: GSTIN of supplier (Row 4)
    # 1: Trade/Legal name of the Supplier (Row 4)
    # 2: Invoice number (Row 5)
    # 3: Invoice type (Row 5)
    # 4: Invoice Date (Row 5)
    # 5: Invoice Value (Row 5)
    # 6: Place of supply (Row 4)
    # 7: Reverse Charge (Row 4)
    # 8: Rate (%) (Row 4)
    # 9: Taxable Value (Row 4)
    # 10: Integrated Tax (Row 5)
    # 11: Central Tax (Row 5)
    # 12: State/UT Tax (Row 5)
    # 13: Cess (Row 5)
    
    data_start_row = 6
    
    # Iterate through rows starting from index 6 (Excel Row 7)
    for idx, row in df.iloc[data_start_row:].iterrows():
        excel_row_num = idx + 1
        
        # 1. Skip completely empty rows
        if row.isna().all():
            continue
            
        # Get GSTIN and Invoice Number
        gstin_raw = row.get(0)
        inv_num_raw = row.get(2)
        
        # If both are empty, it's a blank row
        if pd.isna(gstin_raw) and pd.isna(inv_num_raw):
            # Not a blank row (checked above): data exists but identifiers are missing.
            # Report it instead of silently dropping it.
            if not any(isinstance(v, str) and v.strip() for v in row.tolist()):
                logger.info(f"Skipping numeric-only summary row {excel_row_num}")
                continue
            name_raw = row.get(1)
            parsing_errors.append({
                "row": excel_row_num,
                "invoice": "BLANK",
                "gstin": "BLANK",
                "party_name": str(name_raw).strip() if pd.notna(name_raw) else "",
                "error": "GSTIN and Invoice Number are both blank but the row contains other data; row not converted",
            })
            continue
            
        # Clean values
        gstin = str(gstin_raw).strip().upper() if pd.notna(gstin_raw) else ""
        inv_num = str(inv_num_raw).strip() if pd.notna(inv_num_raw) else ""
        
        # Skip GSTR-2A portal generated total summary rows
        if inv_num.lower().endswith("-total"):
            logger.debug(f"Skipping summary total row {excel_row_num} for invoice {inv_num}")
            continue
            
        # Clean company name
        trade_name = str(row.get(1)).strip() if pd.notna(row.get(1)) else ""
        
        # Clean Invoice Type
        inv_type = str(row.get(3)).strip() if pd.notna(row.get(3)) else ""
        
        # Parse Invoice Date
        inv_date_raw = row.get(4)
        inv_date_str = ""
        if pd.notna(inv_date_raw):
            if isinstance(inv_date_raw, datetime):
                inv_date_str = inv_date_raw.strftime("%d-%m-%Y")
            elif isinstance(inv_date_raw, pd.Timestamp):
                inv_date_str = inv_date_raw.strftime("%d-%m-%Y")
            else:
                # Attempt to parse string formats
                date_str = str(inv_date_raw).strip()
                for fmt in ("%d-%m-%Y", "%d-%b-%y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        inv_date_str = dt.strftime("%d-%m-%Y")
                        break
                    except ValueError:
                        continue
                if not inv_date_str:
                    inv_date_str = date_str # Keep original if parsing fails (validator will flag it)
        
        # Helper to convert to float cleanly
        def clean_float(val):
            if pd.isna(val):
                return 0.0
            val_str = str(val).strip().replace(",", "")
            if val_str == "-" or val_str == "":
                return 0.0
            try:
                return float(val_str)
            except ValueError:
                return str(val) # Keep original string to let validator fail it
                
        # Helper to convert to integer rate cleanly
        def clean_rate(val):
            if pd.isna(val):
                return 0
            val_str = str(val).strip().replace("%", "")
            if val_str == "-" or val_str == "":
                return 0
            try:
                return int(float(val_str))
            except ValueError:
                return val # Let validator handle non-numeric rate
                
        # Extract numeric columns
        inv_value = clean_float(row.get(5))
        place_of_supply = str(row.get(6)).strip() if pd.notna(row.get(6)) else ""
        reverse_charge = str(row.get(7)).strip() if pd.notna(row.get(7)) else "N"
        rate = clean_rate(row.get(8))
        taxable_value = clean_float(row.get(9))
        igst = clean_float(row.get(10))
        cgst = clean_float(row.get(11))
        sgst = clean_float(row.get(12))
        cess = clean_float(row.get(13))
        
        record = {
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
            "cess": cess
        }
        records.append(record)
        
    logger.info(f"Finished parsing. Extracted {len(records)} active rows (excluding summary total rows).")
    return records, parsing_errors

if __name__ == "__main__":
    # Test parser
    import sys
    logging.basicConfig(level=logging.INFO)
    recs, errs = parse_gstr2a_b2b("input/27AAKFH4657G1Z4_052025_R2A.xlsx")
    print("Parsed first 2 records:")
    for r in recs[:2]:
        print(r)
    print("Errors:", errs)
