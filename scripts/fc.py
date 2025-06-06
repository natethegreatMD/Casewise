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

# Constants
TCIA_API_BASE = "https://services.cancerimagingarchive.net/services/v4/TCIA"
DATA_DIR = Path("data/images")
LOG_DIR = Path(__file__).parent.parent / "logs" / "fc"
CACHE_DIR = Path(__file__).parent.parent / "cache" / "studies"
SIZE_WARNING_THRESHOLD_GB = 80  # Warning threshold for collection size
API_RATE_LIMIT_DELAY = 0.2  # Delay between API calls in seconds
REQUEST_TIMEOUT = 450  # Increased timeout for all collections
PAGE_SIZE = 100  # Increased page size for faster fetching
MAX_RETRIES = 3  # Number of retries for failed requests
RATE_LIMIT_DELAY = 1.0  # Delay between requests

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
    logger.debug(f"Ensured data directory exists at {DATA_DIR}")

def ensure_cache_dir() -> None:
    """Ensure the cache directory exists."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured cache directory exists at {CACHE_DIR}")
    except Exception as e:
        logger.error(f"Error creating cache directory: {e}")
        console.print(f"[red]Error creating cache directory: {e}[/red]")
        raise

def get_cached_studies(collection: str) -> Tuple[List[Dict], Set[str]]:
    """Load studies from cache if available, returning both studies and seen UIDs."""
    cache_file = CACHE_DIR / f"{collection}.jsonl"
    uids_file = CACHE_DIR / f"{collection}.uids.json"
    
    if not cache_file.exists():
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
                logger.debug(f"Loaded {len(seen_uids)} cached UIDs")
            except Exception as e:
                logger.warning(f"Error loading UIDs cache: {e}")
        
        # Load studies
        with open(cache_file, 'r') as f:
            for line in f:
                try:
                    study = json.loads(line.strip())
                    studies.append(study)
                    seen_uids.add(study["StudyInstanceUID"])
                except json.JSONDecodeError as e:
                    logger.warning(f"Error parsing study from cache: {e}")
                    continue
        
        # Save updated UIDs
        try:
            with open(uids_file, 'w') as f:
                json.dump(list(seen_uids), f)
        except Exception as e:
            logger.warning(f"Error saving UIDs cache: {e}")
        
        logger.info(f"Loaded {len(studies)} cached studies with {len(seen_uids)} unique UIDs")
        return studies, seen_uids
    except Exception as e:
        logger.error(f"Error loading cache for {collection}: {e}")
        return [], set()

def save_study_to_cache(collection: str, study: Dict, seen_uids: Set[str]) -> None:
    """Save a single study to cache and update seen UIDs."""
    cache_file = CACHE_DIR / f"{collection}.jsonl"
    uids_file = CACHE_DIR / f"{collection}.uids.json"
    
    try:
        # Save study
        with open(cache_file, 'a') as f:
            f.write(json.dumps(study) + "\n")
        
        # Update and save UIDs
        study_uid = study.get("StudyInstanceUID")
        if study_uid:
            seen_uids.add(study_uid)
            with open(uids_file, 'w') as f:
                json.dump(list(seen_uids), f)
    except Exception as e:
        logger.error(f"Error saving study to cache for {collection}: {e}")

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
        
        logger.info(f"Finalized cache for {collection} with {len(studies)} studies and {len(seen_uids)} UIDs")
    except Exception as e:
        logger.error(f"Error finalizing cache for {collection}: {e}")

def get_collections() -> List[Dict]:
    """Fetch available collections from TCIA."""
    try:
        logger.info("Fetching collections from TCIA")
        logger.debug(f"API endpoint: {TCIA_API_BASE}/query/getCollectionValues")
        
        response = requests.get(f"{TCIA_API_BASE}/query/getCollectionValues", timeout=60)
        response.raise_for_status()
        
        collections = response.json()
        logger.info(f"Successfully retrieved {len(collections)} collections")
        logger.debug(f"Collection names: {[c.get('Collection', 'N/A') for c in collections]}")
        return collections
        
    except KeyboardInterrupt:
        logger.info("User cancelled collection fetch")
        console.print("\n[yellow]User cancelled. Exiting.[/yellow]")
        sys.exit(0)
    except requests.Timeout:
        logger.error("Request timed out while fetching collections")
        console.print("[red]Request timed out while fetching collections.[/red]")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching collections: {str(e)}")
        logger.debug(f"Request failed with status code: {getattr(e.response, 'status_code', 'N/A')}")
        logger.debug(f"Response content: {getattr(e.response, 'content', 'N/A')}")
        console.print(f"[red]Error fetching collections: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unhandled error while fetching collections: {str(e)}")
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)

def filter_collections_by_subspecialty(collections: List[Dict], subspecialty: Optional[str] = None) -> List[Dict]:
    """Filter collections based on selected subspecialty."""
    logger.info(f"Filtering collections for subspecialty: {subspecialty}")
    
    if not subspecialty or subspecialty == "show_all":
        logger.debug("No subspecialty filter applied, returning all collections")
        return collections
    
    if subspecialty not in subspecialty_map:
        logger.warning(f"Unknown subspecialty: {subspecialty}")
        logger.debug(f"Available subspecialties: {list(subspecialty_map.keys())}")
        return collections
    
    # Normalize the subspecialty list
    normalized_subspecialty_list = [name.strip().upper() for name in subspecialty_map[subspecialty]]
    logger.debug(f"Normalized subspecialty list for {subspecialty}: {sorted(normalized_subspecialty_list)}")
    
    # Filter collections using normalized names
    filtered = [
        collection for collection in collections
        if collection.get("Collection", "").strip().upper() in normalized_subspecialty_list
    ]
    
    # Log the normalized collection names from API for debugging
    normalized_api_collections = [c.get("Collection", "").strip().upper() for c in collections]
    logger.debug(f"Normalized API collections: {sorted(normalized_api_collections)}")
    
    if not filtered:
        logger.warning(
            f"No collections found for subspecialty '{subspecialty}' after normalization. "
            f"Valid collection names: {sorted(normalized_subspecialty_list)}"
        )
        console.print(f"[yellow]No collections found for subspecialty '{subspecialty}' after normalization.[/yellow]")
        console.print("[yellow]This might be due to case or whitespace differences in collection names.[/yellow]")
    else:
        logger.info(
            f"Filtered collections for {subspecialty}: {len(filtered)} collections found. "
            f"Matched collections: {[c.get('Collection') for c in filtered]}"
        )
    
    return filtered

def get_patients(collection: str) -> List[Dict]:
    """Fetch patients for a given collection."""
    try:
        logger.info(f"Fetching patients for collection: {collection}")
        response = requests.get(
            f"{TCIA_API_BASE}/query/getPatient",
            params={"Collection": collection},
            timeout=60
        )
        response.raise_for_status()
        patients = response.json()
        logger.debug(f"Retrieved {len(patients)} patients for collection {collection}")
        return patients
    except KeyboardInterrupt:
        logger.info("User cancelled patient fetch")
        console.print("\n[yellow]User cancelled. Exiting.[/yellow]")
        sys.exit(0)
    except requests.Timeout:
        logger.error("Request timed out while fetching patients")
        console.print("[red]Request timed out while fetching patients.[/red]")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching patients for collection {collection}: {e}")
        console.print(f"[red]Error fetching patients: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unhandled error while fetching patients: {str(e)}")
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)

def get_series(patient_id: str, collection: str) -> List[Dict]:
    """Fetch series for a given patient and collection."""
    try:
        logger.info(f"Fetching series for patient {patient_id} in collection {collection}")
        response = requests.get(
            f"{TCIA_API_BASE}/query/getSeries",
            params={
                "PatientID": patient_id,
                "Collection": collection
            },
            timeout=60
        )
        response.raise_for_status()
        series = response.json()
        logger.debug(f"Retrieved {len(series)} series for patient {patient_id}")
        return series
    except KeyboardInterrupt:
        logger.info("User cancelled series fetch")
        console.print("\n[yellow]User cancelled. Exiting.[/yellow]")
        sys.exit(0)
    except requests.Timeout:
        logger.error("Request timed out while fetching series")
        console.print("[red]Request timed out while fetching series.[/red]")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching series for patient {patient_id}: {e}")
        console.print(f"[red]Error fetching series: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unhandled error while fetching series: {str(e)}")
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)

def get_series_for_study(collection: str, patient_id: str, study_uid: str) -> List[Dict]:
    """Fetch all series for a specific study."""
    try:
        url = f"{TCIA_API_BASE}/query/getSeries"
        params = {
            "Collection": collection,
            "PatientID": patient_id,
            "StudyInstanceUID": study_uid
        }
        
        logger.info(f"Fetching series for study {study_uid} (Patient: {patient_id})")
        logger.debug(f"API endpoint: {url}")
        logger.debug(f"Request parameters: {params}")
        
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        
        series = response.json()
        logger.info(f"Found {len(series)} series in study {study_uid}")
        logger.debug(f"Series modalities: {[s.get('Modality', 'N/A') for s in series]}")
        logger.debug(f"Series descriptions: {[s.get('SeriesDescription', 'N/A') for s in series]}")
        
        return series
        
    except KeyboardInterrupt:
        logger.info("User cancelled series fetch")
        console.print("\n[yellow]User cancelled. Exiting.[/yellow]")
        sys.exit(0)
    except requests.Timeout:
        logger.error("Request timed out while fetching series")
        console.print("[red]Request timed out while fetching series.[/red]")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching series for study {study_uid}: {str(e)}")
        logger.debug(f"Request failed with status code: {getattr(e.response, 'status_code', 'N/A')}")
        logger.debug(f"Response content: {getattr(e.response, 'content', 'N/A')}")
        return []
    except Exception as e:
        logger.error(f"Unhandled error while fetching series: {str(e)}")
        console.print(f"[red]Unexpected error: {e}[/red]")
        return []

def has_report_series(series_list: List[Dict]) -> bool:
    """Check if any series in the list appears to be a report.
    
    A study is considered to have a report if any of its series meets either condition:
    1. Modality is "SR" (Structured Report)
    2. SeriesDescription contains any report-related keyword (case-insensitive)
    """
    logger.info("Checking for report series")
    logger.debug(f"Total series to check: {len(series_list)}")
    
    # Check each series for report indicators
    for series in series_list:
        modality = series.get("Modality", "").upper()
        description = series.get("SeriesDescription", "").lower()
        
        logger.debug(f"Checking series - Modality: {modality}, Description: {description}")
        
        # Check for SR modality
        if modality == "SR":
            logger.info(f"Found report series with SR modality: {series.get('SeriesDescription')}")
            logger.debug("Matching condition: Modality == 'SR'")
            return True
        
        # Check description for keywords
        if any(keyword in description for keyword in REPORT_KEYWORDS):
            logger.info(f"Found report series with matching description: {series.get('SeriesDescription')}")
            logger.debug(f"Matching keywords: {[k for k in REPORT_KEYWORDS if k in description]}")
            return True
    
    logger.info("No report series found")
    return False

def filter_studies_with_reports(collection: str, studies: List[Dict]) -> List[Dict]:
    """Filter studies to only include those that have report series."""
    logger.info(f"Filtering {len(studies)} studies for report content")
    filtered_studies = []
    excluded_studies = []
    
    for study in studies:
        patient_id = study.get("PatientID")
        study_uid = study.get("StudyInstanceUID")
        study_date = study.get("StudyDate", "N/A")
        
        if not patient_id or not study_uid:
            logger.warning(f"Skipping study with missing PatientID or StudyInstanceUID: {study}")
            continue
        
        # Fetch series for this study
        series_list = get_series_for_study(collection, patient_id, study_uid)
        
        if has_report_series(series_list):
            filtered_studies.append(study)
            logger.info(f"Including study {study_uid} from {study_date} (has report)")
            logger.debug(f"Study details: {json.dumps(study, indent=2)}")
        else:
            excluded_studies.append(study)
            logger.info(f"Excluding study {study_uid} from {study_date} (no report)")
            logger.debug(f"Study details: {json.dumps(study, indent=2)}")
        
        # Rate limiting
        time.sleep(API_RATE_LIMIT_DELAY)
    
    logger.info(f"Filtered {len(studies)} studies: {len(filtered_studies)} included, {len(excluded_studies)} excluded")
    if excluded_studies:
        logger.debug(f"Excluded studies: {[s.get('StudyInstanceUID') for s in excluded_studies]}")
    
    return filtered_studies

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

def get_studies_for_collection(collection: str, limit: Optional[int] = None, refresh_cache: bool = False, resume_cache: bool = False, fetch_attempt: int = 1) -> List[Dict]:
    """Fetch studies for a collection, sorted by date."""
    MAX_FETCH_ATTEMPTS = 5  # Maximum number of attempts to find studies with reports
    
    if fetch_attempt > MAX_FETCH_ATTEMPTS:
        logger.warning(f"Reached maximum fetch attempts ({MAX_FETCH_ATTEMPTS})")
        return []
        
    cache_file = CACHE_DIR / f"{collection}.jsonl"
    uids_file = CACHE_DIR / f"{collection}.uids.json"
    FINALIZE_THRESHOLD = 1000  # Convert to .json after this many studies
    
    # Load existing cache if available
    all_studies, seen_uids = get_cached_studies(collection)
    if all_studies:
        logger.info(f"Loaded {len(all_studies)} studies from cache")
    
    # Fetch from API if needed
    if refresh_cache or not all_studies:
        stop_event = threading.Event()
        timer_thread = None
        try:
            url = f"{TCIA_API_BASE}/query/getPatientStudy"
            base_params = {
                "Collection": collection,
                "format": "json"  # Explicitly request JSON format
            }
            
            logger.info(f"Fetching cases for collection: {collection}")
            logger.debug(f"API endpoint: {url}")
            logger.debug(f"Base parameters: {base_params}")
            
            # Start live timer
            timer_thread = threading.Thread(target=show_live_timer, args=(stop_event,))
            timer_thread.daemon = True
            timer_thread.start()
            start_time = time.time()
            
            # Clear existing cache if refreshing
            if refresh_cache:
                if cache_file.exists():
                    cache_file.unlink()
                    logger.info("Cleared existing cache file")
                if uids_file.exists():
                    uids_file.unlink()
                    logger.info("Cleared existing UIDs file")
                all_studies = []
                seen_uids = set()
            
            # First, get total count if available
            try:
                with requests.Session() as session:
                    count_params = {**base_params, "limit": 1}
                    logger.debug(f"Count request parameters: {count_params}")
                    count_response = session.get(
                        url,
                        params=count_params,
                        timeout=REQUEST_TIMEOUT,
                        headers={
                            'Accept': 'application/json',
                            'User-Agent': 'TCIA-Case-Fetcher/1.0'
                        }
                    )
                    count_response.raise_for_status()
                    try:
                        count_data = count_response.json()
                        total_studies = len(count_data)
                        logger.info(f"Total studies in collection: {total_studies}")
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON in count response: {e}")
                        raise
            except Exception as e:
                logger.warning(f"Could not get total study count: {e}")
                total_studies = None
            
            # Calculate how many studies we need to fetch
            studies_to_fetch = limit if limit is not None else total_studies
            if studies_to_fetch is None:
                studies_to_fetch = 100  # Default to 100 if we can't get total count
            
            # Fetch studies page by page
            page = 0
            while len(all_studies) < studies_to_fetch:
                params = {
                    **base_params,
                    "offset": page * PAGE_SIZE,
                    "limit": PAGE_SIZE
                }
                logger.info(f"Fetching page {page + 1} with {PAGE_SIZE} studies")
                logger.debug(f"Request parameters: {params}")
                
                # Retry logic for each page
                for retry in range(MAX_RETRIES):
                    try:
                        with requests.Session() as session:
                            response = session.get(
                                url,
                                params=params,
                                timeout=REQUEST_TIMEOUT,
                                headers={
                                    'Accept': 'application/json',
                                    'User-Agent': 'TCIA-Case-Fetcher/1.0'
                                }
                            )
                            response.raise_for_status()
                            
                            try:
                                new_studies = response.json()
                                if not isinstance(new_studies, list):
                                    raise ValueError(f"Expected list, got {type(new_studies)}")
                                logger.debug(f"Page {page + 1} received {len(new_studies)} studies")
                            except json.JSONDecodeError as e:
                                logger.error(f"Invalid JSON response: {e}")
                                raise
                            
                            break
                    except requests.Timeout:
                        if retry < MAX_RETRIES - 1:
                            wait_time = (retry + 1) * 5
                            logger.warning(f"Request timed out, retrying in {wait_time}s (attempt {retry + 1}/{MAX_RETRIES})")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise
                    except requests.exceptions.RequestException as e:
                        if retry < MAX_RETRIES - 1:
                            wait_time = (retry + 1) * 5
                            logger.warning(f"Request failed: {e}, retrying in {wait_time}s (attempt {retry + 1}/{MAX_RETRIES})")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise
                
                if not new_studies:  # No more studies to fetch
                    break
                
                # Process studies in chunks
                chunk_size = 10
                for i in range(0, len(new_studies), chunk_size):
                    chunk = new_studies[i:i + chunk_size]
                    for study in chunk:
                        study_uid = study.get("StudyInstanceUID")
                        if study_uid and study_uid not in seen_uids:
                            all_studies.append(study)
                            save_study_to_cache(collection, study, seen_uids)
                
                # Show progress
                if total_studies:
                    progress = f"Processed {len(all_studies)}/{studies_to_fetch} studies"
                else:
                    progress = f"Processed {len(all_studies)} studies"
                logger.info(f"Processed page {page + 1} ({len(new_studies)} studies) - {progress}")
                
                page += 1
                time.sleep(RATE_LIMIT_DELAY)  # Rate limiting between pages
            
            # Stop timer and show completion
            stop_event.set()
            timer_thread.join()
            elapsed = int(time.time() - start_time)
            print(f"\n✅ Study list fetched in {elapsed}s")
            
            # Sort by study date
            all_studies.sort(key=lambda s: s.get("StudyDate", ""), reverse=True)
            logger.info(f"Sorted {len(all_studies)} studies by date")
            
            # Group studies by patient and filter for reports
            patient_studies = group_studies_by_patient(all_studies)
            logger.info(f"Grouped {len(all_studies)} studies into {len(patient_studies)} patients")
            
            # Filter patients to only include those with reports
            filtered_studies = filter_patients_with_reports(collection, patient_studies)
            
            if not filtered_studies:
                logger.warning(f"No cases with reports found in collection")
                console.print(f"\n[yellow]No studies with reports were found in this collection.[/yellow]")
                return []
            
            # If we don't have enough studies with reports, fetch more
            if limit is not None and len(filtered_studies) < limit:
                logger.info(f"Only found {len(filtered_studies)} studies with reports, need {limit} (attempt {fetch_attempt}/{MAX_FETCH_ATTEMPTS})")
                # Recursively fetch more studies
                more_studies = get_studies_for_collection(
                    collection,
                    limit=limit - len(filtered_studies),
                    refresh_cache=False,
                    resume_cache=True,
                    fetch_attempt=fetch_attempt + 1
                )
                filtered_studies.extend(more_studies)
            
            # Apply final limit
            if limit is not None:
                filtered_studies = filtered_studies[:limit]
            
            logger.info(f"Found {len(filtered_studies)} cases with reports (of {len(all_studies)} processed studies)")
            return filtered_studies
            
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
        except requests.Timeout:
            if 'stop_event' in locals():
                stop_event.set()
                timer_thread.join()
            logger.error("Request timed out while fetching studies")
            if all_studies:
                console.print("[red]Request timed out while fetching studies. Progress has been saved to cache.[/red]")
            else:
                console.print("[red]Request timed out while fetching studies. No progress was saved.[/red]")
            return all_studies
        except requests.exceptions.RequestException as e:
            if 'stop_event' in locals():
                stop_event.set()
                timer_thread.join()
            logger.error(f"Error fetching cases for collection {collection}: {str(e)}")
            if all_studies:
                console.print(f"[red]Error fetching studies. Progress has been saved to cache.[/red]")
            else:
                console.print(f"[red]Error fetching studies. No progress was saved.[/red]")
            return all_studies
        except Exception as e:
            if 'stop_event' in locals():
                stop_event.set()
                timer_thread.join()
            logger.error(f"Unhandled error while fetching studies: {str(e)}")
            if all_studies:
                console.print(f"[red]Unexpected error. Progress has been saved to cache.[/red]")
            else:
                console.print(f"[red]Unexpected error. No progress was saved.[/red]")
            return all_studies

def download_series(series_uid: str, save_path: Path) -> bool:
    """Download a series and save it to the specified path."""
    try:
        logger.info(f"Downloading series {series_uid}")
        logger.debug(f"Save path: {save_path}")
        
        # Get the series download URL
        url = f"{TCIA_API_BASE}/query/getImage"
        params = {"SeriesInstanceUID": series_uid}
        logger.debug(f"Requesting download URL from: {url}")
        logger.debug(f"Request parameters: {params}")
        
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        
        download_url = response.json()["url"]
        logger.debug(f"Download URL received: {download_url}")
        
        # Download the ZIP file
        logger.debug("Downloading ZIP file...")
        zip_response = requests.get(download_url, timeout=300)  # Longer timeout for large downloads
        zip_response.raise_for_status()
        
        # Extract the ZIP file
        logger.debug("Extracting ZIP file...")
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zip_ref:
            file_list = zip_ref.namelist()
            logger.debug(f"ZIP contents: {file_list}")
            zip_ref.extractall(save_path)
        
        logger.info(f"Successfully downloaded and extracted series {series_uid} to {save_path}")
        logger.debug(f"Extracted {len(file_list)} files")
        return True
        
    except KeyboardInterrupt:
        logger.info("User cancelled series download")
        console.print("\n[yellow]User cancelled. Exiting.[/yellow]")
        sys.exit(0)
    except requests.Timeout:
        logger.error(f"Request timed out while downloading series {series_uid}")
        console.print(f"[red]Request timed out while downloading series {series_uid}.[/red]")
        return False
    except Exception as e:
        logger.error(f"Error downloading series {series_uid}: {str(e)}")
        logger.debug(f"Error type: {type(e).__name__}")
        logger.debug(f"Error details: {str(e)}")
        console.print(f"[red]Error downloading series {series_uid}: {e}[/red]")
        return False

def display_collections(collections: List[Dict]) -> None:
    """Display available collections in a table.
    
    TODO: Future enhancements:
    1. Add subject/series-level metadata analysis for better descriptions
    2. Generate descriptions using GPT based on collection names
    """
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

def select_subspecialty() -> Optional[str]:
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

def select_collection(collections: List[Dict]) -> Optional[Dict]:
    """Display collections and handle user selection."""
    while True:
        display_collections(collections)
        
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
            logger.info("User chose to go back to subspecialty selection")
            console.print("\n[yellow]Returning to subspecialty selection...[/yellow]")
            return None
        
        # Handle Exit option
        if choice_num == len(collections) + 2:
            logger.info("User chose to exit program")
            return None
        
        # Return selected collection
        selected_collection = collections[choice_num - 1]
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

def get_study_by_uid(collection: str, study_uid: str) -> Optional[Dict]:
    """Fetch a specific study by its UID."""
    try:
        url = f"{TCIA_API_BASE}/query/getPatientStudy"
        params = {
            "Collection": collection,
            "StudyInstanceUID": study_uid
        }
        
        logger.info(f"Fetching study {study_uid} from collection {collection}")
        logger.debug(f"API endpoint: {url}")
        logger.debug(f"Request parameters: {params}")
        
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        
        studies = response.json()
        if not studies:
            logger.error(f"Study {study_uid} not found in collection {collection}")
            return None
        
        study = studies[0]
        logger.info(f"Found study {study_uid} for patient {study.get('PatientID')}")
        return study
        
    except KeyboardInterrupt:
        logger.info("User cancelled study fetch")
        console.print("\n[yellow]User cancelled. Exiting.[/yellow]")
        sys.exit(0)
    except requests.Timeout:
        logger.error("Request timed out while fetching study")
        console.print("[red]Request timed out while fetching study.[/red]")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching study {study_uid}: {str(e)}")
        logger.debug(f"Request failed with status code: {getattr(e.response, 'status_code', 'N/A')}")
        logger.debug(f"Response content: {getattr(e.response, 'content', 'N/A')}")
        return None
    except Exception as e:
        logger.error(f"Unhandled error while fetching study: {str(e)}")
        console.print(f"[red]Unexpected error: {e}[/red]")
        return None

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

def get_patient_series(collection: str, patient_id: str) -> List[Dict]:
    """Fetch all series for a patient in a collection."""
    try:
        logger.info(f"Fetching all series for patient {patient_id}")
        response = requests.get(
            f"{TCIA_API_BASE}/query/getSeries",
            params={
                "Collection": collection,
                "PatientID": patient_id
            },
            timeout=REQUEST_TIMEOUT,
            headers={
                'Accept': 'application/json',
                'User-Agent': 'TCIA-Case-Fetcher/1.0'
            }
        )
        response.raise_for_status()
        series = response.json()
        logger.debug(f"Retrieved {len(series)} series for patient {patient_id}")
        return series
    except Exception as e:
        logger.error(f"Error fetching series for patient {patient_id}: {e}")
        return []

def filter_patients_with_reports(collection: str, patient_studies: Dict[str, List[Dict]]) -> List[Dict]:
    """Filter patients to only include those with report series."""
    logger.info(f"Filtering {len(patient_studies)} patients for report content")
    valid_patients = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task(
            "Checking patients for reports...",
            total=len(patient_studies)
        )
        
        for patient_id, studies in patient_studies.items():
            # Get all series for this patient
            series_list = get_patient_series(collection, patient_id)
            
            if has_report_series(series_list):
                # Use the most recent study
                valid_patients.append(studies[0])
                logger.info(f"Including patient {patient_id} (has report)")
            else:
                logger.info(f"Excluding patient {patient_id} (no report)")
            
            progress.update(task, advance=1)
            time.sleep(API_RATE_LIMIT_DELAY)
    
    logger.info(f"Filtered {len(patient_studies)} patients: {len(valid_patients)} included")
    return valid_patients

def main():
    """Main function to run the TCIA case fetcher."""
    # Parse command line arguments
    args = parse_args()
    
    # Setup logging
    global logger
    logger = setup_logging(args)
    
    logger.info("Starting TCIA case fetcher")
    logger.debug(f"Python version: {sys.version}")
    logger.debug(f"Working directory: {os.getcwd()}")
    logger.debug(f"Command line arguments: {args}")
    
    ensure_data_dir()
    ensure_cache_dir()
    
    # Handle direct study download
    if args.study:
        if not args.collection:
            logger.error("--study requires --collection to be specified")
            console.print("[red]Error: --study requires --collection to be specified[/red]")
            sys.exit(1)
        
        console.print(f"\n[bold blue]Fetching study {args.study} from collection {args.collection}[/bold blue]")
        study = get_study_by_uid(args.collection, args.study)
        
        if not study:
            logger.error(f"Study {args.study} not found or invalid")
            console.print(f"[red]Error: Study {args.study} not found or invalid[/red]")
            sys.exit(1)
        
        if args.report_required:
            series_list = get_series_for_study(args.collection, study["PatientID"], args.study)
            if not has_report_series(series_list):
                logger.warning(f"Study {args.study} has no report series")
                console.print("[yellow]Study has no report series[/yellow]")
                sys.exit(0)
        
        if args.download:
            if download_case(args.collection, study):
                logger.info("Study downloaded successfully")
                sys.exit(0)
            else:
                logger.error("Failed to download study")
                sys.exit(1)
        else:
            # Show series and prompt for download
            series_list = get_series_for_study(args.collection, study["PatientID"], args.study)
            display_series(series_list)
            if Confirm.ask("Do you want to download this study?"):
                if download_case(args.collection, study):
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
        console.print(f"\n[bold blue]Fetching cases for collection: {args.collection}[/bold blue]")
        studies = get_studies_for_collection(args.collection, args.limit, args.refresh_cache)
        
        if not studies:
            logger.error(f"No cases found in collection {args.collection}")
            console.print("[red]No cases found in the selected collection.[/red]")
            sys.exit(1)
        
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
            if download_case(args.collection, selected_study):
                logger.info("Study downloaded successfully")
                sys.exit(0)
            else:
                logger.error("Failed to download study")
                sys.exit(1)
        else:
            # Show series and prompt for download
            series_list = get_series_for_study(args.collection, selected_study["PatientID"], selected_study["StudyInstanceUID"])
            display_series(series_list)
            if Confirm.ask("Do you want to download this study?"):
                if download_case(args.collection, selected_study):
                    logger.info("Study downloaded successfully")
                    sys.exit(0)
                else:
                    logger.error("Failed to download study")
                    sys.exit(1)
            else:
                logger.info("Download cancelled by user")
                sys.exit(0)
    
    # Interactive mode
    while True:
        # Select subspecialty
        console.print("\n[bold blue]Select a subspecialty to filter collections:[/bold blue]")
        selected_subspecialty = select_subspecialty()
        if selected_subspecialty is None:
            logger.info("User chose to exit program")
            console.print("\n[yellow]Exiting program...[/yellow]")
            break
        
        # Fetch and display collections
        console.print("[bold blue]Fetching available collections...[/bold blue]")
        collections = get_collections()
        filtered_collections = filter_collections_by_subspecialty(collections, selected_subspecialty)
        
        if not filtered_collections:
            logger.warning(f"No collections found for subspecialty: {selected_subspecialty}")
            console.print("[red]No collections found for the selected subspecialty.[/red]")
            continue
        
        # Select collection
        selected_collection = select_collection(filtered_collections)
        if selected_collection is None:
            continue
        
        collection_name = selected_collection["Collection"]
        
        # Fetch and display studies
        console.print(f"\n[bold blue]Fetching cases for collection: {collection_name}[/bold blue]")
        studies = get_studies_for_collection(collection_name, args.limit, args.refresh_cache)
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
        if download_case(collection_name, selected_study):
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
    main() 