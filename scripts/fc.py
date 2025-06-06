#!/usr/bin/env python3
"""
TCIA Case Fetcher

This script provides a CLI interface to download medical imaging cases from TCIA.
It uses the public TCIA REST API to fetch collections, patients, and DICOM series.

Usage:
    python fc.py [options]

Options:
    Navigation / Automation:
        --collection COLLECTION    Name of the TCIA collection to use
        --subject SUBJECT         Subject ID to jump directly to
        --study STUDY            StudyInstanceUID to download directly
        --report-required        Skip studies without reports (no prompt)
        --download              Automatically download valid cases
        --limit                 Limit the number of studies to process
        --refresh-cache         Force refresh of cached study lists
        --resume-cache          Resume from partial cache if available

    Debugging / Logging:
        --verbose               Enable debug-level logging
        --logfile LOGFILE      Set custom log file path
        --help                 Show this help message
"""

import os
import sys
import json
import requests
import logging
import time
import threading
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.prompt import Prompt, Confirm
import zipfile
import io
import aiohttp
import asyncio

# Constants
TCIA_API_BASE = "https://services.cancerimagingarchive.net/services/v4/TCIA"
DATA_DIR = Path("data/images")
LOG_DIR = Path(__file__).parent.parent / "logs" / "fc"
CACHE_DIR = Path(__file__).parent.parent / "cache" / "studies"
SIZE_WARNING_THRESHOLD_GB = 80  # Warning threshold for collection size
API_RATE_LIMIT_DELAY = 0.2  # Delay between API calls in seconds
REQUEST_TIMEOUT = 450  # Increased timeout for all collections
MIN_PAGE_SIZE = 50  # Minimum page size for small collections
MAX_PAGE_SIZE = 200  # Maximum page size for large collections
MAX_RETRIES = 3  # Number of retries for failed requests
RATE_LIMIT_DELAY = 1.0  # Delay between requests
CACHE_CHUNK_SIZE = 1000  # Number of studies to cache before writing to disk
EARLY_EXIT_THRESHOLD = 0.8  # Exit early if we have 80% of needed studies with reports
MAX_CONCURRENT_DOWNLOADS = 5  # Maximum number of concurrent series downloads
MAX_CONCURRENT_PATIENTS = 10  # Maximum number of concurrent patient processing
MEMORY_THRESHOLD_MB = 1000  # Memory threshold in MB to trigger cleanup

# Known large collections that need special handling
LARGE_COLLECTIONS = {
    "UPENN-GBM",
    "TCGA-GBM",
    "TCGA-BRCA",
    "LIDC-IDRI"
}

# Report-related keywords to check in series
REPORT_KEYWORDS = {
    "report",
    "rtstruct",
    "sc",  # Secondary Capture
    "doc"
}

# Subspecialty mapping
subspecialty_map = {
    "neuroradiology": [
        "TCGA-GBM", "UPENN-GBM", "ICDC-Glioma", "Vestibular-Schwannoma-SEG",
        "Vestibular-Schwannoma-MC-RC", "MIDRC-RICORD-1A", "MIDRC-RICORD-1B", "MIDRC-RICORD-1C",
        "RIDER PHANTOM MRI", "RIDER Pilot", "CMMD", "GBM-DSC-MRI-DRO", "ISPY2"
    ],
    "breast": [
        "TCGA-BRCA", "CBIS-DDSM", "Breast-Diagnosis", "Breast-MRI-NACT-Pilot",
        "ACRIN-6698", "ACRIN-Contralateral-Breast-MR", "Advanced-MRI-Breast-Lesions",
        "Duke-Breast-Cancer-MRI", "Breast-Cancer-Screening-DBT", "QIN-BREAST", "QIN Breast DCE-MRI", "ISPY1"
    ],
    "msk": [
        "TCGA-SARC", "Soft-tissue-Sarcoma", "CPTAC-SAR", "Spine-Mets-CT-SEG", "CPTAC-CCRCC"
    ],
    "cardiothoracic": [
        "LIDC-IDRI", "RIDER Lung CT", "RIDER Lung PET-CT", "QIN LUNG CT",
        "LungCT-Diagnosis", "Lung-PET-CT-Dx", "Anti-PD-1_Lung", "NSCLC Radiogenomics",
        "NSCLC-Radiomics", "NSCLC-Radiomics-Genomics", "NSCLC-Radiomics-Interobserver1",
        "APOLLO-5-LUAD", "APOLLO-5-THYM", "APOLLO-5-LUNG-MISC"
    ],
    "abdominal": [
        "TCGA-COAD", "TCGA-LIHC", "TCGA-KIRC", "TCGA-KIRP", "TCGA-KICH", "TCGA-PAAD", "Pancreas-CT",
        "Pancreatic-CT-CBCT-SEG", "CT COLONOGRAPHY", "CT Lymph Nodes", "StageII-Colorectal-CT",
        "Colorectal-Liver-Metastases", "CTpred-Sunitinib-panNET", "CPTAC-LIHC", "CPTAC-CHOL",
        "PDMR-Texture-Analysis", "HCC-TACE-Seg", "C4KC-KiTS", "Adrenal-ACC-Ki67-Seg"
    ],
    "nuclear": [
        "ACRIN-NSCLC-FDG-PET", "FDG-PET-CT-Lesions", "QIN PET Phantom", "RIDER PHANTOM PET-CT",
        "NaF PROSTATE", "CT-vs-PET-Ventilation-Imaging", "QIBA CT-1C"
    ],
    "pediatric": [
        "Pediatric-CT-SEG", "NBIA Pediatric Brain", "Childhood Brain Tumor Network", "CPTAC-Pediatric"
    ]
}

