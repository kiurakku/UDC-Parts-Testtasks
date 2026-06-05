"""
genuinefactoryparts.com ARI PartStream scraper
Target: MTD Merged Data Staging > Troy-Bilt > 11-Push Walk-Behind Mowers > 2024 & 2025 Models
Output: parts.csv  (upsert by unique_key = path + oem)
"""

import csv
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── config ──────────────────────────────────────────────────────────────────
ARIK = "555qPs25Mt463866f2mt"
ARIB = "MTF2_STAGING"
BASE = "https://partstreamstg.arinet.com"
CSV_FILE = Path(__file__).parent / "parts.csv"
LOG_FILE = Path(__file__).parent / "scraper.log"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.genuinefactoryparts.com/",
    "APPKEY": ARIK,
}

# years to scrape (folders at depth-3)
TARGET_YEARS = {"2024 Models", "2025 Models"}

CSV_FIELDS = ["unique_key", "path", "ref", "oem", "description", "updated_at"]

DELAY = 0.3  # seconds between requests

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)


def get_assembly(arib: str, aria: str | None = None) -> list[dict]:
    """Return list of catalog tree nodes for given brand + optional node id."""
    params = {"arik": ARIK, "arib": arib, "responsive": "true", "cb": "cb"}
    if aria:
        params["aria"] = aria
    r = session.get(BASE + "/Parts/GetAssembly", params=params, timeout=20)
    r.raise_for_status()
    m = re.match(r"\w+\((.*)\)$", r.text.strip(), re.DOTALL)
    data = json.loads(m.group(1) if m else r.text)
    return data.get("model", {}).get("json") or []


def get_parts(slug: str) -> list[dict]:
    """Fetch parts list for an assembly slug. Returns list of {ref, oem, description}."""
    params = {"arik": ARIK, "ariq": slug, "responsive": "true"}
    r = session.get(BASE + "/Parts/GetDetails", params=params, timeout=20)
    r.raise_for_status()
    html = r.json().get("html", "")
    return parse_parts_html(html)


def parse_parts_html(html: str) -> list[dict]:
    """Parse parts from GetDetails HTML using ARI-specific CSS classes.

    Each part row is a <li class="ariPartInfo ..."> containing:
      .ariPLTag   -> "Ref:" label + ref number as trailing text node
      .ariPartNumber -> OEM part number
      .ariPLDesc  -> part description
    """
    soup = BeautifulSoup(html, "html.parser")
    parts = []

    for row in soup.find_all(class_="ariPartInfo"):
        # Ref number: text inside .ariPLTag after the .ariTag "Ref:" label
        tag_el = row.find(class_="ariPLTag")
        ref = ""
        if tag_el:
            # Remove the inner ariTag div to isolate the ref number text
            inner = tag_el.find(class_="ariTag")
            if inner:
                inner.extract()
            ref = tag_el.get_text(strip=True)

        # OEM: the ariPartNumber div holds just the SKU
        num_el = row.find(class_="ariPartNumber")
        oem = num_el.get_text(strip=True) if num_el else ""

        # Description: ariPLDesc — remove nested image divs first
        desc_el = row.find(class_="ariPLDesc")
        description = ""
        if desc_el:
            for noise in desc_el.find_all(class_=["ariExtendRow_Img", "ari_PartRow_LargeImg"]):
                noise.decompose()
            description = desc_el.get_text(separator=" ", strip=True)

        if oem:
            parts.append({"ref": ref, "oem": oem, "description": description})

    return parts


def load_existing(csv_path: Path) -> dict:
    """Load existing CSV into dict keyed by unique_key."""
    existing = {}
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing[row["unique_key"]] = row
    return existing


def save_csv(records: dict, csv_path: Path):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records.values())


# ── main scraping logic ──────────────────────────────────────────────────────

