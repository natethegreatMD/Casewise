1. Drop DICOMs into `data/images/case001/AX/`  
2. Add `case001_report.txt` to `data/reports/`  
3. Run:
   - convert_dicom_to_png.py
   - extract_dicom_metadata.py
   - scan_new_cases.py
4. Manually check `case.json` if needed  
5. Run `validate_case.py`  
6. Approve in `review_case.py`  
7. Run `simulate_oral_exam.py`  
8. Grade with `grading.py`  
9. Export with `export_batch.py`  
