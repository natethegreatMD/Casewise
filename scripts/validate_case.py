#!/usr/bin/env python3

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Valid subspecialties from SUBSPECIALTY_LIST.md
VALID_SUBSPECIALTIES = {
    "abdominal",
    "breast",
    "cardiothoracic",
    "musculoskeletal",
    "neuroradiology",
    "nuclear",
    "pediatric"
}

class CaseValidationError(Exception):
    """Custom exception for case validation errors."""
    pass

def load_subspecialty_list() -> set:
    """Load valid subspecialties from SUBSPECIALTY_LIST.md."""
    try:
        subspecialty_file = Path("docs/SUBSPECIALTY_LIST.md")
        if not subspecialty_file.exists():
            raise CaseValidationError("SUBSPECIALTY_LIST.md not found")
        
        # For now, we'll use the hardcoded list
        # In the future, we could parse the markdown file
        return VALID_SUBSPECIALTIES
    except Exception as e:
        raise CaseValidationError(f"Error loading subspecialty list: {str(e)}")

def validate_subspecialty(case_data: Dict) -> None:
    """Validate the subspecialty field if present."""
    if "subspecialty" in case_data:
        subspecialty = case_data["subspecialty"]
        if not isinstance(subspecialty, str):
            raise CaseValidationError("subspecialty must be a string")
        
        valid_subspecialties = load_subspecialty_list()
        if subspecialty not in valid_subspecialties:
            raise CaseValidationError(
                f"Invalid subspecialty: {subspecialty}. "
                f"Must be one of: {', '.join(sorted(valid_subspecialties))}"
            )

def validate_case(case_path: str) -> None:
    """Validate a case's metadata and structure."""
    try:
        # Check if case directory exists
        case_dir = Path(case_path)
        if not case_dir.exists():
            raise CaseValidationError(f"Case directory not found: {case_path}")

        # Check for case.json
        case_json = case_dir / "case.json"
        if not case_json.exists():
            raise CaseValidationError("case.json not found")

        # Load and validate case.json
        with open(case_json, 'r') as f:
            case_data = json.load(f)

        # Validate required fields
        required_fields = {
            "case_id", "title", "difficulty", "report",
            "schema_version", "report_path", "ground_truth", "image_sets"
        }
        missing_fields = required_fields - set(case_data.keys())
        if missing_fields:
            raise CaseValidationError(f"Missing required fields: {missing_fields}")

        # Validate subspecialty if present
        validate_subspecialty(case_data)

        # Validate image sets
        for image_set in case_data["image_sets"]:
            required_image_fields = {
                "converted_path", "metadata_path", "num_slices", "sort_key"
            }
            missing_image_fields = required_image_fields - set(image_set.keys())
            if missing_image_fields:
                raise CaseValidationError(
                    f"Missing required image set fields: {missing_image_fields}"
                )

        print(f"Case validation successful: {case_path}")

    except json.JSONDecodeError:
        raise CaseValidationError("Invalid JSON in case.json")
    except Exception as e:
        raise CaseValidationError(f"Validation error: {str(e)}")

def main():
    """Main entry point for the validation script."""
    if len(sys.argv) != 2:
        print("Usage: validate_case.py <case_path>")
        sys.exit(1)

    case_path = sys.argv[1]
    try:
        validate_case(case_path)
    except CaseValidationError as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main() 