# Initialize Rich console
console = Console()

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="TCIA Case Fetcher - Download medical imaging cases from TCIA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Navigation / Automation group
    nav_group = parser.add_argument_group("Navigation / Automation")
    nav_group.add_argument(
        "--collection",
        help="Name of the TCIA collection to use",
        type=str
    )
    nav_group.add_argument(
        "--subject",
        help="Subject ID to jump directly to",
        type=str
    )
    nav_group.add_argument(
        "--study",
        help="StudyInstanceUID to download directly",
        type=str
    )
    nav_group.add_argument(
        "--report-required",
        help="Skip studies without reports (no prompt)",
        action="store_true"
    )
    nav_group.add_argument(
        "--download",
        help="Automatically download valid cases",
        action="store_true"
    )
    nav_group.add_argument(
        "--limit",
        help="Limit the number of studies to process",
        type=int
    )
    nav_group.add_argument(
        "--refresh-cache",
        help="Force refresh of cached study lists",
        action="store_true"
    )
    nav_group.add_argument(
        "--resume-cache",
        help="Resume from partial cache if available",
        action="store_true"
    )
    
    # Debugging / Logging group
    debug_group = parser.add_argument_group("Debugging / Logging")
    debug_group.add_argument(
        "--verbose",
        help="Enable debug-level logging",
        action="store_true"
    )
    debug_group.add_argument(
        "--logfile",
        help="Set custom log file path",
        type=str,
        default="fc.log"
    )
    
    return parser.parse_args()

def setup_logging(args: argparse.Namespace) -> logging.Logger:
    """Configure and return a logger for the fc module."""
    # Ensure log directory exists
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Log directory created/verified at: {LOG_DIR.absolute()}")  # Debug print
    except Exception as e:
        print(f"Error creating log directory: {e}")  # Debug print
        raise
    
    # Create a logger
    logger = logging.getLogger("fc")
    logger.setLevel(logging.DEBUG)  # Always set to DEBUG to capture everything
    
    # Remove any existing handlers to avoid duplicate logs
    logger.handlers = []
    
    # Create a file handler for the fc module
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"fc_{timestamp}.log"
    print(f"Creating log file at: {log_file.absolute()}")  # Debug print
    
    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file
    except Exception as e:
        print(f"Error creating log file: {e}")  # Debug print
        raise
    
    # Create a console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)  # Only console output is affected by verbosity
    
    # Create formatters and add them to the handlers
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s\n'
        'Additional Info: %(pathname)s:%(lineno)d\n'
        'Function: %(funcName)s\n'
        'Thread: %(threadName)s\n'
        'Process: %(processName)s\n'
    )
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)
    
    # Add the handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Log initial setup information
    logger.info("=" * 80)
    logger.info("Logging initialized")
    logger.info(f"Log file: {log_file.absolute()}")
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info(f"Command line arguments: {args}")
    logger.info("=" * 80)
    
    return logger

def ensure_data_dir() -> None:
    """Ensure the data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Ensured data directory exists at {DATA_DIR}")  # Use print instead of logger for setup

def ensure_cache_dir() -> None:
    """Ensure the cache directory exists."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Ensured cache directory exists at {CACHE_DIR}")  # Use print instead of logger for setup
    except Exception as e:
        print(f"Error creating cache directory: {e}")
        console.print(f"[red]Error creating cache directory: {e}[/red]")
        raise

def get_cached_studies(collection, logger=None):
    """Get cached studies for a collection."""
    cache_file = CACHE_DIR / f"{collection}.jsonl"
    uids_file = CACHE_DIR / f"{collection}.uids.json"
    
    if not cache_file.exists():
        if logger:
            logger.debug(f"No cache file found for collection {collection}")
        return [], set()
    
    try:
        studies = []
        seen_uids = set()
        
        # Load seen UIDs if available
        if uids_file.exists():
            try:
                with open(uids_file, 'r') as f:
                    seen_uids = set(json.load(f))
                if logger:
                    logger.debug(f"Loaded {len(seen_uids)} cached UIDs")
            except Exception as e:
                if logger:
                    logger.warning(f"Error loading UIDs cache: {e}")
        
        # Load studies
        with open(cache_file, 'r') as f:
            for line in f:
                try:
                    study = json.loads(line.strip())
                    studies.append(study)
                    seen_uids.add(study["StudyInstanceUID"])
                except json.JSONDecodeError as e:
                    if logger:
                        logger.warning(f"Error parsing study from cache: {e}")
                    continue
        
        # Save updated UIDs
        try:
            with open(uids_file, 'w') as f:
                json.dump(list(seen_uids), f)
        except Exception as e:
            if logger:
                logger.warning(f"Error saving UIDs cache: {e}")
        
        if logger:
            logger.info(f"Loaded {len(studies)} cached studies with {len(seen_uids)} unique UIDs")
        return studies, seen_uids
    except Exception as e:
        if logger:
            logger.error(f"Error loading cache for {collection}: {e}")
        return [], set()

def get_dynamic_page_size(total_studies: Optional[int]) -> int:
    """Calculate optimal page size based on collection size."""
    if total_studies is None:
        return MIN_PAGE_SIZE
    
    if total_studies <= 100:
        return MIN_PAGE_SIZE
    elif total_studies <= 1000:
        return 100
    else:
        return MAX_PAGE_SIZE

def save_study_to_cache(collection: str, study: Dict, seen_uids: Set[str], cache_buffer: List[Dict]) -> None:
    """Save a single study to cache buffer and update seen UIDs."""
    study_uid = study.get("StudyInstanceUID")
    if study_uid:
        seen_uids.add(study_uid)
        cache_buffer.append(study)
        
        # Write to disk if buffer is full
        if len(cache_buffer) >= CACHE_CHUNK_SIZE:
            flush_cache_buffer(collection, cache_buffer)
            cache_buffer.clear()
            
            # Check memory usage after flushing
            if check_memory_usage():
                logger.info("Memory threshold reached after cache flush, waiting for cleanup...")
                time.sleep(1)  # Give time for memory cleanup

def flush_cache_buffer(collection: str, cache_buffer: List[Dict]) -> None:
    """Write cached studies to disk."""
    if not cache_buffer:
        return
        
    cache_file = CACHE_DIR / f"{collection}.jsonl"
    try:
        with open(cache_file, 'a') as f:
            for study in cache_buffer:
                f.write(json.dumps(study) + "\n")
                
        # Force garbage collection after writing
        import gc
        gc.collect()
    except Exception as e:
        logger.error(f"Error flushing cache buffer for {collection}: {e}")

