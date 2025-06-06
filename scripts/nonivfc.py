#!/usr/bin/env python3
"""
Non-interactive TCIA Case Fetcher

This script provides a non-interactive version of fc.py, designed to be called by other scripts.
It focuses on checking if collections have reports and fetching study information.
"""

import os
import sys
import json
import requests
import logging
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
import aiohttp
import asyncio

# Constants
TCIA_API_BASE = "https://services.cancerimagingarchive.net/services/v4/TCIA"
DATA_DIR = Path("data/images")
LOG_DIR = Path(__file__).parent.parent / "logs" / "scanner"
CACHE_DIR = Path(__file__).parent.parent / "cache" / "studies"
API_RATE_LIMIT_DELAY = 0.2  # Delay between API calls in seconds
REQUEST_TIMEOUT = 450  # Increased timeout for all collections
MAX_RETRIES = 3  # Number of retries for failed requests

# Report-related keywords to check in series
REPORT_KEYWORDS = {
    "report",
    "rtstruct",
    "sc",  # Secondary Capture
    "doc",
    "annotation",
    "segmentation",
    "measurement",
    "findings",
    "impression",
    "diagnosis"
}

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Non-interactive TCIA Case Fetcher",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--collection",
        help="Name of the TCIA collection to check",
        type=str,
        required=True
    )
    parser.add_argument(
        "--report-required",
        help="Check if collection has reports",
        action="store_true"
    )
    parser.add_argument(
        "--verbose",
        help="Enable debug-level logging",
        action="store_true"
    )
    parser.add_argument(
        "--logfile",
        help="Set custom log file path",
        type=str,
        default="nonivfc.log"
    )
    
    return parser.parse_args()

def setup_logging(args: argparse.Namespace) -> logging.Logger:
    """Configure and return a logger."""
    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create a logger
    logger = logging.getLogger("nonivfc")
    logger.setLevel(logging.DEBUG)
    
    # Remove any existing handlers
    logger.handlers = []
    
    # Always create a timestamped log file for every run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"nonivfc_{timestamp}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    
    # Always create a console handler (info level by default, debug if verbose)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Log initial setup
    logger.info("=" * 80)
    logger.info("Logging initialized")
    logger.info(f"Log file: {log_file.absolute()}")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info(f"Command line arguments: {args}")
    logger.info("=" * 80)
    
    return logger

async def get_series(session: aiohttp.ClientSession, patient_id: str, collection: str) -> List[Dict]:
    """Get all series for a patient in a collection."""
    url = f"{TCIA_API_BASE}/query/getSeries"
    params = {
        "Collection": collection,
        "PatientID": patient_id
    }
    
    # Add delay between API calls
    await asyncio.sleep(API_RATE_LIMIT_DELAY)
    
    async with session.get(url, params=params) as response:
        if response.status == 200:
            return await response.json()
        return []

def has_report_series(series_list: list, logger: logging.Logger) -> bool:
    """Check if any series in the list appears to be a report."""
    for series in series_list:
        series_desc = series.get("SeriesDescription", "").lower()
        modality = series.get("Modality", "").lower()
        series_number = series.get("SeriesNumber", "")
        
        # Log series details for debugging
        logger.debug(f"Checking series: {series_desc} (Modality: {modality}, Number: {series_number})")
        
        # Check if series description contains report keywords
        if any(keyword in series_desc for keyword in REPORT_KEYWORDS):
            logger.info(f"Found report keyword in series: {series_desc}")
            return True
            
        # Check if modality indicates a report
        if modality in ["SR", "DOC", "SEG", "RTSTRUCT"]:
            logger.info(f"Found report modality: {modality}")
            return True
            
    return False

async def check_collection_has_reports(session: aiohttp.ClientSession, collection: str, logger, sample_size: int = 20) -> bool:
    """Check if a collection has any reports by sampling patients."""
    logger.info(f"Checking if collection {collection} has reports (sampling {sample_size} series)")
    
    # Get a sample of patients
    url = f"{TCIA_API_BASE}/query/getPatient"
    params = {"Collection": collection}
    
    # Add delay before first API call
    await asyncio.sleep(API_RATE_LIMIT_DELAY)
    
    async with session.get(url, params=params) as response:
        if response.status != 200:
            logger.error(f"Failed to get patients for collection {collection}")
            return False
            
        patients = await response.json()
        if not patients:
            logger.warning(f"No patients found in collection {collection}")
            return False
            
        logger.info(f"Found {len(patients)} patients in collection {collection}")
        
        # Sample patients
        sample_patients = patients[:sample_size]
        logger.info(f"Sampling {len(sample_patients)} patients")
        
        # Check each patient's series for reports
        for patient in sample_patients:
            patient_id = patient["PatientID"]
            logger.debug(f"Checking patient: {patient_id}")
            series = await get_series(session, patient_id, collection)
            
            if has_report_series(series, logger):
                logger.info(f"Found reports in collection {collection} for patient {patient_id}")
                return True
                
        logger.warning(f"No reports found in collection {collection} after checking {len(sample_patients)} patients")
        return False

async def main():
    args = parse_args()
    logger = setup_logging(args)
    
    # Ensure directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create aiohttp session
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if args.report_required:
            has_reports = await check_collection_has_reports(session, args.collection, logger)
            if not has_reports:
                logger.warning(f"Collection {args.collection} appears to have no reports, skipping")
                print(f"Collection {args.collection} appears to have no reports. Skipping...")
                sys.exit(1)
            else:
                print(f"Collection {args.collection} has reports.")
                sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main()) 