def scrape():
    start = datetime.now()
    log.info("=== Scraper started at %s ===", start.isoformat())

    existing = load_existing(CSV_FILE)
    log.info("Loaded %d existing records from CSV", len(existing))

    collected = 0
    new_count = 0
    updated_count = 0
    errors = 0

    now_str = datetime.now(timezone.utc).isoformat()

    # Step 1: get Troy-Bilt root
    log.info("Fetching root catalog...")
    root_items = get_assembly(ARIB)
    time.sleep(DELAY)

    troy = next((i for i in root_items if "Troy" in i["data"]), None)
    if not troy:
        log.error("Troy-Bilt not found in root catalog")
        return
    log.info("Found: %s", troy["data"])

    # Step 2: get Push Walk-Behind Mowers
    troy_items = get_assembly(ARIB, troy["attr"]["aria"])
    time.sleep(DELAY)

    push = next((i for i in troy_items if "11-Push" in i["data"] or ("Push" in i["data"] and "Walk" in i["data"])), None)
    if not push:
        log.error("11-Push Walk-Behind Mowers not found")
        return
    log.info("Found: %s", push["data"])

    # Step 3: get year folders (2024, 2025 Models)
    year_items = get_assembly(ARIB, push["attr"]["aria"])
    time.sleep(DELAY)

    target_year_nodes = [i for i in year_items if i["data"] in TARGET_YEARS]
    log.info("Target year folders: %s", [i["data"] for i in target_year_nodes])

    path_prefix = f"MTD Merged Data Staging - Troy-Bilt - {push['data']}"

    for year_node in target_year_nodes:
        year_label = year_node["data"]
        year_path = f"{path_prefix} - {year_label}"
        log.info("Processing %s...", year_label)

        # Step 4: get models under each year
        model_items = get_assembly(ARIB, year_node["attr"]["aria"])
        time.sleep(DELAY)
        log.info("  %d models found", len(model_items))

        for model in model_items:
            model_label = model["data"]
            model_path = f"{year_path} - {model_label}"
            log.info("  Model: %s", model_label)

            # Step 5: get assemblies for model
            try:
                asm_items = get_assembly(ARIB, model["attr"]["aria"])
            except Exception as e:
                log.error("  Error getting assemblies for %s: %s", model_label, e)
                errors += 1
                continue
            time.sleep(DELAY)

            # Only assemblies (rel=assembly), skip folders like "Assemblies for X"
            # There's typically a "Assemblies for MODEL" folder wrapping them
            # Flatten: if items are folders, drill one level deeper
            flat_assemblies = []
            for item in asm_items:
                if item["attr"].get("rel") == "assembly":
                    flat_assemblies.append(item)
                else:
                    # It's a folder (e.g. "Assemblies for ..."), go one level deeper
                    try:
                        sub = get_assembly(ARIB, item["attr"]["aria"])
                        time.sleep(DELAY)
                        for sub_item in sub:
                            if sub_item["attr"].get("rel") == "assembly":
                                sub_item["_parent_folder"] = item["data"]
                                flat_assemblies.append(sub_item)
                            else:
                                # One more level (rare)
                                try:
                                    sub2 = get_assembly(ARIB, sub_item["attr"]["aria"])
                                    time.sleep(DELAY)
                                    for s2 in sub2:
                                        if s2["attr"].get("rel") == "assembly":
                                            s2["_parent_folder"] = f"{item['data']} - {sub_item['data']}"
                                            flat_assemblies.append(s2)
                                except Exception as e:
                                    log.warning("    Sub2 error: %s", e)
                    except Exception as e:
                        log.error("  Error getting sub-assemblies for %s/%s: %s", model_label, item["data"], e)
                        errors += 1

            if not flat_assemblies:
                log.warning("  No assemblies found for %s", model_label)
                continue

            log.info("  %d assemblies to scrape", len(flat_assemblies))

            for asm in flat_assemblies:
                asm_label = asm["data"]
                parent = asm.get("_parent_folder", "")
                if parent:
                    asm_path = f"{model_path} - {parent} - {asm_label}"
                else:
                    asm_path = f"{model_path} - {asm_label}"

                slug = asm["attr"].get("slug", "")
                if not slug:
                    continue

                # Step 6: get parts for this assembly
                try:
                    parts = get_parts(slug)
                except Exception as e:
                    log.error("    Error getting parts for %s: %s", asm_label, e)
                    errors += 1
                    time.sleep(1)
                    continue
                time.sleep(DELAY)

                for part in parts:
                    oem = part["oem"]
                    if not oem:
                        continue
                    collected += 1
                    unique_key = f"{asm_path}|{oem}"
                    record = {
                        "unique_key": unique_key,
                        "path": asm_path,
                        "ref": part["ref"],
                        "oem": oem,
                        "description": part["description"],
                        "updated_at": now_str,
                    }
                    if unique_key not in existing:
                        new_count += 1
                        existing[unique_key] = record
                    else:
                        old = existing[unique_key]
                        if old["description"] != record["description"] or old["ref"] != record["ref"]:
                            updated_count += 1
                            existing[unique_key] = record

    save_csv(existing, CSV_FILE)

    elapsed = (datetime.now() - start).total_seconds()
    log.info("=== Done in %.1fs | collected=%d new=%d updated=%d errors=%d total_in_csv=%d ===",
             elapsed, collected, new_count, updated_count, errors, len(existing))


if __name__ == "__main__":
    scrape()
