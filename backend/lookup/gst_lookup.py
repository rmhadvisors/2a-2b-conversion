import re
import os
import json
import asyncio
import logging
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

async def _scrape_gstzen_batch(gstins):
    """Internal async function to scrape GSTIN names from GSTzen using Playwright."""
    results = {}
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
            # Fill GSTIN in the textarea
            await page.fill("#gstin-input", gstin_list_str)
            
            # Click the Validate button
            btn = await page.wait_for_selector("button:has-text('Validate')", timeout=15000)
            logger.info("Submitting the GSTIN validation request...")
            await btn.click()
            
            # Wait for the results table to render
            logger.info("Waiting for results table to load...")
            await page.wait_for_selector("table", timeout=30000)
            
            # Parse rows from the results table
            rows = await page.query_selector_all("table tr")
            for row in rows:
                cols = await row.query_selector_all("td")
                if not cols or len(cols) < 2:
                    continue  # Skip header row or incomplete rows
                
                gstin_val = (await cols[0].inner_text()).strip().upper()
                legal_name_val = (await cols[1].inner_text()).strip()
                valid_status = (await cols[2].inner_text()).strip().lower()
                
                if gstin_val in gstins:
                    if valid_status == "yes" and legal_name_val:
                        cleaned_name = clean_company_name(legal_name_val)
                        results[gstin_val] = cleaned_name
                        logger.info(f"Resolved: {gstin_val} -> {cleaned_name}")
                    else:
                        logger.warning(f"GSTIN {gstin_val} is reported as invalid or has empty name on GSTzen.")
        
        except Exception as e:
            logger.error(f"Error during GSTzen scraping: {e}")
        finally:
            await browser.close()
            
    return results

def lookup_gstin_batch(gstins, max_retries=2):
    """
    Look up a list of GSTINs. Check cache first, then query GSTzen for missing ones.
    Retries failed/unresolved lookups up to `max_retries` times before giving up.
    Returns a dictionary of {GSTIN: Legal Name}.
    """
    # Normalize input
    gstins = [g.strip().upper() for g in gstins if g and isinstance(g, str)]
    if not gstins:
        return {}

    # Load cache
    cache = load_cache()

    results = {}
    missing_gstins = []

    # 1. Check cache and validate format
    for gstin in gstins:
        if not is_valid_gstin(gstin):
            logger.warning(f"Skipping lookup for invalid GSTIN format: {gstin}")
            continue

        if gstin in cache:
            results[gstin] = cache[gstin]
        else:
            missing_gstins.append(gstin)

    # 2. Query missing GSTINs in batch, retrying whatever remains unresolved
    missing_gstins = list(set(missing_gstins))
    attempt = 0
    while missing_gstins and attempt <= max_retries:
        attempt += 1
        logger.info(f"Cache miss. Querying {len(missing_gstins)} GSTINs from GSTzen (attempt {attempt}/{max_retries + 1})...")

        try:
            scraped_results = asyncio.run(_scrape_gstzen_batch(missing_gstins))
        except Exception as e:
            logger.error(f"Lookup attempt {attempt} failed entirely: {e}")
            scraped_results = {}

        for gstin, name in scraped_results.items():
            results[gstin] = name
            cache[gstin] = name

        missing_gstins = [g for g in missing_gstins if g not in scraped_results]

        if missing_gstins and attempt <= max_retries:
            logger.warning(f"{len(missing_gstins)} GSTINs still unresolved, will retry: {missing_gstins}")

    if missing_gstins:
        logger.error(f"Giving up on {len(missing_gstins)} GSTINs after {attempt} attempts: {missing_gstins}")

    if cache:
        save_cache(cache)

    return results

if __name__ == "__main__":
    # Test lookup
    logging.basicConfig(level=logging.INFO)
    test_gstins = ["27AHXPG7714R1ZA", "27AAACH2702H3ZY", "INVALID1234"]
    res = lookup_gstin_batch(test_gstins)
    print("Test Results:", res)