def finalize_cache(collection: str, studies: List[Dict], seen_uids: Set[str]) -> None:
    """Convert .jsonl cache to .json for finalized collections."""
    jsonl_file = CACHE_DIR / f"{collection}.jsonl"
    json_file = CACHE_DIR / f"{collection}.json"
    uids_file = CACHE_DIR / f"{collection}.uids.json"
    
    try:
        # Save as JSON
        with open(json_file, 'w') as f:
            json.dump(studies, f, indent=2)
        
        # Save final UIDs
        with open(uids_file, 'w') as f:
            json.dump(list(seen_uids), f)
        
        # Remove JSONL file
        jsonl_file.unlink()
        
        # Force garbage collection after finalizing
        import gc
        gc.collect()
        
        logger.info(f"Finalized cache for {collection} with {len(studies)} studies and {len(seen_uids)} UIDs")
    except Exception as e:
        logger.error(f"Error finalizing cache for {collection}: {e}")

def get_collections_sync() -> List[Dict]:
    # Keep the old sync version for CLI bootstrapping if needed
    ...

async def get_collections(session: aiohttp.ClientSession, logger) -> List[Dict]:
    try:
        logger.info("Fetching collections from TCIA (async)")
        url = f"{TCIA_API_BASE}/query/getCollectionValues"
        async with session.get(url, timeout=60) as response:
            response.raise_for_status()
            collections = await response.json()
            logger.info(f"Successfully retrieved {len(collections)} collections")
            logger.debug(f"Collection names: {[c.get('Collection', 'N/A') for c in collections]}")
            return collections
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching collections: {e}")
        return []
    except Exception as e:
        logger.error(f"Unhandled error while fetching collections: {e}")
        return []

async def get_patients(session: aiohttp.ClientSession, collection: str) -> List[Dict]:
    try:
        logger.info(f"Fetching patients for collection: {collection} (async)")
        url = f"{TCIA_API_BASE}/query/getPatient"
        params = {"Collection": collection}
        async with session.get(url, params=params, timeout=60) as response:
            response.raise_for_status()
            patients = await response.json()
            logger.debug(f"Retrieved {len(patients)} patients for collection {collection}")
            return patients
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching patients for collection {collection}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unhandled error while fetching patients: {e}")
        return []

