# Valid Subspecialties (ABR Oral Exam Categories)

This document lists the valid subspecialties that can be used in the `subspecialty` field of case metadata. These categories align with the American Board of Radiology (ABR) Oral Exam categories.

## Valid Subspecialties

1. `abdominal` - Abdominal imaging
2. `breast` - Breast imaging
3. `cardiothoracic` - Cardiothoracic imaging
4. `musculoskeletal` - Musculoskeletal imaging
5. `neuroradiology` - Neuroradiology
6. `nuclear` - Nuclear medicine
7. `pediatric` - Pediatric radiology

## Usage Notes

- The `subspecialty` field in case metadata must exactly match one of these values
- Values are case-sensitive
- No abbreviations or variations are allowed
- This list may be updated as ABR categories evolve

## Validation

The case validation system will check that any provided subspecialty matches one of these exact values. If an invalid subspecialty is provided, the validation will fail with an error message indicating the invalid value. 