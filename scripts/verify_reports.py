#!/usr/bin/env python3
"""
Report Verification Script

This script verifies that collections marked as having reports in scan_cache.json
actually contain text reports (SR, DOC, RTSTRUCT). It creates a new cache file
with verification results.
"""

import json
import asyncio
import aiohttp
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set
import sys

# Constants
TCIA_API_BASE = "https://services.cancerimagingarchive.net/services/v4/TCIA"
SCAN_CACHE_FILE = Path("scan_cache.json")
VERIFY_CACHE_FILE = Path("verify_cache.json")
LOG_DIR = Path("logs/verify")
API_RATE_LIMIT_DELAY = 0.2

# Report modalities we're specifically looking for
TEXT_REPORT_MODALITIES = {"SR", "DOC", "RTSTRUCT"}

def setup_logging() -> logging.Logger:
    """Configure and return a logger."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger("verify_reports")
    logger.setLevel(logging.DEBUG)
    
    # Create timestamped log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"verify_{timestamp}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formatters
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def load_scan_cache() -> Dict:
    """Load the original scan cache."""
    if SCAN_CACHE_FILE.exists():
        with open(SCAN_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def load_verify_cache() -> Dict:
    """Load the verification cache if it exists."""
    if VERIFY_CACHE_FILE.exists():
        with open(VERIFY_CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_verify_cache(cache: Dict) -> None:
    """Save the verification cache."""
    with open(VERIFY_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

async def get_series(session: aiohttp.ClientSession, patient_id: str, collection: str) -> List[Dict]:
    """Get all series for a patient in a collection."""
    url = f"{TCIA_API_BASE}/query/getSeries"
    params = {
        "Collection": collection,
        "PatientID": patient_id
    }
    
    await asyncio.sleep(API_RATE_LIMIT_DELAY)
    
    async with session.get(url, params=params) as response:
        if response.status == 200:
            return await response.json()
        return []

async def get_patients(session: aiohttp.ClientSession, collection: str) -> List[Dict]:
    """Get all patients in a collection."""
    url = f"{TCIA_API_BASE}/query/getPatient"
    params = {"Collection": collection}
    
    await asyncio.sleep(API_RATE_LIMIT_DELAY)
    
    async with session.get(url, params=params) as response:
        if response.status == 200:
            return await response.json()
        return []

async def verify_collection_reports(session: aiohttp.ClientSession, collection: str, logger: logging.Logger) -> Dict:
    """Verify what types of reports exist in a collection."""
    logger.info(f"Verifying reports in collection: {collection}")
    
    # Get a sample of patients
    patients = await get_patients(session, collection)
    if not patients:
        logger.warning(f"No patients found in collection {collection}")
        return {"has_text_reports": False, "report_types": []}
    
    # Sample up to 10 patients
    sample_size = min(10, len(patients))
    sample_patients = patients[:sample_size]
    
    found_report_types = set()
    
    for patient in sample_patients:
        patient_id = patient["PatientID"]
        logger.debug(f"Checking patient: {patient_id}")
        
        series = await get_series(session, patient_id, collection)
        for s in series:
            modality = s.get("Modality", "").upper()
            if modality in TEXT_REPORT_MODALITIES:
                found_report_types.add(modality)
                logger.info(f"Found {modality} report in patient {patient_id}")
    
    has_text_reports = len(found_report_types) > 0
    logger.info(f"Collection {collection} has text reports: {has_text_reports}")
    if has_text_reports:
        logger.info(f"Found report types: {', '.join(found_report_types)}")
    
    return {
        "has_text_reports": has_text_reports,
        "report_types": list(found_report_types),
        "verified_at": datetime.now().isoformat()
    }

async def main():
    logger = setup_logging()
    logger.info("Starting report verification")
    
    # Load both caches
    scan_cache = load_scan_cache()
    verify_cache = load_verify_cache()
    collections_to_verify = []
    
    # Find all collections marked as having reports in scan cache
    for section, collections in scan_cache.items():
        for collection, data in collections.items():
            if data.get("has_reports", False):
                collections_to_verify.append((section, collection))
    
    logger.info(f"Found {len(collections_to_verify)} collections to verify")
    
    async with aiohttp.ClientSession() as session:
        for section, collection in collections_to_verify:
            logger.info(f"\nVerifying collection: {collection} (from {section})")
            result = await verify_collection_reports(session, collection, logger)
            
            # Initialize section if it doesn't exist
            if section not in verify_cache:
                verify_cache[section] = {}
            
            # Update verification cache
            verify_cache[section][collection] = {
                "has_text_reports": result["has_text_reports"],
                "report_types": result["report_types"],
                "verified_at": result["verified_at"],
                "original_has_reports": scan_cache[section][collection].get("has_reports", False)
            }
            save_verify_cache(verify_cache)
            
            if result["has_text_reports"]:
                logger.info(f"✓ Verified text reports in {collection}")
            else:
                logger.warning(f"✗ No text reports found in {collection}")
    
    logger.info("\nVerification complete!")
    
    # Print summary
    print("\nVerification Summary:")
    print("====================")
    for section, collections in verify_cache.items():
        for collection, data in collections.items():
            status = "✓" if data.get("has_text_reports", False) else "✗"
            report_types = ", ".join(data.get("report_types", [])) or "None"
            original_status = "✓" if data.get("original_has_reports", False) else "✗"
            print(f"{status} {collection} (Original: {original_status}): {report_types}")

if __name__ == "__main__":
    asyncio.run(main()) 