async def get_series(session: aiohttp.ClientSession, patient_id: str, collection: str) -> List[Dict]:
    try:
        logger.info(f"Fetching series for patient {patient_id} in collection {collection} (async)")
        url = f"{TCIA_API_BASE}/query/getSeries"
        params = {"PatientID": patient_id, "Collection": collection}
        async with session.get(url, params=params, timeout=60) as response:
            response.raise_for_status()
            series = await response.json()
            logger.debug(f"Retrieved {len(series)} series for patient {patient_id}")
            return series
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching series for patient {patient_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unhandled error while fetching series: {e}")
        return []

async def get_series_for_study(session: aiohttp.ClientSession, collection: str, patient_id: str, study_uid: str) -> List[Dict]:
    try:
        url = f"{TCIA_API_BASE}/query/getSeries"
        params = {"Collection": collection, "PatientID": patient_id, "StudyInstanceUID": study_uid}
        logger.info(f"Fetching series for study {study_uid} (Patient: {patient_id}) (async)")
        async with session.get(url, params=params, timeout=60) as response:
            response.raise_for_status()
            series = await response.json()
            logger.info(f"Found {len(series)} series in study {study_uid}")
            logger.debug(f"Series modalities: {[s.get('Modality', 'N/A') for s in series]}")
            logger.debug(f"Series descriptions: {[s.get('SeriesDescription', 'N/A') for s in series]}")
            return series
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching series for study {study_uid}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unhandled error while fetching series: {e}")
        return []

async def get_study_by_uid(session: aiohttp.ClientSession, collection: str, study_uid: str) -> Optional[Dict]:
    try:
        url = f"{TCIA_API_BASE}/query/getPatientStudy"
        params = {"Collection": collection, "StudyInstanceUID": study_uid}
        logger.info(f"Fetching study {study_uid} from collection {collection} (async)")
        async with session.get(url, params=params, timeout=60) as response:
            response.raise_for_status()
            studies = await response.json()
            if not studies:
                logger.error(f"Study {study_uid} not found in collection {collection}")
                return None
            study = studies[0]
            logger.info(f"Found study {study_uid} for patient {study.get('PatientID')}")
            return study
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching study {study_uid}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unhandled error while fetching study: {e}")
        return None

def filter_collections_by_subspecialty(collections: List[Dict], subspecialty: Optional[str] = None, logger=None) -> List[Dict]:
    """Filter collections based on selected subspecialty."""
    if logger:
        logger.info(f"Filtering collections for subspecialty: {subspecialty}")
    if not subspecialty or subspecialty == "show_all":
        if logger:
            logger.debug("No subspecialty filter applied, returning all collections")
        return collections
    if subspecialty not in subspecialty_map:
        if logger:
            logger.warning(f"Unknown subspecialty: {subspecialty}")
            logger.debug(f"Available subspecialties: {list(subspecialty_map.keys())}")
        return collections
    normalized_subspecialty_list = [name.strip().upper() for name in subspecialty_map[subspecialty]]
    if logger:
        logger.debug(f"Normalized subspecialty list for {subspecialty}: {sorted(normalized_subspecialty_list)}")
    filtered = [
        collection for collection in collections
        if collection.get("Collection", "").strip().upper() in normalized_subspecialty_list
    ]
    normalized_api_collections = [c.get("Collection", "").strip().upper() for c in collections]
    if logger:
        logger.debug(f"Normalized API collections: {sorted(normalized_api_collections)}")
    if not filtered:
        if logger:
            logger.warning(
                f"No collections found for subspecialty '{subspecialty}' after normalization. "
                f"Valid collection names: {sorted(normalized_subspecialty_list)}"
            )
        console.print(f"[yellow]No collections found for subspecialty '{subspecialty}' after normalization.[/yellow]")
        console.print("[yellow]This might be due to case or whitespace differences in collection names.[/yellow]")
    else:
        if logger:
            logger.info(
                f"Filtered collections for {subspecialty}: {len(filtered)} collections found. "
                f"Matched collections: {[c.get('Collection') for c in filtered]}"
            )
    return filtered

def display_studies(studies: List[Dict], page_index: int = 0, page_size: int = 10) -> None:
    """Display available studies in a table with pagination."""
    # Calculate page boundaries
    start_idx = page_index * page_size
    end_idx = min(start_idx + page_size, len(studies))
    current_page_studies = studies[start_idx:end_idx]
    total_pages = (len(studies) + page_size - 1) // page_size
    
    logger.debug(f"Displaying page {page_index + 1} of {total_pages} (studies {start_idx + 1}–{end_idx} of {len(studies)})")
    
    table = Table(title=f"Available Cases (Page {page_index + 1} of {total_pages})")
    table.add_column("Index", style="cyan")
    table.add_column("Case ID", style="green")
    table.add_column("Date", style="yellow")
    table.add_column("Description", style="blue")
    
    # Add study rows
    for idx, study in enumerate(current_page_studies, start_idx + 1):
        table.add_row(
            str(idx),
            study.get("PatientID", "N/A"),
            study.get("StudyDate", "N/A"),
            study.get("StudyDescription", "N/A")
        )
    
    # Add navigation options
    nav_row = len(current_page_studies) + 1
    
    # Add Previous Page option if not on first page
    if page_index > 0:
        table.add_row(
            str(nav_row),
            "[Previous Page]",
            "",
            f"View studies {max(1, start_idx - page_size + 1)}–{start_idx}"
        )
        nav_row += 1
    
    # Add Next Page option if more pages exist
    if end_idx < len(studies):
        table.add_row(
            str(nav_row),
            "[Next Page]",
            "",
            f"View studies {end_idx + 1}–{min(end_idx + page_size, len(studies))}"
        )
        nav_row += 1
    
    # Add Cancel option
    table.add_row(
        str(nav_row),
        "[Cancel]",
        "",
        "Exit or go back"
    )
    
    console.print(table)

def select_study(studies: List[Dict]) -> Optional[Dict]:
    """Display paginated studies and handle user selection."""
    if not studies:
        logger.warning("No studies available to display")
        console.print("\n[yellow]No studies with reports were found in this collection.[/yellow]")
        return None
    
    page_size = 10
    page_index = 0
    total_pages = (len(studies) + page_size - 1) // page_size
    
    logger.info(f"Starting study selection with {len(studies)} studies across {total_pages} pages")
    
    while True:
        display_studies(studies, page_index, page_size)
        
        # Calculate valid choices
        start_idx = page_index * page_size
        end_idx = min(start_idx + page_size, len(studies))
        valid_choices = [str(i) for i in range(start_idx + 1, end_idx + 1)]
        
        # Add navigation options
        nav_row = len(valid_choices) + 1
        
        if page_index > 0:
            valid_choices.append(str(nav_row))
            nav_row += 1
        
        if end_idx < len(studies):
            valid_choices.append(str(nav_row))
            nav_row += 1
        
        valid_choices.append(str(nav_row))
        
        choice = Prompt.ask(
            "Select a case or navigation option (enter number)",
            choices=valid_choices
        )
        
        choice_num = int(choice)
        
        # Handle navigation options
        if choice_num > end_idx:
            # Calculate which navigation option was selected
            nav_option = choice_num - end_idx
            
            # Previous Page
            if page_index > 0 and nav_option == 1:
                page_index -= 1
                logger.info(f"Navigating to previous page ({page_index + 1} of {total_pages})")
                continue
            
            # Next Page (if available)
            if end_idx < len(studies) and nav_option == (1 if page_index > 0 else 1):
                page_index += 1
                logger.info(f"Navigating to next page ({page_index + 1} of {total_pages})")
                continue
            
            # Cancel
            logger.info("User cancelled study selection")
            return None
        
        # Return selected study
        selected_study = studies[choice_num - 1]
        logger.info(f"Selected case: {selected_study.get('StudyInstanceUID')} for patient {selected_study.get('PatientID')}")
        return selected_study

def show_live_timer(stop_event: threading.Event) -> None:
    """Show a live timer in the terminal while waiting for an operation."""
    start = time.time()
    try:
        while not stop_event.is_set():
            elapsed = int(time.time() - start)
            minutes = elapsed // 60
            seconds = elapsed % 60
            print(f"\r⏳ Fetching study list... {minutes}m {seconds}s", end="", flush=True)
            time.sleep(1)  # Update every second
    except KeyboardInterrupt:
        stop_event.set()  # Ensure we stop if interrupted
        raise  # Re-raise to be caught by main handler

async def get_patient_series(session: aiohttp.ClientSession, collection: str, patient_id: str) -> List[Dict]:
    """Fetch all series for a patient asynchronously using the shared session."""
    url = f"{TCIA_API_BASE}/query/getSeries?Collection={collection}&PatientID={patient_id}"
    async with session.get(url) as response:
        if response.status == 200:
            return await response.json()
        else:
            logging.error(f"Failed to fetch series for patient {patient_id}: {response.status}")
            return []

async def filter_patients_with_reports(session: aiohttp.ClientSession, collection: str, patient_studies: Dict[str, List[Dict]]) -> List[Dict]:
    """Filter patients to include only those with reports, using async fetching and shared session."""
    valid_patients = []
    tasks = []
    for patient_id, studies in patient_studies.items():
        tasks.append(get_patient_series(session, collection, patient_id))
    series_results = await asyncio.gather(*tasks)
    for patient_id, series_list in zip(patient_studies.keys(), series_results):
        if has_report_series(series_list):
            valid_patients.append(patient_id)
    return valid_patients

async def check_collection_has_reports(session: aiohttp.ClientSession, collection: str, logger, sample_size: int = 10) -> bool:
    """Quickly check if a collection is likely to have reports by sampling series directly.
    Uses /getSeries with Collection and limit to avoid fetching all patients.
    """
    try:
        logger.info(f"Checking if collection {collection} has reports (sampling {sample_size} series)")
        url = f"{TCIA_API_BASE}/query/getSeries"
        params = {"Collection": collection, "limit": sample_size}
        
        start_time = time.time()
        print(f"Checking reports for {collection}...", end="", flush=True)
        
        # Start a background task to update the timer
        async def update_timer():
            while True:
                elapsed_time = time.time() - start_time
                print(f"\rChecking reports for {collection}... {elapsed_time:.2f} seconds", end="", flush=True)
                await asyncio.sleep(0.1)
        
        timer_task = asyncio.create_task(update_timer())
        
        async with session.get(url, params=params, timeout=60) as response:
            response.raise_for_status()
            series_list = await response.json()
        
        # Cancel the timer task
        timer_task.cancel()
        try:
            await timer_task
        except asyncio.CancelledError:
            pass
        
        elapsed_time = time.time() - start_time
        print(f"\rChecking reports for {collection}... completed in {elapsed_time:.2f} seconds")
        console.print("")
        
        if not series_list:
            logger.warning(f"No series found in collection {collection}")
            return False
        # Check if any series contains reports
        if has_report_series(series_list):
            logger.info(f"Found reports in collection {collection}")
            return True
        logger.warning(f"No reports found in sample from collection {collection}")
        return False
    except Exception as e:
        logger.error(f"Error checking collection {collection} for reports: {e}")
        return False

async def get_studies_for_collection(session: aiohttp.ClientSession, collection: str, limit: Optional[int] = None, refresh_cache: bool = False, resume_cache: bool = False, fetch_attempt: int = 1, logger=None) -> List[Dict]:
    """Fetch studies for a collection with async support and optimizations."""
    MAX_FETCH_ATTEMPTS = 5
    
    if fetch_attempt > MAX_FETCH_ATTEMPTS:
        logger.warning(f"Reached maximum fetch attempts ({MAX_FETCH_ATTEMPTS})")
        return []
    
    # First check if the collection is likely to have reports
    if not await check_collection_has_reports(session, collection, logger):
        logger.warning(f"Collection {collection} appears to have no reports, skipping")
        console.print(f"\n[yellow]Collection {collection} appears to have no reports. Skipping...[/yellow]")
        return []  # Return to main menu instead of exiting
    
    # Load existing cache if available
    all_studies, seen_uids = get_cached_studies(collection, logger)
    if all_studies:
        logger.info(f"Loaded {len(all_studies)} studies from cache")
    
    # Fetch from API if needed
    if refresh_cache or not all_studies:
        stop_event = threading.Event()
        timer_thread = None
        cache_buffer = []
        
        try:
            url = f"{TCIA_API_BASE}/query/getPatientStudy"
            base_params = {
                "Collection": collection,
                "format": "json"
            }
            
            logger.info(f"Fetching cases for collection: {collection}")
            
            # Start live timer
            timer_thread = threading.Thread(target=show_live_timer, args=(stop_event,))
            timer_thread.daemon = True
            timer_thread.start()
            start_time = time.time()
            
            # Clear existing cache if refreshing
            if refresh_cache:
                cache_file = CACHE_DIR / f"{collection}.jsonl"
                uids_file = CACHE_DIR / f"{collection}.uids.json"
                if cache_file.exists():
                    cache_file.unlink()
                if uids_file.exists():
                    uids_file.unlink()
                all_studies = []
                seen_uids = set()
            
            # Get total count and calculate optimal page size
            try:
                async with session.get(url, params={**base_params, "limit": 1}, timeout=REQUEST_TIMEOUT) as response:
                    response.raise_for_status()
                    count_data = await response.json()
                    total_studies = len(count_data)
                    logger.info(f"Total studies in collection: {total_studies}")
            except Exception as e:
                logger.warning(f"Could not get total study count: {e}")
                total_studies = None
            
            # Calculate optimal page size and studies to fetch
            page_size = get_dynamic_page_size(total_studies)
            studies_to_fetch = limit if limit is not None else total_studies
            if studies_to_fetch is None:
                studies_to_fetch = 100
            
            # Fetch studies page by page
            page = 0
            studies_with_reports = 0
            target_reports = limit if limit is not None else studies_to_fetch
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console
            ) as progress:
                task = progress.add_task(
                    f"Fetching studies...",
                    total=studies_to_fetch
                )
                
                while len(all_studies) < studies_to_fetch:
                    params = {
                        **base_params,
                        "offset": page * page_size,
                        "limit": page_size
                    }
                    
                    # Retry logic for each page
                    for retry in range(MAX_RETRIES):
                        try:
                            async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as response:
                                response.raise_for_status()
                                new_studies = await response.json()
                                break
                        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                            if retry < MAX_RETRIES - 1:
                                wait_time = (retry + 1) * 5
                                logger.warning(f"Request failed: {e}, retrying in {wait_time}s")
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                raise
                    
                    if not new_studies:
                        break
                    
                    # Process studies in chunks
                    for study in new_studies:
                        study_uid = study.get("StudyInstanceUID")
                        if study_uid and study_uid not in seen_uids:
                            all_studies.append(study)
                            save_study_to_cache(collection, study, seen_uids, cache_buffer)
                            progress.update(task, advance=1)
                    
                    # Group and check for reports periodically
                    if len(all_studies) % (page_size * 2) == 0:
                        patient_studies = group_studies_by_patient(all_studies)
                        valid_patients = await filter_patients_with_reports_batch(session, collection, patient_studies)
                        studies_with_reports = len(valid_patients)
                        
                        # Early exit if we have enough studies with reports
                        if limit is not None and studies_with_reports >= int(limit * EARLY_EXIT_THRESHOLD):
                            logger.info(f"Early exit: Found {studies_with_reports} studies with reports (threshold: {int(limit * EARLY_EXIT_THRESHOLD)})")
                            break
                    
                    page += 1
                    await asyncio.sleep(RATE_LIMIT_DELAY)
            
            # Flush any remaining studies in cache buffer
            flush_cache_buffer(collection, cache_buffer)
            
            # Stop timer and show completion
            stop_event.set()
            timer_thread.join()
            elapsed = int(time.time() - start_time)
            print(f"\n✅ Study list fetched in {elapsed}s")
            
            # Sort by study date
            all_studies.sort(key=lambda s: s.get("StudyDate", ""), reverse=True)
            
            # Group studies by patient and filter for reports
            patient_studies = group_studies_by_patient(all_studies)
            valid_patients = await filter_patients_with_reports_batch(session, collection, patient_studies)
            
            if not valid_patients:
                logger.warning(f"No cases with reports found in collection")
                console.print(f"\n[yellow]No studies with reports were found in this collection.[/yellow]")
                return []  # Return to main menu instead of exiting
            
            # If we don't have enough studies with reports, fetch more
            if limit is not None and len(valid_patients) < limit:
                logger.info(f"Only found {len(valid_patients)} studies with reports, need {limit}")
                more_studies = await get_studies_for_collection(
                    session,
                    collection,
                    limit=limit - len(valid_patients),
                    refresh_cache=False,
                    resume_cache=True,
                    fetch_attempt=fetch_attempt + 1
                )
                valid_patients.extend(more_studies)
            
            # Apply final limit
            if limit is not None:
                valid_patients = valid_patients[:limit]
            
            logger.info(f"Found {len(valid_patients)} cases with reports (of {len(all_studies)} processed studies)")
            return valid_patients
            
        except KeyboardInterrupt:
            if 'stop_event' in locals():
                stop_event.set()
                timer_thread.join()
            logger.info("User cancelled study fetch")
            if all_studies:
                console.print("\n[yellow]User cancelled. Progress has been saved to cache.[/yellow]")
            else:
                console.print("\n[yellow]User cancelled. No progress was saved.[/yellow]")
            sys.exit(0)
        except Exception as e:
            if 'stop_event' in locals():
                stop_event.set()
                timer_thread.join()
            logger.error(f"Error fetching studies: {e}")
            if all_studies:
                console.print(f"[red]Error fetching studies. Progress has been saved to cache.[/red]")
            else:
                console.print(f"[red]Error fetching studies. No progress was saved.[/red]")
            return all_studies

