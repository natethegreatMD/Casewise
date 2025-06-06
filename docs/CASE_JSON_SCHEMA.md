# Case JSON Schema

## Required Fields
- `case_id`: Unique identifier for the case
- `title`: Case title or description
- `difficulty`: Case difficulty level (1-5)
- `report`: Original radiology report text
- `schema_version`
- `report_path`
- `ground_truth`
- `image_sets`

Each `image_sets` entry:
- `converted_path`
- `metadata_path`
- `num_slices`
- `sort_key`: "InstanceNumber" or "filename"

## Optional Fields
- `subspecialty`: "neuroradiology" — used to categorize cases based on clinical subspecialty, aligning with ABR Oral Exam categories. This is a string field and is recommended for all future cases. Must match one of the valid subspecialties listed in SUBSPECIALTY_LIST.md.
- `transcript`: Previous discussion transcript
- `images`: List of image file paths
- `metadata`: Additional case information
- `created_at`: Timestamp of case creation
- `updated_at`: Timestamp of last update
- `modality`, `study_date`
- `difficulty`: "easy", "intermediate", "hard"
- `tags`: normalized (lowercase, underscore)
- `status`: "ready", "draft", "invalid"
- `approved_by`, `last_reviewed`
- `key_findings[]`
- `prompt_version`: oral_exam, grading
- `total_slices`

## Validation
- All paths must exist
- metadata.json must include: orientation, modality, num_slices
- Orientations must match config
- If subspecialty is provided, must match a valid ABR Oral Exam category

## Example
```json
{
  "case_id": "case_001",
  "title": "Acute stroke evaluation",
  "difficulty": 3,
  "report": "CT head without contrast shows...",
  "subspecialty": "neuroradiology",
  "transcript": "Previous discussion...",
  "images": ["data/images/case_001_1.png"],
  "metadata": {
    "patient_age": "65",
    "clinical_history": "Acute onset right hemiparesis"
  },
  "created_at": "2025-06-03T10:00:00Z",
  "updated_at": "2025-06-03T10:00:00Z"
}
```
