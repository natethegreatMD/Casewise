1. convert_dicom_to_png.py → generates PNGs from DICOMs  
2. extract_dicom_metadata.py → writes metadata.json  
3. scan_new_cases.py → builds case.json, normalizes tags  
4. validate_case.py → checks structure, logs validation status  
5. review_case.py → CLI for human review/approval/tagging  
6. simulate_oral_exam.py → builds and sends GPT prompt  
7. grading.py → runs rubric scoring, stores in results/  
8. export_batch.py → outputs case packets and logs export  
