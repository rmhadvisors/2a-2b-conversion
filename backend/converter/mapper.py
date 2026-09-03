import logging
from collections import defaultdict

logger = logging.getLogger("converter.mapper")

# Row indices (0-based) of the Purchase IGST / Input IGST columns, per GST rate.
IGST_COLUMNS = {
    5: (21, 22),
    12: (23, 24),
    18: (25, 26),
    28: (27, 28),
}

def map_records(valid_records):
    """
    Maps validated GSTR-2A records to the Tally import format structure.
    Groups by GSTIN and Invoice Number, splitting multi-rate invoices into multiple rows.
    Returns a list of list-rows, where each list represents the 81 cells of Sheet2.
    """
    mapped_rows = []
    
    # Group records by (GSTIN, Invoice Number)
    invoice_groups = defaultdict(list)
    for rec in valid_records:
        key = (rec["gstin"], rec["invoice_num"])
        invoice_groups[key].append(rec)
        
    logger.info(f"Grouping complete. Found {len(invoice_groups)} unique invoices from {len(valid_records)} rows.")
    
    for (gstin, inv_num), group in invoice_groups.items():
        # Sort group by rate to keep them in consistent ascending order (e.g. 0, 5, 12, 18, 28)
        group_sorted = sorted(group, key=lambda x: x["rate"])
        num_rates = len(group_sorted)
        
        for i, rec in enumerate(group_sorted):
            # Create a row of size 81 (matching columns 0 to 80)
            row = [None] * 81
            
            # Common headers
            row[0] = rec["gstin"]                   # GST.NO.
            row[1] = rec["invoice_num"]              # VOUCHER NO
            row[2] = rec["invoice_date"]             # DATE
            row[3] = rec["party_name"]               # PARTY NAME
            row[4] = "BEING PURCHASE RECORDED"       # NARRATION
            row[5] = "Purchase New"                  # VOUCHER TYPE 
            row[6] = rec["rate"]                     # GST RATE
            
            # Initialize all value columns to 0.0 or 0
            for col_idx in range(7, 29):
                row[col_idx] = 0.0

            # Populate rate-specific columns based on the rate
            rate = rec["rate"]
            taxable = rec["taxable_value"]
            cgst = rec["cgst"]
            sgst = rec["sgst"]
            cess = rec["cess"]
            igst = rec["igst"]
            is_interstate = isinstance(igst, (int, float)) and igst > 0

            if rate == 0:
                row[7] = taxable                     # Taxfree Purchase
            elif rate == 5:
                if is_interstate:
                    p_idx, i_idx = IGST_COLUMNS[5]
                    row[p_idx] = taxable              # Purchase IGST @ 5%
                    row[i_idx] = igst                 # Input IGST @ 5%
                else:
                    row[8] = taxable                  # Purchase GST @ 5%
                    row[9] = cgst                     # Input Cgst @ 2.5%
                    row[10] = sgst                    # Input Sgst @ 2.5%
            elif rate == 12:
                if is_interstate:
                    p_idx, i_idx = IGST_COLUMNS[12]
                    row[p_idx] = taxable              # Purchase IGST @ 12%
                    row[i_idx] = igst                 # Input IGST @ 12%
                else:
                    row[11] = taxable                 # Purchase GST @ 12%
                    row[12] = cgst                    # Input Cgst @ 6%
                    row[13] = sgst                    # Input Sgst @ 6%
            elif rate == 18:
                if is_interstate:
                    p_idx, i_idx = IGST_COLUMNS[18]
                    row[p_idx] = taxable              # Purchase IGST @ 18%
                    row[i_idx] = igst                 # Input IGST @ 18%
                else:
                    row[14] = taxable                 # Purchase GST @ 18%
                    row[15] = cgst                    # Input Cgst @ 9%
                    row[16] = sgst                    # Input Sgst @ 9%
            elif rate == 28:
                if is_interstate:
                    p_idx, i_idx = IGST_COLUMNS[28]
                    row[p_idx] = taxable              # Purchase IGST @ 28%
                    row[i_idx] = igst                 # Input IGST @ 28%
                else:
                    row[17] = taxable                 # Purchase GST @  28% (Note double space!)
                    row[18] = cgst                    # Input Cgst @ 14%
                    row[19] = sgst                    # Input Sgst @ 14%
                row[20] = cess                        # CESS @28% (applies regardless of intra/interstate)
            else:
                # Fallback for unexpected rate (e.g. 3%, etc.) - put into Nearest or Taxfree as fallback.
                # In standard flow, validator checks this.
                logger.warning(f"Unexpected rate {rate}% for invoice {inv_num}. Defaulting to Taxfree Purchase.")
                row[7] = taxable

            # Columns 29 to 79 remain None (which write_to_excel writes as empty cells)
            
            # Column 80: Error / Status
            if i < num_rates - 1:
                row[80] = "Continue..."
            else:
                row[80] = f"{rec['invoice_num']}{rec['party_name']}. Done!"
                
            mapped_rows.append(row)
            
    logger.info(f"Mapping complete. Generated {len(mapped_rows)} rows for Tally import.")
    return mapped_rows
