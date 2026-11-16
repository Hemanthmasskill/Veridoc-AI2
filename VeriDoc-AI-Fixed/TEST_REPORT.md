# VeriDoc-AI Test Execution Report (`TEST_REPORT.md`)

**Date**: 2026-11-12  
**Test Environment**: Python 3.11.2, Node.js v18.20.4, OpenCV 4.11.0, Tesseract OCR 5.x, pypdf, pypdfium2, Pillow  
**Test Suite**: `tests/test_pipeline.py` (`45` automated tests) + CLI End-to-End Document Suite (`6` synthetic PDFs + scanned PDF + JPEG tamper tests) + JavaScript Syntax Verification (`node -c`)

---

## 1. Automated Unit & Integration Test Results (`45 / 45 PASSED`)

Command executed:
```bash
python3 -m unittest discover -s tests -v
```

### Summary by Test Class
| Test Class | Tests Executed | Passed | Failed | Coverage Scope |
| :--- | :---: | :---: | :---: | :--- |
| `TestVerhoeff` | 3 | 3 | 0 | Verhoeff dihedral $D_8$ checksum, single-digit optical confusion self-healing, rejection of non-confusion invalid numbers (`483276483503`) |
| `TestMRZ` | 3 | 3 | 0 | ICAO Doc 9303 TD3 2×44 MRZ parsing, `7-3-1` modulo-10 check digits, composite check digit, tampered MRZ detection |
| `TestMarksheetArithmetic` | 3 | 3 | 0 | `obtained <= max`, `total == sum(subjects)`, percentage consistency, and space-separated tabular marksheet parsing |
| `TestClassifier` | 15 | 15 | 0 | TF-IDF cosine classification across Aadhaar, PAN, Passport, Driving Licence, Marksheet, Certificate, Generic fallback, MRZ anchor, DL/PAN number anchors, underage DL rejection |
| `TestQualityGate` | 2 | 2 | 0 | Laplacian blur variance gating (`PASS` vs `FAIL`), 4-way OCR rotation invariance (`0°` and `180°` upside-down images) |
| `TestDecision` | 4 | 4 | 0 | `VERIFIED` decision path, `SUSPICIOUS` contradiction path, `NEEDS_REVIEW` low-quality path, and clean Aadhaar JPEG false-positive regression test |
| `TestVerifiers` | 5 | 5 | 0 | Adapter interface, Generic fallback, PAN 4th-char entity & 5th-char surname initial check, Certificate ID header disambiguation, OCR text cleanup preservation |
| `TestFabricationScreen` | 8 | 8 | 0 | Repeated digits, repeated groups (`0000 1111 2222`), sequential runs (`123456`), entropy screen, ID masking (`72••••••••81`), sparse-text structure downgrade |
| `TestEndToEndSamples` | 2 | 2 | 0 | Full CLI subprocess verification across all 6 synthetic PDFs (`sample_*.pdf`) and scanned image-only PDF rasterization + temp file cleanup |
| **Total** | **45** | **45** | **0** | **100% Pass Rate (`Ran 45 tests in 9.39s — OK`)** |

---

## 2. End-to-End Synthetic Document Verification Results

| Synthetic Document | Auto-Detected Type | Verdict | Overall Score | Extracted & Validated Fields |
| :--- | :--- | :---: | :---: | :--- |
| `sample/sample_aadhaar.pdf` | `aadhaar` | `VERIFIED` | `0.961` | `aadhaar_number`: `726018159081` (`checksum_valid: True`), `dob`: `14/08/1999`, `gender`: `MALE`, `pincode`: `110001` |
| `sample/sample_pan.pdf` | `pan` | `VERIFIED` | `0.982` | `pan_number`: `ABCPS1234F`, `entity_type`: `Individual`, `name`: `Aarav Sharma`, `father_name`: `Rajesh Sharma`, `dob`: `14/08/1999` |
| `sample/sample_passport.pdf` | `passport` | `VERIFIED` | `0.985` | `passport_no`: `L898902C3`, `dob`: `12/08/1974`, `nationality`: `INDIAN`, MRZ `valid: True` (all 5 ICAO check digits pass) |
| `sample/sample_driving_licence.pdf` | `driving_licence` | `VERIFIED` | `0.977` | `dl_no`: `TN01 20230048291`, `state_code`: `TN`, `name`: `Karthik Rajan`, `dob`: `15/05/1998`, `issue_date`: `10/06/2023`, `validity`: `14/05/2040`, `blood_group`: `O+` |
| `sample/sample_marksheet.pdf` | `marksheet` | `VERIFIED` | `0.964` | `roll_no`: `26843179`, `year`: `2024`, `percentage`: `85.0%`, arithmetic `255 == 85+92+78` (`PASS`) |
| `sample/sample_certificate.pdf` | `certificate` | `VERIFIED` | `0.970` | `cert_id`: `CERT-2024-8821`, `recipient`: `Aarav Sharma`, `course`: `Machine Learning Foundations`, `date`: `15/08/2024` |

---

## 3. JavaScript & Backend Syntax Validation

Executed `node -c` across all backend and frontend JavaScript files:
- `node -c server.js` — **PASSED** (0 syntax errors)
- `node -c public/app.js` — **PASSED** (0 syntax errors)
- `node -c public/auth.js` — **PASSED** (0 syntax errors)
- `node -c public/dashboard.js` — **PASSED** (0 syntax errors)

---

## 4. Environment-Specific Notes & Blocked Tests

1. **Optional Heavy OCR Engines (`PaddleOCR` / `RapidOCR ONNX`)**:
   - In the isolated audit VM (where internet package installation is disabled), `Tesseract OCR 5.x`, `OpenCV`, `pypdf`, `pypdfium2`, and `Pillow` are pre-installed and verified end-to-end.
   - `RapidOCR ONNX` and `PaddleOCR` were audited via static code inspection, singleton caching repair, and output tuple contract verification; on Windows 11, running `start_veridoc.bat` automatically installs `rapidocr-onnxruntime` and `scikit-learn` into `ml\venv`.
2. **Live HTTP Server Socket Test**:
   - Because `node_modules/` is excluded from source control and outbound `npm install` is disabled inside the air-gapped audit VM, `server.js` was verified via `node -c` syntax validation and full code-path review, while `start_veridoc.bat` automatically executes `npm install` on the target Windows 11 machine.
