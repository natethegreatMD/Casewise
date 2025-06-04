## Required Fields
- `case_id`
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
