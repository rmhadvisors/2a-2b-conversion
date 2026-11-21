import re
import os
import json
import asyncio
import time
import logging
import uuid
import urllib.request
import urllib.error
from playwright.async_api import async_playwright

# Setup logging for this module
logger = logging.getLogger("gst_lookup")

# Regex to validate Indian GSTIN format
# 2 digits state code, 10 alphanumeric PAN, 1 digit entity code, 1 letter/digit, 'Z', 1 letter/digit
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

CACHE_FILE = os.path.join(os.path.dirname(__file__), "gstin_cache.json")

def load_cache():
    """Load cached GSTIN mappings from local JSON file."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load GSTIN cache: {e}")
    return {}

def save_cache(cache):
    """Save GSTIN mappings to local JSON file."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save GSTIN cache: {e}")

def is_valid_gstin(gstin):
    """Check if the GSTIN matches the standard Indian GSTIN regex."""
    if not gstin or not isinstance(gstin, str):
        return False
    return bool(GSTIN_REGEX.match(gstin.strip().upper()))

def normalize_party_name(name):
    """Collapse whitespace and treat blank/NaN-like placeholders as empty."""
    if name is None:
        return ""
    cleaned = " ".join(str(name).split())
    if cleaned.lower() in ("nan", "none", "null", "-", "--", "n/a", "na", "read on lookup"):
        return ""
    return cleaned

def source_names_by_gstin(records):
    """
    Map each GSTIN to the supplier name printed in the GSTR file itself
    (the first non-empty 'Trade/Legal name of the Supplier' seen for it).
    """
    names = {}
    for rec in records:
        gstin = (rec.get("gstin") or "").strip().upper()
        name = normalize_party_name(rec.get("supplier_name_2a"))
        if gstin and name and gstin not in names:
            names[gstin] = name
    return names

def gstins_needing_lookup(records):
    """
    All valid-format GSTINs. The business name from GSTzen (or the cache) takes
    priority over the name printed in the GSTR file, so every GSTIN is looked up;
    the file name is only the fallback when the lookup fails.
    """
    needed = []
    for gstin in dict.fromkeys(r["gstin"] for r in records if r.get("gstin")):
        if is_valid_gstin(gstin):
            needed.append(gstin)
    return needed

ACRONYMS = {
    "HDFC", "ICICI", "SBI", "IDBI", "HSBC", "PNB", "IIFL", "IRN", "GST", "GSTIN",
    "ONGC", "LLP", "PVT", "LTD", "LLC", "INC", "PLC",
}

def clean_company_name(name):
    """Convert company name to Title Case while preserving uppercase for acronyms like HDFC."""
    if not name:
        return ""
    name = name.strip()
    words = name.split()
    cleaned_words = []
    for word in words:
        # Strip trailing punctuation (e.g. "Ltd.") before checking acronym list
        bare = word.strip(".,")
        if bare.upper() in ACRONYMS:
            cleaned_words.append(word.upper())
        else:
            cleaned_words.append(word.capitalize())
    return " ".join(cleaned_words)

