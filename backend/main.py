import os
import sys
import logging
from converter.parser import parse_gstr2a_b2b
from lookup.gst_lookup import lookup_gstin_batch
from converter.validator import validate_records
from converter.mapper import map_records
from converter.exporter import export_to_excel

# Create output directory if it doesn't exist
os.makedirs("output", exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join("output", "conversion.log"), mode="w", encoding="utf-8")
    ]
)

logger = logging.getLogger("main")

def main():
    logger.info("==========================================")
    logger.info("GST 2A to Tally Purchase Import Converter")
    logger.info("==========================================")
    
    # 1. Define paths
    input_dir = "input"
    template_path = os.path.join("templates", "PURCHASE IMPORT MAY.xlsx")
    output_path = os.path.join("output", "Purchase_Import_Output.xlsx")
    
    # Verify template exists
    if not os.path.exists(template_path):
        logger.error(f"Template file not found at: {template_path}")
        logger.error("Please ensure templates/PURCHASE IMPORT MAY.xlsx exists.")
        sys.exit(1)
        
    # 2. Locate input file in input/ directory
    if not os.path.exists(input_dir):
        logger.error(f"Input directory '{input_dir}' not found.")
        sys.exit(1)
        
    input_files = [f for f in os.listdir(input_dir) if f.endswith(".xlsx")]
    if not input_files:
        logger.error(f"No Excel files found in '{input_dir}' directory.")
        sys.exit(1)
        
    # Select GSTR-2A file
    input_file = None
    for f in input_files:
        if "R2A" in f or "2A" in f:
            input_file = os.path.join(input_dir, f)
            break
            
    if not input_file:
        input_file = os.path.join(input_dir, input_files[0])
        logger.warning(f"No explicit GSTR-2A file matching '*2A*' found. Using first file: {input_file}")
        
    logger.info(f"Target Input File: {input_file}")
    
    # 3. Parse GSTR-2A file
    logger.info("Step 1: Parsing input GSTR-2A B2B sheet...")
    records, parsing_errors = parse_gstr2a_b2b(input_file)
    
    if not records and not parsing_errors:
        logger.warning("No records or errors extracted from the input sheet. Finished.")
        sys.exit(0)
        
    # 4. Extract unique GSTINs and lookup legal names
    logger.info("Step 2: Resolving supplier names from GSTINs...")
    unique_gstins = list(set([r["gstin"] for r in records if r["gstin"]]))
    logger.info(f"Identified {len(unique_gstins)} unique GSTINs in data.")
    
    # Run lookup (checks cache first, hits GSTzen for misses)
    gst_names_cache = {}
    try:
        gst_names_cache = lookup_gstin_batch(unique_gstins)
    except Exception as e:
        logger.error(f"Critical error during GSTIN lookup: {e}")
        
    # 5. Validate records
    logger.info("Step 3: Validating records against business rules...")
    valid_records, validation_errors = validate_records(records, gst_names_cache)
    
    # Combine errors
    all_errors = parsing_errors + validation_errors
    
    # 6. Map valid records to Tally columns
    logger.info("Step 4: Mapping valid records to Tally columns...")
    mapped_rows = map_records(valid_records)
    
    # 7. Export to Excel
    logger.info("Step 5: Writing results to Excel...")
    try:
        export_to_excel(mapped_rows, all_errors, template_path, output_path)
    except Exception as e:
        logger.error(f"Failed to export to Tally import file: {e}")
        sys.exit(1)
        
    # 8. Report Statistics
    logger.info("==========================================")
    logger.info("CONVERSION STATUS SUMMARY")
    logger.info("==========================================")
    logger.info(f"Total raw records parsed: {len(records)}")
    logger.info(f"Valid records processed:  {len(valid_records)}")
    logger.info(f"Total transactions mapped: {len(mapped_rows)}")
    logger.info(f"Total errors recorded:     {len(all_errors)}")
    logger.info(f"Output File generated:    {output_path}")
    logger.info(f"Conversion Logs saved to:  output/conversion.log")
    logger.info("==========================================")

if __name__ == "__main__":
    main()