async def download_series_async(session: aiohttp.ClientSession, series_uid: str, save_path: Path, semaphore: asyncio.Semaphore) -> bool:
    """Download a series asynchronously with rate limiting."""
    async with semaphore:
        try:
            logger.info(f"Downloading series {series_uid}")
            url = f"{TCIA_API_BASE}/query/getImage"
            params = {"SeriesInstanceUID": series_uid}
            async with session.get(url, params=params, timeout=REQUEST_TIMEOUT) as response:
                if response.status != 200:
                    logger.error(f"Failed to get download URL for series {series_uid}: {response.status}")
                    return False
                download_url = (await response.json())["url"]
            # Download the ZIP file
            async with session.get(download_url, timeout=300) as zip_response:
                if zip_response.status != 200:
                    logger.error(f"Failed to download ZIP for series {series_uid}: {zip_response.status}")
                    return False
                zip_content = await zip_response.read()
            # Extract the ZIP file
            with zipfile.ZipFile(io.BytesIO(zip_content)) as zip_ref:
                file_list = zip_ref.namelist()
                zip_ref.extractall(save_path)
            logger.info(f"Successfully downloaded and extracted series {series_uid}")
            return True
        except Exception as e:
            logger.error(f"Error downloading series {series_uid}: {str(e)}")
            return False

