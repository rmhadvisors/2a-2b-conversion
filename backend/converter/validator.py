import logging
from datetime import datetime
from lookup.gst_lookup import is_valid_gstin, normalize_party_name, source_names_by_gstin

logger = logging.getLogger("converter.validator")

def find_duplicate_invoices(records):
    """
    Returns a set of (gstin, invoice_num, rate) keys that appear more than once.
    A given invoice legitimately repeats once per GST rate slab, so duplicates
    are only flagged when the same (gstin, invoice_num, rate) combination recurs.
    """
    seen = {}
    duplicates = set()
    for rec in records:
        key = (rec["gstin"], rec["invoice_num"], rec.get("rate"))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            duplicates.add(key)
    return duplicates

def fallback_party_name(gstin):
    return f"Unknown Supplier - {gstin}"

def resolve_party_name(rec, source_names, gst_names_cache):
    """
    Resolves a record's party name by priority:
      1. 'Trade/Legal name of the Supplier' from the input file (row, then any row of that GSTIN)
      2/3. GSTIN cache / GSTzen result (both arrive via gst_names_cache)
      4. Fallback placeholder (caller reports a warning)
    Returns (name, source) where source is 'input', 'lookup' or 'fallback'.
    """
    gstin = (rec.get("gstin") or "").strip().upper()
    name = normalize_party_name(rec.get("supplier_name_2a")) or source_names.get(gstin, "")
    if name:
        return name, "input"
    name = normalize_party_name(gst_names_cache.get(rec.get("gstin")) or gst_names_cache.get(gstin))
    if name:
        return name, "lookup"
    return fallback_party_name(gstin), "fallback"

def validate_records(records, gst_names_cache, warnings=None):
    """
    Validates a list of parsed records.
    Returns:
        - valid_records: list of records that passed validation, with 'party_name' populated.
        - errors: list of error dicts with keys: 'row', 'invoice', 'gstin', 'error'.
    Party-name resolution never rejects a record; if no source can supply a name the
    record is kept under a fallback name and a warning dict is appended to `warnings`
    (when a list is passed).
    """
    valid_records = []
    errors = []
    source_names = source_names_by_gstin(records)
    duplicate_keys = find_duplicate_invoices(records)

    for rec in records:
        row_num = rec["excel_row_num"]
        inv_num = rec["invoice_num"]
        gstin = rec["gstin"]
        
        row_errors = []
        
        # 1. Validate GSTIN
        if not gstin:
            row_errors.append("GSTIN of supplier is blank")
        elif not is_valid_gstin(gstin):
            row_errors.append(f"Invalid GSTIN format: '{gstin}'")
            
        # 2. Validate Invoice Number
        if not inv_num:
            row_errors.append("Invoice Number is blank")
            
        # 3. Validate Invoice Date
        inv_date = rec["invoice_date"]
        if not inv_date:
            row_errors.append("Invoice Date is blank")
        else:
            try:
                datetime.strptime(inv_date, "%d-%m-%Y")
            except ValueError:
                row_errors.append(f"Invalid Invoice Date format: '{inv_date}' (expected DD-MM-YYYY)")
                
        # 4. Validate GST Rate
        rate = rec.get("rate")
        if not isinstance(rate, (int, float)):
            row_errors.append(f"GST Rate must be numeric, got: '{rate}'")
        elif int(rate) not in [0, 5, 12, 18, 28]:
            row_errors.append(f"GST Rate {rate}% is not supported by Tally template (supported: 0, 5, 12, 18, 28)")

        # 5. Validate Taxable Value and Tax Amounts are numeric
        taxable_value = rec["taxable_value"]
        if not isinstance(taxable_value, (int, float)):
            row_errors.append(f"Taxable Value must be numeric, got: '{taxable_value}'")
        elif taxable_value < 0:
            row_errors.append(f"Taxable Value is negative: {taxable_value}")
            
        cgst = rec["cgst"]
        sgst = rec["sgst"]
        igst = rec["igst"]
        cess = rec["cess"]
        
        for tax_name, tax_val in [("CGST", cgst), ("SGST", sgst), ("IGST", igst), ("Cess", cess)]:
            if not isinstance(tax_val, (int, float)):
                row_errors.append(f"{tax_name} must be numeric, got: '{tax_val}'")
                
        # 5a. Detect duplicate invoices (same GSTIN + invoice number + rate repeated)
        dup_key = (gstin, inv_num, rec.get("rate"))
        if dup_key in duplicate_keys:
            row_errors.append(f"Duplicate invoice detected for GSTIN {gstin}, Invoice {inv_num}, Rate {rate}%")

        # 5b. Validate tax calculation consistency (CGST+SGST should equal ~taxable * rate, within rounding tolerance)
        is_interstate = isinstance(igst, (int, float)) and igst > 0
        if (
            not is_interstate
            and isinstance(taxable_value, (int, float))
            and isinstance(cgst, (int, float))
            and isinstance(sgst, (int, float))
            and isinstance(rate, (int, float))
            and rate > 0
        ):
            expected_half_tax = taxable_value * (rate / 2) / 100
            if abs(cgst - expected_half_tax) > max(1.0, expected_half_tax * 0.02):
                row_errors.append(f"CGST {cgst} does not match expected {expected_half_tax:.2f} for taxable value {taxable_value} @ {rate}%")
            if abs(sgst - expected_half_tax) > max(1.0, expected_half_tax * 0.02):
                row_errors.append(f"SGST {sgst} does not match expected {expected_half_tax:.2f} for taxable value {taxable_value} @ {rate}%")

        # 6. Resolve the party name (input file first, then cache/GSTzen, then fallback).
        # An unresolved name is a warning, not a reason to drop the invoice.
        if is_valid_gstin(gstin):
            party_name, name_source = resolve_party_name(rec, source_names, gst_names_cache)
            rec["party_name"] = party_name
            rec["party_name_source"] = name_source
            if name_source == "fallback" and warnings is not None:
                warnings.append({
                    "row": row_num,
                    "invoice": inv_num if inv_num else "BLANK",
                    "gstin": gstin,
                    "error": f"Party name could not be resolved from the input file, cache or GSTzen; using fallback '{party_name}'"
                })

        # Handle validation output
        if row_errors:
            error_desc = "; ".join(row_errors)
            logger.warning(f"Row {row_num} (Invoice: {inv_num}, GSTIN: {gstin}) failed validation: {error_desc}")
            errors.append({
                "row": row_num,
                "invoice": inv_num if inv_num else "BLANK",
                "gstin": gstin if gstin else "BLANK",
                "error": error_desc
            })
        else:
            valid_records.append(rec)
            
    logger.info(f"Validation complete. Valid: {len(valid_records)}, Errors: {len(errors)}")
    return valid_records, errors

if __name__ == "__main__":
    # Test validator
    test_recs = [
        {
            "excel_row_num": 10,
            "gstin": "27AHXPG7714R1ZA",
            "invoice_num": "INV-001",
            "invoice_date": "04-05-2025",
            "taxable_value": 1000.0,
            "igst": 0.0,
            "cgst": 90.0,
            "sgst": 90.0,
            "cess": 0.0
        },
        {
            "excel_row_num": 11,
            "gstin": "INVALID",
            "invoice_num": "",
            "invoice_date": "invalid-date",
            "taxable_value": "not-numeric",
            "igst": 10.0,
            "cgst": 0.0,
            "sgst": 0.0,
            "cess": 0.0
        }
    ]
    cache = {"27AHXPG7714R1ZA": "Yash Enterprises"}
    v, e = validate_records(test_recs, cache)
    print("Valid:", v)
    print("Errors:", e)
