import re
import logging
import pandas as pd

logger = logging.getLogger("converter.reconciliation")

GSTIN_LIKE = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")


def find_all_gstin_like_values(file_path, sheet_name="B2B"):
    """
    Independently re-scans every cell of the source sheet for GSTIN-shaped values,
    without relying on the parser's own column/header detection. This is the ground
    truth used to catch parser-level drops (e.g. a mis-detected data-start row) that
    would otherwise never surface, since a row the parser never emitted as a record
    can't be flagged by the validator either.
    """
    try:
        xl = pd.ExcelFile(file_path)
        if sheet_name not in xl.sheet_names:
            return set()
        df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
    except Exception as e:
        logger.warning(f"Reconciliation scan could not read {file_path}: {e}")
        return set()

    found = set()
    for col in range(df.shape[1]):
        for row in range(len(df)):
            val = df.iat[row, col]
            if pd.notna(val):
                s = str(val).strip().upper()
                if len(s) == 15 and GSTIN_LIKE.match(s):
                    found.add(s)
    return found


def reconcile(file_path, records, valid_records, errors, sheet_name="B2B"):
    """
    Builds a GSTIN-level reconciliation summary and returns any GSTIN found in the raw
    source file that never made it into either the valid records or the Errors sheet.

    Returns:
        summary: dict of counts for reporting.
        unaccounted_gstins: sorted list of GSTINs present in the source file but not
            accounted for anywhere downstream - should always be empty.
    """
    raw_gstins = find_all_gstin_like_values(file_path, sheet_name)
    parsed_gstins = {r["gstin"] for r in records if r.get("gstin")}
    valid_gstins = {r["gstin"] for r in valid_records if r.get("gstin")}
    error_gstins = {e["gstin"] for e in errors if e.get("gstin") and e["gstin"] != "BLANK"}

    accounted_gstins = valid_gstins | error_gstins
    never_parsed = sorted(raw_gstins - parsed_gstins)
    unaccounted = sorted(raw_gstins - accounted_gstins)

    summary = {
        "total_gstins_in_input": len(raw_gstins),
        "converted_gstins": len(valid_gstins),
        "errored_gstins": len(error_gstins - valid_gstins),
        "skipped_gstins": len(unaccounted),
        "skipped_gstin_list": unaccounted,
        "never_parsed_gstin_list": never_parsed,
    }

    if unaccounted:
        logger.error(
            f"RECONCILIATION FAILURE: {len(unaccounted)} GSTIN(s) present in the source "
            f"file are missing from both the output and the Errors sheet: {unaccounted}"
        )
    else:
        logger.info(
            f"Reconciliation OK: {summary['total_gstins_in_input']} GSTINs in input -> "
            f"{summary['converted_gstins']} converted, {summary['errored_gstins']} errored, "
            f"0 skipped."
        )

    return summary, unaccounted