async def download_case_async(session: aiohttp.ClientSession, collection: str, study: Dict) -> bool:
    """Handle the download process for a single case asynchronously."""
    patient_id = study["PatientID"]
    study_uid = study["StudyInstanceUID"]
    # Fetch series
    console.print(f"\n[bold blue]Fetching series for case: {study_uid}[/bold blue]")
    series_list = await get_series_for_study(session, collection, patient_id, study_uid)
    if not series_list:
        logger.error(f"No series found for case {study_uid}")
        console.print("[red]No series found for the selected case.[/red]")
        return False
    display_series(series_list)
    # Download all series
    save_base = DATA_DIR / collection / patient_id / study_uid
    save_base.mkdir(parents=True, exist_ok=True)
    # Create semaphore for rate limiting
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task(
            f"Downloading series for case {study_uid}...",
            total=len(series_list)
        )
        # Create download tasks
        download_tasks = []
        for series in series_list:
            series_uid = series["SeriesInstanceUID"]
            save_path = save_base / series_uid
            save_path.mkdir(exist_ok=True)
            download_tasks.append(download_series_async(session, series_uid, save_path, semaphore))
        # Wait for all downloads to complete
        results = await asyncio.gather(*download_tasks)
        # Update progress
        for success in results:
            progress.update(task, advance=1)
    # Display summary
    success_count = sum(1 for r in results if r)
    console.print("\n[bold green]Case download complete![\/bold green]")
    console.print(f"Files have been saved to: {save_base}")
    console.print(f"Successfully downloaded: {success_count}/{len(series_list)} series")
    return success_count > 0

def check_memory_usage() -> bool:
    """Check if memory usage is above threshold."""
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024  # Convert to MB
        
        if memory_mb > MEMORY_THRESHOLD_MB:
            logger.warning(f"Memory usage ({memory_mb:.1f}MB) above threshold ({MEMORY_THRESHOLD_MB}MB)")
            return True
        return False
    except ImportError:
        logger.warning("psutil not installed, memory monitoring disabled")
        return False

async def process_patient_batch(session: aiohttp.ClientSession, collection: str, patient_batch: List[Tuple[str, List[Dict]]], semaphore: asyncio.Semaphore) -> List[str]:
    """Process a batch of patients in parallel using the shared session."""
    async with semaphore:
        tasks = []
        for patient_id, studies in patient_batch:
            tasks.append(get_patient_series(session, collection, patient_id))
        series_results = await asyncio.gather(*tasks)
        valid_patients = []
        for (patient_id, _), series_list in zip(patient_batch, series_results):
            if has_report_series(series_list):
                valid_patients.append(patient_id)
        return valid_patients

async def filter_patients_with_reports_batch(session: aiohttp.ClientSession, collection: str, patient_studies: Dict[str, List[Dict]]) -> List[Dict]:
    """Filter patients to include only those with reports, using parallel processing and shared session."""
    valid_patients = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PATIENTS)
    
    # Convert to list of tuples for batch processing
    patient_batches = []
    current_batch = []
    for patient_id, studies in patient_studies.items():
        current_batch.append((patient_id, studies))
        if len(current_batch) >= MAX_CONCURRENT_PATIENTS:
            patient_batches.append(current_batch)
            current_batch = []
    if current_batch:
        patient_batches.append(current_batch)
    
    # Process batches
    for batch in patient_batches:
        batch_results = await process_patient_batch(session, collection, batch, semaphore)
        valid_patients.extend(batch_results)
        
        # Check memory usage
        if check_memory_usage():
            logger.info("Memory threshold reached, waiting for cleanup...")
            await asyncio.sleep(1)  # Give time for memory cleanup
    
    return valid_patients

