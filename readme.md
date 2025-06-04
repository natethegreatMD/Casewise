# Casewise

**AI-powered radiology oral board simulator.**  
Casewise processes real DICOM imaging cases, simulates diagnostic oral exams using GPT, and applies rubric-based AI grading. Built with modular CLI tools, file-based workflows, and long-term scalability in mind.

---

## Project Overview

Casewise is designed to:
- Ingest and process real DICOMs + radiology reports
- Extract metadata and convert DICOM slices to PNG
- Generate structured `case.json` files
- Run GPT-based oral board simulations using prompt templates
- Transcribe and QA voice responses
- Grade performance using rubric-based GPT scoring
- Export results for review or evaluation

---

## How to Use

1. Place raw DICOM files into `data/images/caseXXX/AX/`
2. Place report file into `data/reports/caseXXX_report.txt`
3. Run:

```bash
python scripts/convert_dicom_to_png.py --case case001
python scripts/extract_dicom_metadata.py --case case001
python scripts/scan_new_cases.py --case case001
python scripts/validate_case.py --case case001
python scripts/review_case.py --case case001
python scripts/simulate_oral_exam.py --case case001 --user mic
python scripts/grade_response.py --case case001 --user mic
python scripts/export_batch.py
```

---

## Configuration

- All runtime settings are in `config.json`
- Secrets (like API keys) are stored in `.env` (not tracked in git)

---

## Prompt Management

- Prompt files live in `prompts/`
- Each prompt includes a version header
- Used prompts must be logged in `case.json["prompt_version"]`

---

## MVP Features

- Modular pipeline: ingest → validate → review → simulate → grade → export
- File-based inter-module communication (no direct script imports)
- Structured `case.json` format with validation and schema enforcement
- Retry handling and error logging for GPT + Whisper
- CLI-first architecture for portability and clarity

---

## .gitignore (included)

- `venv/`
- `__pycache__/`
- `logs/`
- `data/converted/`
- `data/images/`
- `.env`
- `*.log`

---

## System Design Docs

Located in `docs/`, including:
- `PROJECT_STRUCTURE.md`
- `CASE_JSON_SCHEMA.md`
- `MODULE_FLOW.md`
- `FUNCTION_INTERFACE.md`
- `DATA_FLOW.md`
- `CONFIG_OVERVIEW.md`
- `PLANNING_CHECKLIST.md`
- `POST_MVP_FIXES.md`

---

## Built With

- Python 3.11+
- OpenAI API (GPT, Whisper)
- pydicom, Pillow, python-dotenv

---

## Status

MVP planning complete.  
Implementation begins at `case001` with real data walkthrough.

---

## 📄 License

This project is not open source. All rights reserved to the author.
