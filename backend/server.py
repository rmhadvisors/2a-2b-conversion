import os
import sys
import uuid
import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Import existing converter modules
from converter.parser import parse_gstr2a_b2b
from converter.parser_2b import parse_gstr2b_b2b
from lookup.gst_lookup import lookup_gstin_batch, load_cache, is_valid_gstin
from converter.validator import validate_records
from converter.mapper import map_records
from converter.exporter import export_to_excel

# Initialize FastAPI
app = FastAPI(title="GST 2A/2B to Tally Purchase Import Converter API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories setup
UPLOAD_DIR = os.path.abspath("uploads")
OUTPUT_DIR = os.path.abspath("output")
HISTORY_FILE = os.path.join(OUTPUT_DIR, "conversion_history.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory store for active jobs
# Structure: { job_id: { type, status, step, progress, stats_2a: {...}, stats_2b: {...}, logs: [...], errors_2a: [...], errors_2b: [...] } }
jobs_store: Dict[str, Dict[str, Any]] = {}

# Custom logging handler to capture conversion logs
class JobLogHandler(logging.Handler):
    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id
        self.formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    def emit(self, record):
        if self.job_id in jobs_store:
            log_entry = self.format(record)
            jobs_store[self.job_id]["logs"].append(log_entry)

def load_history() -> List[Dict[str, Any]]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(history: List[Dict[str, Any]]):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save history: {e}")

def create_default_stats() -> Dict[str, Any]:
    return {
        "invoices_processed": 0,
        "lookups_completed": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "valid_invoices": 0,
        "invalid_invoices": 0,
        "total_unique_gstins": 0,
        "validation_errors_count": 0,
        "lookup_failures_count": 0
    }

def convert_single_file(
    input_path: str,
    file_type: str,  # '2a' or '2b'
    job_id: str,
    settings: Dict[str, Any],
    logger: logging.Logger
) -> tuple[Dict[str, Any], List[Dict[str, Any]], str, str]:
    """
    Executes the parsing, lookup, validation, mapping, and exporting stages for a single input Excel sheet.
    Returns: (stats, errors, output_filename, error_report_filename)
    """
    stats = create_default_stats()
    
    # 1. Parse GSTR sheet (2A and 2B use different B2B column layouts)
    logger.info(f"[{file_type.upper()}] Starting parser stage for file: {os.path.basename(input_path)}...")
    if file_type == "2b":
        records, parsing_errors = parse_gstr2b_b2b(input_path)
    else:
        records, parsing_errors = parse_gstr2a_b2b(input_path)
    stats["invoices_processed"] = len(records)
    logger.info(f"[{file_type.upper()}] Parsed {len(records)} active invoice rows.")
    
    if not records and not parsing_errors:
        raise ValueError(f"No records could be extracted from GSTR-{file_type.upper()} B2B sheet.")

    # 2. Extract GSTINs and Lookup Legal Names
    unique_gstins = list(set([r["gstin"] for r in records if r["gstin"]]))
    stats["total_unique_gstins"] = len(unique_gstins)
    
    # Determine cache hits & misses
    current_cache = load_cache()
    cache_hits = 0
    cache_misses = 0
    for gstin in unique_gstins:
        if not is_valid_gstin(gstin):
            continue
        if gstin in current_cache:
            cache_hits += 1
        else:
            cache_misses += 1
            
    stats["cache_hits"] = cache_hits
    stats["cache_misses"] = cache_misses
    
    gst_names_cache = {}
    if settings.get("enableLookup", True):
        logger.info(f"[{file_type.upper()}] Resolving {len(unique_gstins)} GSTINs. Cache hits: {cache_hits}, Misses: {cache_misses}")
        gst_names_cache = lookup_gstin_batch(unique_gstins)
        stats["lookups_completed"] = len(gst_names_cache)
    else:
        logger.info(f"[{file_type.upper()}] Lookup disabled. Mapping only from local cache and record fallbacks.")
        for gstin in unique_gstins:
            if gstin in current_cache:
                gst_names_cache[gstin] = current_cache[gstin]
            else:
                # Find first GSTR record to get GSTR-2A supplier name fallback
                matching = next((r for r in records if r["gstin"] == gstin), None)
                gst_names_cache[gstin] = matching.get("supplier_name_2a", "Unknown Supplier") if matching else "Unknown Supplier"
        stats["lookups_completed"] = len(gst_names_cache)

    # 3. Validation Check
    logger.info(f"[{file_type.upper()}] Validating invoices against accounting rules...")
    valid_records, validation_errors = validate_records(records, gst_names_cache)
    
    if not settings.get("enableValidation", True):
        logger.warning(f"[{file_type.upper()}] Validation filter disabled. Mapping all rows.")
        valid_records = records
        for r in valid_records:
            if "party_name" not in r:
                r["party_name"] = gst_names_cache.get(r["gstin"], r.get("supplier_name_2a", "Unknown Supplier"))
        validation_errors = []
        
    all_errors = parsing_errors + validation_errors
    stats["valid_invoices"] = len(valid_records)
    stats["invalid_invoices"] = len(validation_errors)
    stats["validation_errors_count"] = len(all_errors)
    stats["lookup_failures_count"] = sum(
        1 for err in all_errors if "lookup failed" in err.get("error", "").lower()
    )

    # 4. Map to Tally Columns
    logger.info(f"[{file_type.upper()}] Mapping {len(valid_records)} validated transactions to Tally format...")
    mapped_rows = map_records(valid_records)

    # 5. Export to Tally Excel Template
    # We resolve the template file internally
    template_path = os.path.abspath(os.path.join("templates", "PURCHASE IMPORT MAY.xlsx"))
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Default template file templates/PURCHASE IMPORT MAY.xlsx is missing.")
        
    # Output file path generation
    base_name = settings.get("outputFileName", "Purchase_Import_Output")
    if base_name.endswith(".xlsx"):
        base_name = base_name[:-5]
    elif base_name.endswith(".xls"):
        base_name = base_name[:-4]
        
    if settings.get("job_type") == "both":
        output_filename = f"{base_name}_{file_type.upper()}.xlsx"
    else:
        output_filename = f"{base_name}.xlsx"
        
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    if os.path.exists(output_path) and not settings.get("overwriteExisting", True):
        suffix = f"_{file_type.upper()}" if settings.get("job_type") == "both" else ""
        output_filename = f"{base_name}{suffix}_{int(time.time())}.xlsx"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
    logger.info(f"[{file_type.upper()}] Generating final Tally workbook at: {output_filename}...")
    errors_to_export = all_errors if settings.get("enableErrorSheet", True) else []
    export_to_excel(mapped_rows, errors_to_export, template_path, output_path)

    # Create Error Report JSON for standalone download
    error_report_filename = f"ErrorReport_{file_type.upper()}_{job_id}.json"
    error_report_path = os.path.join(OUTPUT_DIR, error_report_filename)
    with open(error_report_path, "w", encoding="utf-8") as ef:
        json.dump(all_errors, ef, indent=4)
        
    logger.info(f"[{file_type.upper()}] Successfully compiled.")
    return stats, all_errors, output_filename, error_report_filename

def run_conversion_pipeline_multi(
    job_id: str,
    job_type: str,  # '2a' | '2b' | 'both'
    path_2a: Optional[str],
    path_2b: Optional[str],
    settings: Dict[str, Any]
):
    job = jobs_store[job_id]
    job["status"] = "running"
    start_time = time.time()
    
    # Attach our custom logging handler to root logger
    handler = JobLogHandler(job_id)
    handler.setLevel(logging.INFO)
    logger = logging.getLogger()
    logger.addHandler(handler)
    
    # Configure root level loggers
    logging.getLogger("converter").setLevel(logging.INFO)
    logging.getLogger("lookup").setLevel(logging.INFO)
    
    try:
        settings["job_type"] = job_type
        # Convert GSTR-2A if present
        if job_type in ["2a", "both"] and path_2a:
            job["step"] = "Converting GSTR-2A..."
            job["progress"] = 10 if job_type == "both" else 20
            
            stats_2a, errors_2a, out_2a, err_rep_2a = convert_single_file(
                input_path=path_2a,
                file_type="2a",
                job_id=job_id,
                settings=settings,
                logger=logger
            )
            
            job["stats_2a"] = stats_2a
            job["errors_2a"] = errors_2a
            job["output_file_2a"] = out_2a
            job["error_report_file_2a"] = err_rep_2a
            
            if job_type == "both":
                job["progress"] = 50
                
        # Convert GSTR-2B if present
        if job_type in ["2b", "both"] and path_2b:
            job["step"] = "Converting GSTR-2B..."
            job["progress"] = 60 if job_type == "both" else 20
            
            stats_2b, errors_2b, out_2b, err_rep_2b = convert_single_file(
                input_path=path_2b,
                file_type="2b",
                job_id=job_id,
                settings=settings,
                logger=logger
            )
            
            job["stats_2b"] = stats_2b
            job["errors_2b"] = errors_2b
            job["output_file_2b"] = out_2b
            job["error_report_file_2b"] = err_rep_2b
            
        # Create consolidated log file for download
        if settings.get("saveConversionLog", True):
            log_filename = f"ConversionLog_{job_id}.log"
            log_path = os.path.join(OUTPUT_DIR, log_filename)
            with open(log_path, "w", encoding="utf-8") as lf:
                lf.write("\n".join(job["logs"]))
        else:
            log_filename = None
            
        elapsed_time = round(time.time() - start_time, 2)
        job["status"] = "completed"
        job["step"] = "Completed."
        job["progress"] = 100
        job["elapsed_time"] = elapsed_time
        job["log_file"] = log_filename
        
        logger.info(f"Conversion job completed successfully in {elapsed_time}s.")
        
        # Format input file names list for history
        input_names = []
        if path_2a: input_names.append(os.path.basename(path_2a))
        if path_2b: input_names.append(os.path.basename(path_2b))
        
        # Add to history list
        history_entry = {
            "job_id": job_id,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": job_type.upper(),
            "input_file": " & ".join(input_names),
            "output_file_2a": job.get("output_file_2a"),
            "output_file_2b": job.get("output_file_2b"),
            "log_file": log_filename,
            "error_report_file_2a": job.get("error_report_file_2a"),
            "error_report_file_2b": job.get("error_report_file_2b"),
            "processing_time": f"{elapsed_time}s",
            "status": "completed",
            "stats_2a": job.get("stats_2a"),
            "stats_2b": job.get("stats_2b")
        }
        
        history = load_history()
        history.insert(0, history_entry)
        save_history(history)
        
    except Exception as e:
        elapsed_time = round(time.time() - start_time, 2)
        job["status"] = "failed"
        job["step"] = f"Failed: {str(e)}"
        job["elapsed_time"] = elapsed_time
        logger.error(f"Job conversion failed: {e}", exc_info=True)
        
        # Create log file even on failure
        if settings.get("saveConversionLog", True):
            log_filename = f"ConversionLog_{job_id}.log"
            log_path = os.path.join(OUTPUT_DIR, log_filename)
            with open(log_path, "w", encoding="utf-8") as lf:
                lf.write("\n".join(job["logs"]))
        else:
            log_filename = None
        job["log_file"] = log_filename
        
        # Add failed to history
        input_names = []
        if path_2a: input_names.append(os.path.basename(path_2a))
        if path_2b: input_names.append(os.path.basename(path_2b))
        
        history_entry = {
            "job_id": job_id,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": job_type.upper(),
            "input_file": " & ".join(input_names),
            "output_file_2a": None,
            "output_file_2b": None,
            "log_file": log_filename,
            "error_report_file_2a": None,
            "error_report_file_2b": None,
            "processing_time": f"{elapsed_time}s",
            "status": "failed",
            "stats_2a": job.get("stats_2a"),
            "stats_2b": job.get("stats_2b")
        }
        history = load_history()
        history.insert(0, history_entry)
        save_history(history)
        
    finally:
        logger.removeHandler(handler)

def init_job_state(job_id: str, job_type: str) -> Dict[str, Any]:
    return {
        "type": job_type,
        "status": "queued",
        "step": "Initializing...",
        "progress": 0,
        "logs": [],
        "errors_2a": [],
        "errors_2b": [],
        "stats_2a": create_default_stats(),
        "stats_2b": create_default_stats(),
        "output_file_2a": None,
        "output_file_2b": None,
        "error_report_file_2a": None,
        "error_report_file_2b": None,
        "log_file": None,
        "start_time": time.time(),
        "elapsed_time": 0.0
    }

# FastAPI Endpoints

@app.post("/api/convert/2a")
async def convert_2a(
    inputFile: UploadFile = File(...),
    outputFileName: str = Form("Purchase_Import_Output.xlsx"),
    enableLookup: bool = Form(True),
    enableValidation: bool = Form(True),
    enableErrorSheet: bool = Form(True),
    overwriteExisting: bool = Form(True),
    saveConversionLog: bool = Form(True)
):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(inputFile.filename)[1].lower()
    if ext not in [".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="GSTR-2A file must be .xlsx or .xls.")
        
    input_filename = f"input_2a_{job_id}{ext}"
    input_path = os.path.join(UPLOAD_DIR, input_filename)
    with open(input_path, "wb") as f:
        f.write(await inputFile.read())
        
    jobs_store[job_id] = init_job_state(job_id, "2a")
    settings = {
        "outputFileName": outputFileName,
        "enableLookup": enableLookup,
        "enableValidation": enableValidation,
        "enableErrorSheet": enableErrorSheet,
        "overwriteExisting": overwriteExisting,
        "saveConversionLog": saveConversionLog
    }
    
    thread = threading.Thread(
        target=run_conversion_pipeline_multi,
        args=(job_id, "2a", input_path, None, settings)
    )
    thread.daemon = True
    thread.start()
    
    return {"job_id": job_id, "status": "running"}

@app.post("/api/convert/2b")
async def convert_2b(
    inputFile: UploadFile = File(...),
    outputFileName: str = Form("Purchase_Import_Output.xlsx"),
    enableLookup: bool = Form(True),
    enableValidation: bool = Form(True),
    enableErrorSheet: bool = Form(True),
    overwriteExisting: bool = Form(True),
    saveConversionLog: bool = Form(True)
):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(inputFile.filename)[1].lower()
    if ext not in [".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="GSTR-2B file must be .xlsx or .xls.")
        
    input_filename = f"input_2b_{job_id}{ext}"
    input_path = os.path.join(UPLOAD_DIR, input_filename)
    with open(input_path, "wb") as f:
        f.write(await inputFile.read())
        
    jobs_store[job_id] = init_job_state(job_id, "2b")
    settings = {
        "outputFileName": outputFileName,
        "enableLookup": enableLookup,
        "enableValidation": enableValidation,
        "enableErrorSheet": enableErrorSheet,
        "overwriteExisting": overwriteExisting,
        "saveConversionLog": saveConversionLog
    }
    
    thread = threading.Thread(
        target=run_conversion_pipeline_multi,
        args=(job_id, "2b", None, input_path, settings)
    )
    thread.daemon = True
    thread.start()
    
    return {"job_id": job_id, "status": "running"}

@app.post("/api/convert/both")
async def convert_both(
    file_2a: UploadFile = File(...),
    file_2b: UploadFile = File(...),
    outputFileName: str = Form("Purchase_Import_Output.xlsx"),
    enableLookup: bool = Form(True),
    enableValidation: bool = Form(True),
    enableErrorSheet: bool = Form(True),
    overwriteExisting: bool = Form(True),
    saveConversionLog: bool = Form(True)
):
    job_id = str(uuid.uuid4())
    ext_a = os.path.splitext(file_2a.filename)[1].lower()
    ext_b = os.path.splitext(file_2b.filename)[1].lower()
    
    if ext_a not in [".xlsx", ".xls"] or ext_b not in [".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="All uploaded files must be .xlsx or .xls.")
        
    path_a = os.path.join(UPLOAD_DIR, f"input_2a_{job_id}{ext_a}")
    with open(path_a, "wb") as f:
        f.write(await file_2a.read())
        
    path_b = os.path.join(UPLOAD_DIR, f"input_2b_{job_id}{ext_b}")
    with open(path_b, "wb") as f:
        f.write(await file_2b.read())
        
    jobs_store[job_id] = init_job_state(job_id, "both")
    settings = {
        "outputFileName": outputFileName,
        "enableLookup": enableLookup,
        "enableValidation": enableValidation,
        "enableErrorSheet": enableErrorSheet,
        "overwriteExisting": overwriteExisting,
        "saveConversionLog": saveConversionLog
    }

    thread = threading.Thread(
        target=run_conversion_pipeline_multi,
        args=(job_id, "both", path_a, path_b, settings)
    )
    thread.daemon = True
    thread.start()
    
    return {"job_id": job_id, "status": "running"}

@app.get("/api/status")
async def get_status(job_id: str, last_log_index: int = 0):
    if job_id not in jobs_store:
        # Fallback check history list
        history = load_history()
        past = next((h for h in history if h["job_id"] == job_id), None)
        if past:
            return {
                "type": past["type"].lower(),
                "status": past["status"],
                "step": "Finished.",
                "progress": 100,
                "stats_2a": past.get("stats_2a", create_default_stats()),
                "stats_2b": past.get("stats_2b", create_default_stats()),
                "logs": [],
                "errors_2a": [],
                "errors_2b": [],
                "output_file_2a": past.get("output_file_2a"),
                "output_file_2b": past.get("output_file_2b"),
                "log_file": past.get("log_file"),
                "error_report_file_2a": past.get("error_report_file_2a"),
                "error_report_file_2b": past.get("error_report_file_2b"),
                "elapsed_time": float(past["processing_time"].replace("s", ""))
            }
        raise HTTPException(status_code=404, detail="Job ID not found.")
        
    job = jobs_store[job_id]
    
    # Calculate elapsed time
    elapsed = round(time.time() - job["start_time"], 1) if job["status"] == "running" else job["elapsed_time"]
    
    # Slice log lines
    current_logs = job["logs"]
    new_logs = current_logs[last_log_index:]
    
    # Estimate remaining time
    eta = "0s"
    if job["status"] == "running":
        if job["type"] == "both":
            eta = "30s" if job["progress"] < 50 else "15s"
        else:
            eta = "15s"
            
    return {
        "type": job["type"],
        "status": job["status"],
        "step": job["step"],
        "progress": job["progress"],
        "stats_2a": job["stats_2a"],
        "stats_2b": job["stats_2b"],
        "logs": new_logs,
        "logs_count": len(current_logs),
        "errors_2a": job["errors_2a"],
        "errors_2b": job["errors_2b"],
        "output_file_2a": job["output_file_2a"],
        "output_file_2b": job["output_file_2b"],
        "log_file": job["log_file"],
        "error_report_file_2a": job["error_report_file_2a"],
        "error_report_file_2b": job["error_report_file_2b"],
        "elapsed_time": elapsed,
        "eta": eta
    }

@app.get("/api/history")
async def get_history():
    return load_history()

@app.delete("/api/history/{job_id}")
async def delete_history(job_id: str):
    history = load_history()
    run = next((h for h in history if h["job_id"] == job_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="Historical run not found.")
        
    # Remove from list
    new_history = [h for h in history if h["job_id"] != job_id]
    save_history(new_history)
    
    # Delete associated files
    for file_key in ["output_file_2a", "output_file_2b", "log_file", "error_report_file_2a", "error_report_file_2b"]:
        filename = run.get(file_key)
        if filename and filename != "N/A":
            filepath = os.path.join(OUTPUT_DIR, filename)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                    
    # Remove from memory store
    if job_id in jobs_store:
        del jobs_store[job_id]
        
    return {"message": "Run and associated files deleted successfully."}

@app.get("/api/download/{job_id}/{file_type}")
async def download_file(job_id: str, file_type: str):
    # Find files associated with this job
    history = load_history()
    run = next((h for h in history if h["job_id"] == job_id), None)
    
    if not run and job_id in jobs_store:
        run = jobs_store[job_id]
        
    if not run:
        raise HTTPException(status_code=404, detail="Job not found.")
        
    filename = None
    media_type = "application/octet-stream"
    
    if file_type == "output_2a":
        filename = run.get("output_file_2a")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif file_type == "output_2b":
        filename = run.get("output_file_2b")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif file_type == "log":
        filename = run.get("log_file")
        media_type = "text/plain"
    elif file_type == "error_2a":
        filename = run.get("error_report_file_2a")
        media_type = "application/json"
    elif file_type == "error_2b":
        filename = run.get("error_report_file_2b")
        media_type = "application/json"
        
    if not filename or filename == "N/A":
        raise HTTPException(status_code=404, detail="Requested file is not available.")
        
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"File {filename} not found on server.")
        
    return FileResponse(filepath, media_type=media_type, filename=filename)

# Serve built frontend static files if they exist
from fastapi.staticfiles import StaticFiles
frontend_dist = os.path.abspath(os.path.join("frontend", "dist"))
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