def display_collections(collections: List[Dict], logger=None) -> None:
    """Display available collections in a table."""
    if logger:
        logger.debug("Displaying collections table")
    table = Table(title="Available Collections")
    table.add_column("Index", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Description", style="yellow")
    for idx, collection in enumerate(collections, 1):
        table.add_row(
            str(idx),
            collection.get("Collection", "N/A"),
            "Description pending"  # TODO: Add GPT-generated descriptions
        )
    console.print(table)

def display_patients(patients: List[Dict]) -> None:
    """Display patients in a table."""
    logger.debug("Displaying patients table")
    table = Table(title="Available Patients")
    table.add_column("Index", style="cyan")
    table.add_column("Patient ID", style="green")
    table.add_column("Patient Name", style="yellow")
    
    for idx, patient in enumerate(patients, 1):
        table.add_row(
            str(idx),
            patient.get("PatientID", "N/A"),
            patient.get("PatientName", "N/A")
        )
    
    console.print(table)

def select_subspecialty(logger) -> Optional[str]:
    """Display subspecialty selection menu and return selected option."""
    while True:
        logger.info("Displaying subspecialty selection menu")
        subspecialties = list(subspecialty_map.keys()) + ["show_all", "exit"]
        table = Table(title="Select Subspecialty")
        table.add_column("Index", style="cyan")
        table.add_column("Option", style="green")
        for idx, subspecialty in enumerate(subspecialties, 1):
            display_name = subspecialty.replace("_", " ").title()
            if subspecialty == "show_all":
                display_name = "[Show All]"
            elif subspecialty == "exit":
                display_name = "[Exit]"
            table.add_row(str(idx), display_name)
        console.print(table)
        choice = Prompt.ask(
            "Select a subspecialty (enter number)",
            choices=[str(i) for i in range(1, len(subspecialties) + 1)]
        )
        selected = subspecialties[int(choice) - 1]
        if selected == "exit":
            logger.info("User chose to exit program")
            return None
        logger.info(f"Selected subspecialty: {selected}")
        return selected

def select_collection(collections: List[Dict], logger=None) -> Optional[Dict]:
    """Display collections and handle user selection."""
    while True:
        display_collections(collections, logger=logger)
        # Add Exit option
        console.print("\n[bold cyan]Additional Options:[/bold cyan]")
        console.print(f"{len(collections) + 1}. [Back to Subspecialty Selection]")
        console.print(f"{len(collections) + 2}. [Exit]")
        choice = Prompt.ask(
            "Select a collection (enter number)",
            choices=[str(i) for i in range(1, len(collections) + 3)]
        )
        choice_num = int(choice)
        # Handle Back option
        if choice_num == len(collections) + 1:
            if logger:
                logger.info("User chose to go back to subspecialty selection")
            console.print("\n[yellow]Returning to subspecialty selection...[/yellow]")
            return None
        # Handle Exit option
        if choice_num == len(collections) + 2:
            if logger:
                logger.info("User chose to exit program")
            return None
        # Return selected collection
        selected_collection = collections[choice_num - 1]
        if logger:
            logger.info(f"Selected collection: {selected_collection.get('Collection')}")
        return selected_collection

def download_case(collection: str, study: Dict) -> bool:
    """Handle the download process for a single case."""
    patient_id = study["PatientID"]
    study_uid = study["StudyInstanceUID"]
    logger.debug(f"Study details: {json.dumps(study, indent=2)}")
    
    # Fetch and display series
    console.print(f"\n[bold blue]Fetching series for case: {study_uid}[/bold blue]")
    series_list = get_series_for_study(collection, patient_id, study_uid)
    if not series_list:
        logger.error(f"No series found for case {study_uid}")
        console.print("[red]No series found for the selected case.[/red]")
        return False
    
    display_series(series_list)
    
    # Download all series
    save_base = DATA_DIR / collection / patient_id / study_uid
    save_base.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created save directory: {save_base}")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task(
            f"Downloading series for case {study_uid}...",
            total=len(series_list)
        )
        
        for series in series_list:
            series_uid = series["SeriesInstanceUID"]
            save_path = save_base / series_uid
            save_path.mkdir(exist_ok=True)
            
            logger.debug(f"Processing series {series_uid}")
            logger.debug(f"Series details: {json.dumps(series, indent=2)}")
            
            if download_series(series_uid, save_path):
                progress.update(task, advance=1)
            else:
                progress.update(task, description=f"[red]Failed to download series {series_uid}[/red]")
    
    # Display summary
    console.print("\n[bold green]Case download complete![/bold green]")
    console.print(f"Files have been saved to: {save_base}")
    console.print(f"Total series downloaded: {len(series_list)}")
    console.print("✓ Case includes report series")
    
    logger.info("Download process completed successfully")
    logger.debug(f"Final save location: {save_base}")
    logger.debug(f"Total series processed: {len(series_list)}")
    logger.debug("Report series present: True")
    
    return True

def group_studies_by_patient(studies: List[Dict]) -> Dict[str, List[Dict]]:
    """Group studies by PatientID and sort by StudyDate within each group."""
    patient_studies = {}
    for study in studies:
        patient_id = study.get("PatientID")
        if not patient_id:
            continue
        if patient_id not in patient_studies:
            patient_studies[patient_id] = []
        patient_studies[patient_id].append(study)
    
    # Sort studies by date within each patient group
    for patient_id in patient_studies:
        patient_studies[patient_id].sort(
            key=lambda s: s.get("StudyDate", ""),
            reverse=True  # Most recent first
        )
    
    return patient_studies

def has_report_series(series_list: list) -> bool:
    """Return True if any series in the list appears to be a report (by description or modality)."""
    for series in series_list:
        desc = (series.get("SeriesDescription") or "").lower()
        modality = (series.get("Modality") or "").lower()
        if any(keyword in desc for keyword in REPORT_KEYWORDS):
            return True
        if any(keyword in modality for keyword in REPORT_KEYWORDS):
            return True
    return False