async def _scrape_gstzen_batch(gstins, reasons=None):
    """
    Internal async function to scrape GSTIN names from GSTzen using Playwright.
    Partial results are always returned (an error mid-way does not discard names already read).
    `reasons` (optional dict) is filled with {GSTIN: failure reason} for unresolved GSTINs.
    """
    results = {}
    if reasons is None:
        reasons = {}
    if not gstins:
        return results

    gstin_list_str = ", ".join(gstins)
    logger.info(f"Launching browser to lookup {len(gstins)} GSTINs on GSTzen...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            logger.info("Navigating to GSTzen validator page...")
            await page.goto("https://my.gstzen.in/p/gstin-validator/home/free/", timeout=30000)

            await page.wait_for_selector("#gstin-input", timeout=15000)
            await page.fill("#gstin-input", gstin_list_str)

            btn = await page.wait_for_selector("button:has-text('Look up')", timeout=15000)
            logger.info("Submitting the GSTIN validation request...")
            await btn.click()

            logger.info("Waiting for results table to load...")
            await page.wait_for_selector("table td", timeout=30000)

            rows = await page.query_selector_all("table tr")
            for row in rows:
                try:
                    cols = await row.query_selector_all("td")
                    if not cols or len(cols) < 3:
                        continue  # header row or incomplete row
                    gstin_val = (await cols[0].inner_text()).strip().upper()
                    legal_name_val = (await cols[1].inner_text()).strip()
                    valid_status = (await cols[2].inner_text()).strip().lower()
                except Exception as row_err:
                    logger.warning(f"Could not read a GSTzen result row: {row_err}")
                    continue

                if gstin_val not in gstins:
                    continue
                if valid_status.endswith("valid") and not valid_status.startswith("in") and legal_name_val:
                    cleaned_name = clean_company_name(legal_name_val)
                    results[gstin_val] = cleaned_name
                    logger.info(f"Resolved: {gstin_val} -> {cleaned_name}")
                elif valid_status.endswith("valid") and not valid_status.startswith("in"):
                    reasons[gstin_val] = "GSTzen returned an empty legal name (incomplete response)"
                    logger.warning(f"GSTIN {gstin_val}: GSTzen returned an empty name.")
                else:
                    reasons[gstin_val] = f"GSTzen reports GSTIN as not valid/active (status '{valid_status}')"
                    logger.warning(f"GSTIN {gstin_val} is reported as invalid on GSTzen.")

        except Exception as e:
            logger.error(f"Error during GSTzen scraping: {type(e).__name__}: {e}")
            for g in gstins:
                if g not in results and g not in reasons:
                    reasons[g] = f"GSTzen request failed ({type(e).__name__}: {e})"
        finally:
            await browser.close()

    return results

def _load_env_file():
    """Load backend/.env into os.environ (existing environment variables win)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except Exception as e:
        logger.warning(f"Could not read .env: {e}")


def _api_lookup_one(gstin, cfg):
    """Call the fallback GST API for one GSTIN. Returns (name, reason)."""
    body = {
        "request_id": uuid.uuid4().hex,
        "consent": "Y",
        "consent_text": "I hereby declare my consent agreement for fetching my information via RPACPC API",
        "gstNumber": gstin,
        "hsnDetails": False,
        "branchDetails": False,
        "filingDetails": False,
        "filingFrequency": False,
        "liabilityPaidDetails": False,
    }
    headers = {
        "Content-Type": "application/json",
        cfg["token_header"]: cfg["token"],
        cfg["secret_header"]: cfg["secret"],
    }
    req = urllib.request.Request(cfg["url"], data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return "", f"API HTTP {e.code}"
    except Exception as e:
        return "", f"API request failed ({type(e).__name__})"

    if str(payload.get("status", "")).upper() != "SUCCESS":
        return "", f"API status '{payload.get('status')}': {payload.get('message')}"
    basic = (payload.get("data") or {}).get("basicDetails") or {}
    name = normalize_party_name(basic.get("tradeNam")) or normalize_party_name(basic.get("Legal_Name"))
    if not name:
        return "", "API returned no trade/legal name"
    return name, ""


def _api_lookup_batch(gstins, reasons):
    """Fallback lookup for GSTINs GSTzen could not resolve. Returns {GSTIN: name}."""
    _load_env_file()
    cfg = {
        "url": os.environ.get("GST_API_BASE_URL", ""),
        "token": os.environ.get("GST_API_TOKEN", ""),
        "secret": os.environ.get("GST_API_SECRET", ""),
        "token_header": os.environ.get("GST_API_TOKEN_HEADER", ""),
        "secret_header": os.environ.get("GST_API_SECRET_HEADER", ""),
    }
    if not all(cfg.values()):
        logger.warning("GST API fallback not configured (check backend/.env); skipping.")
        return {}
    results = {}
    for gstin in gstins:
        name, reason = _api_lookup_one(gstin, cfg)
        if name:
            results[gstin] = name
        else:
            reasons[gstin] = f"GSTzen failed; {reason}"
            logger.warning(f"API fallback failed for {gstin}: {reason}")
    return results

def lookup_gstin_batch(gstins, max_retries=2, failures=None, backoff_seconds=2.0):
    """
    Look up a list of GSTINs. Check cache first, then query GSTzen for missing ones.
    Unresolved GSTINs are retried up to `max_retries` more times (so 3 attempts by default)
    with exponential backoff between attempts. Only successfully resolved, non-blank names
    are cached, and cached blanks are ignored.

    `failures` (optional dict) is filled with {GSTIN: reason} for every GSTIN that could
    not be resolved, so callers can report it instead of silently dropping it.
    Returns a dictionary of {GSTIN: Legal Name}.
    """
    if failures is None:
        failures = {}
    gstins = [g.strip().upper() for g in gstins if g and isinstance(g, str)]
    if not gstins:
        return {}

    cache = load_cache()
    results = {}
    missing_gstins = []

    for gstin in dict.fromkeys(gstins):  # de-duplicate, keep order
        if not is_valid_gstin(gstin):
            logger.warning(f"Skipping lookup for invalid GSTIN format: {gstin}")
            failures[gstin] = "Invalid GSTIN format; lookup not attempted"
            continue
        cached_name = normalize_party_name(cache.get(gstin))
        if cached_name:
            results[gstin] = cached_name
        else:
            missing_gstins.append(gstin)

    attempt = 0
    reasons = {}
    new_names = False
    while missing_gstins and attempt <= max_retries:
        attempt += 1
        if attempt > 1:
            delay = backoff_seconds * (2 ** (attempt - 2))
            logger.info(f"Waiting {delay:.1f}s before retry...")
            time.sleep(delay)
        logger.info(f"Querying {len(missing_gstins)} GSTINs from GSTzen (attempt {attempt}/{max_retries + 1})...")

        reasons = {}
        try:
            scraped_results = asyncio.run(_scrape_gstzen_batch(missing_gstins, reasons))
        except Exception as e:
            logger.error(f"Lookup attempt {attempt} failed entirely: {type(e).__name__}: {e}")
            scraped_results = {}
            reasons = {g: f"Lookup attempt failed ({type(e).__name__}: {e})" for g in missing_gstins}

        for gstin, name in scraped_results.items():
            name = normalize_party_name(name)
            if not name:
                continue
            results[gstin] = name
            cache[gstin] = name
            new_names = True

        missing_gstins = [g for g in missing_gstins if g not in results]
        if missing_gstins and attempt <= max_retries:
            logger.warning(f"{len(missing_gstins)} GSTINs still unresolved after attempt {attempt}, will retry: {missing_gstins}")

    if missing_gstins:
        logger.info(f"Trying API fallback for {len(missing_gstins)} GSTINs GSTzen could not resolve...")
        api_results = _api_lookup_batch(missing_gstins, reasons)
        for gstin, name in api_results.items():
            results[gstin] = name
            cache[gstin] = name
            new_names = True
        missing_gstins = [g for g in missing_gstins if g not in results]

    for g in missing_gstins:
        failures[g] = f"{reasons.get(g, 'No name returned by GSTzen')} (after {attempt} attempts)"
    if missing_gstins:
        logger.error(f"Giving up on {len(missing_gstins)} GSTINs after {attempt} attempts: {missing_gstins}")

    if new_names:
        save_cache(cache)

    return results

if __name__ == "__main__":
    # Test lookup
    logging.basicConfig(level=logging.INFO)
    test_gstins = ["27AHXPG7714R1ZA", "27AAACH2702H3ZY", "INVALID1234"]
    res = lookup_gstin_batch(test_gstins)
    print("Test Results:", res)