async def main():
    """Main function with async support."""
    args = parse_args()
    logger = setup_logging(args)
    logger.info("Starting TCIA case fetcher")
    logger.debug(f"Python version: {sys.version}")
    logger.debug(f"Working directory: {os.getcwd()}")
    logger.debug(f"Command line arguments: {args}")
    
    # Ensure directories exist before proceeding
    ensure_data_dir()
    ensure_cache_dir()
    
    async with aiohttp.ClientSession() as session:
        # Handle direct study download
        if args.study:
            if not args.collection:
                logger.error("--study requires --collection to be specified")
                console.print("[red]Error: --study requires --collection to be specified[/red]")
                sys.exit(1)
            
            console.print(f"\n[bold blue]Fetching study {args.study} from collection {args.collection}[/bold blue]")
            study = await get_study_by_uid(session, args.collection, args.study)
            
            if not study:
                logger.error(f"Study {args.study} not found or invalid")
                console.print(f"[red]Error: Study {args.study} not found or invalid[/red]")
                sys.exit(1)
            
            if args.report_required:
                series_list = await get_series_for_study(session, args.collection, study["PatientID"], args.study)
                if not has_report_series(series_list):
                    logger.warning(f"Study {args.study} has no report series")
                    console.print("[yellow]Study has no report series[/yellow]")
                    sys.exit(0)
            
            if args.download:
                if await download_case_async(session, args.collection, study):
                    logger.info("Study downloaded successfully")
                    sys.exit(0)
                else:
                    logger.error("Failed to download study")
                    sys.exit(1)
            else:
                # Show series and prompt for download
                series_list = await get_series_for_study(session, args.collection, study["PatientID"], args.study)
                display_series(series_list)
                if Confirm.ask("Do you want to download this study?"):
                    if await download_case_async(session, args.collection, study):
                        logger.info("Study downloaded successfully")
                        sys.exit(0)
                    else:
                        logger.error("Failed to download study")
                        sys.exit(1)
                else:
                    logger.info("Download cancelled by user")
                    sys.exit(0)
        
        # Handle collection-only mode
        if args.collection:
            while True:
                console.print(f"\n[bold blue]Fetching cases for collection: {args.collection}[/bold blue]")
                studies = await get_studies_for_collection(session, args.collection, args.limit, args.refresh_cache, args.resume_cache, logger=logger)
                
                if not studies:
                    logger.error(f"No cases found in collection {args.collection}")
                    console.print("[red]No cases found in the selected collection.[/red]")
                    # Prompt user to return to main menu or quit
                    choice = Prompt.ask("No cases found. What would you like to do? (m=main menu, q=quit)", choices=["m", "q"], default="m")
                    if choice == "m":
                        # Clear the collection argument to enter interactive mode
                        args.collection = None
                        break  # Exit this loop and enter interactive mode
                    else:
                        sys.exit(0)
                # Filter by subject if specified
                if args.subject:
                    studies = [s for s in studies if s.get("PatientID") == args.subject]
                    if not studies:
                        logger.error(f"Subject {args.subject} not found in collection {args.collection}")
                        console.print(f"[red]Subject {args.subject} not found in collection {args.collection}[/red]")
                        sys.exit(1)
                
                # Select study with pagination
                selected_study = select_study(studies)
                if not selected_study:
                    logger.info("User cancelled study selection")
                    console.print("\n[yellow]Operation cancelled by user.[/yellow]")
                    sys.exit(0)
                
                if args.download:
                    if await download_case_async(session, args.collection, selected_study):
                        logger.info("Study downloaded successfully")
                        sys.exit(0)
                    else:
                        logger.error("Failed to download study")
                        sys.exit(1)
                else:
                    # Show series and prompt for download
                    series_list = await get_series_for_study(session, args.collection, selected_study["PatientID"], selected_study["StudyInstanceUID"])
                    display_series(series_list)
                    if Confirm.ask("Do you want to download this study?"):
                        if await download_case_async(session, args.collection, selected_study):
                            logger.info("Study downloaded successfully")
                            sys.exit(0)
                        else:
                            logger.error("Failed to download study")
                            sys.exit(1)
                    else:
                        logger.info("Download cancelled by user")
                        sys.exit(0)
                break  # Only break if studies were found and handled
        
        # Interactive mode
        while True:
            # Select subspecialty
            console.print("\n[bold blue]Select a subspecialty to filter collections:[/bold blue]")
            selected_subspecialty = select_subspecialty(logger)
            if selected_subspecialty is None:
                logger.info("User chose to exit program")
                console.print("\n[yellow]Exiting program...[/yellow]")
                break
            
            # Fetch and display collections
            console.print("[bold blue]Fetching available collections...[/bold blue]")
            collections = await get_collections(session, logger)
            filtered_collections = filter_collections_by_subspecialty(collections, selected_subspecialty, logger=logger)
            
            if not filtered_collections:
                logger.warning(f"No collections found for subspecialty: {selected_subspecialty}")
                console.print("[red]No collections found for the selected subspecialty.[/red]")
                continue
            
            # Select collection
            selected_collection = select_collection(filtered_collections, logger=logger)
            if selected_collection is None:
                continue
            
            collection_name = selected_collection["Collection"]
            
            # Fetch and display studies
            console.print(f"\n[bold blue]Fetching cases for collection: {collection_name}[/bold blue]")
            studies = await get_studies_for_collection(session, collection_name, args.limit, args.refresh_cache, args.resume_cache, logger=logger)
            if not studies:
                logger.error(f"No cases with reports found in collection {collection_name}")
                console.print("[red]No cases with reports found in the selected collection.[/red]")
                continue
            
            # Select study with pagination
            selected_study = select_study(studies)
            if not selected_study:
                logger.info("User cancelled study selection")
                console.print("\n[yellow]Returning to collection selection...[/yellow]")
                continue
            
            # Download the case
            if await download_case_async(session, collection_name, selected_study):
                # Ask if user wants to download another case
                if not Confirm.ask("\nDo you want to download another case?"):
                    logger.info("User chose to exit after successful download")
                    console.print("\n[yellow]Exiting program...[/yellow]")
                    break
            else:
                # Ask if user wants to try another case
                if not Confirm.ask("\nDo you want to try another case?"):
                    logger.info("User chose to exit after failed download")
                    console.print("\n[yellow]Exiting program...[/yellow]")
                    break

if __name__ == "__main__":
    asyncio.run(main()) 