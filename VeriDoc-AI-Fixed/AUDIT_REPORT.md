# VeriDoc-AI Complete Technical Audit Report (`AUDIT_REPORT.md`)

**Project**: VeriDoc — Automated Multi-Document Verification and Tamper Detection System  
**Repository**: `https://github.com/Hemanthmasskill/VeriDoc-AI`  
**Audit Scope**: Full-stack source code inspection across Backend (`server.js`, `package.json`), Frontend (`public/*`), Python ML/OCR/Forensics (`ml/*`), Configuration (`ml/config/documentVerification.json`), Sample Fixtures (`sample/*`), Windows Batch Scripts (`*.bat`), and Automated Tests (`tests/test_pipeline.py`).

---

## Executive Summary of Discovered Defects

A total of **24 confirmed defects** were identified and verified through static code analysis and dynamic execution:
- **Critical Severity**: 5 issues
- **High Severity**: 7 issues
- **Medium Severity**: 8 issues
- **Low Severity**: 4 issues

---

## Detailed Defect Catalogue

### 1. Critical Severity Issues (5)

#### **VD-001: Authentic Aadhaar & ID JPEGs Falsely Flagged as `SUSPICIOUS`**
- **Severity**: Critical
- **Affected Files**: `ml/tamper.py` (`_sharpness_regions`, `_ela_regions`), `ml/evidence.py` (`build_findings`, `decide`)
- **Root Cause**:
  1. `_sharpness_regions` partitioned the image into a 4×4 grid and flagged any cell where Laplacian variance `v < med / 8`. Because identity cards and documents contain blank white margins or uniform background areas (where Laplacian variance approaches `0`), every clean document JPEG triggered 2–3 `local_noise_mismatch` boxes.
  2. `_ela_regions` computed mean and standard deviation across the entire image (including uniform white backgrounds), dragging `diff.mean()` and `diff.std()` near zero and flagging normal high-contrast text edges as `compression_mismatch`.
  3. When a normal text-edge ELA box overlapped a blank-margin sharpness box in `_merge_regions`, `build_findings` escalated the 2-signal overlap to `severity = "CRITICAL"`, which immediately short-circuited `decide()` to return `SUSPICIOUS`.
- **Impact**: Real, unedited Aadhaar JPEGs and clean synthetic document images were falsely rejected as `SUSPICIOUS` with `CRITICAL finding(s) in: tamper`.
- **Actual Correction Implemented**:
  - Restricted `_sharpness_regions` and `_noise_inconsistency` in `ml/tamper.py` to evaluate only content-bearing cells (`std >= 18.0` and `lap_var >= 15.0`), excluding blank margins.
  - Restricted `_ela_regions` threshold statistics to content pixels (`local_std > 10.0`) with a minimum residual floor (`> 18.0`) and morphological closing.
  - Added DCT block copy-move clone detection (`_copy_move_regions`) for non-blank patches.
  - Calibrated `build_findings` and `decide` in `ml/evidence.py` so 2-signal passive forensic overlaps raise `SUSPICIOUS` (routing to `NEEDS_REVIEW` when all structural/checksum checks pass), reserving `CRITICAL` for 3+ overlapping signals or copy-move clone overlaps.
- **Verification Test**: `TestDecision.test_clean_aadhaar_jpeg_not_falsely_suspicious` in `tests/test_pipeline.py`.

---

#### **VD-002: `verhoeff_suggest` Exhaustive Loop Laundering 100% of Fake 12-Digit Numbers**
- **Severity**: Critical
- **Affected File**: `ml/verify_document.py` (`verhoeff_suggest`, `syntax_checks`)
- **Root Cause**: `verhoeff_suggest` first checked likely OCR confusions, and then ran an exhaustive fallback (`for i in range(12): for new_ch in "0123456789"`). Because the Verhoeff dihedral group $D_5$ check digit is a quasigroup permutation, **every** 11-digit prefix has a unique valid 12th check digit in `0..9`. Thus, the exhaustive loop succeeded on 100% of arbitrary invalid 12-digit numbers, marking `syntax:aadhaar_number` as `pass`!
- **Impact**: Any random or forged 12-digit number passed Aadhaar checksum validation as a "single-digit OCR misread".
- **Actual Correction Implemented**:
  - Removed the exhaustive `0123456789` loop from `verhoeff_suggest`.
  - Restricted `verhoeff_suggest` strictly to high-probability single-digit optical confusions (`0<->8`, `1<->7`, `3<->8`, `5<->6`, `6<->5/8`, `7<->1`, `8<->0/3/6`) while forbidding leading `0` or `1`.
  - Updated `syntax_checks` so unhealable Verhoeff failures return `status = "fail"`.
- **Verification Test**: `TestVerhoeff.test_non_confusion_invalid_number_rejected` and `TestVerhoeff.test_self_heal_single_digit`.

---

#### **VD-003: `correct_ocr_text` Destroying Space-Separated Numbers and Driving Licence Formats**
- **Severity**: Critical
- **Affected File**: `ml/verify_document.py` (`correct_ocr_text`)
- **Root Cause**: Line 195 executed `re.sub(r"(?<=\d) (?=\d)", "", text)`, removing spaces between **all** adjacent digits globally across the document.
  1. In Driving Licences (`TN01 2023 0048291` or `HR03 20250000058`), it collapsed the space after the RTO digits (`TN0120230048291`), causing `TEMPLATES["driving_licence"]["patterns"]["dl_no"]` to fail when optional hyphens/spaces varied.
  2. In Marksheets (`MATHEMATICS 085 100`), it merged separate numeric columns into `085100`.
- **Impact**: Corrupted extracted text and broke downstream regex extractors on multi-column tables and licence numbers.
- **Actual Correction Implemented**:
  - Replaced the global digit-space stripper in `correct_ocr_text` with `re.sub(r"\b(\d)(?: (\d)){3,}\b", ...)`, which only collapses isolated single-digit runs split by OCR (`"2 3 4 5"` -> `"2345"`) while preserving multi-digit groups (`"7260 1815 9081"`, `"HR03 20250000058"`, `"085 100"`).
- **Verification Test**: `TestVerifiers.test_correct_ocr_text_preserves_dl_and_names`.

---

#### **VD-004: Scanned PDF Extraction Failure, Single-Page Limit, and Temp File Leak**
- **Severity**: Critical
- **Affected Files**: `ml/verify_document.py` (`extract_text_pdf`, `do_ocr`), `ml/check_engines.py`
- **Root Cause**:
  1. `extract_text_pdf` only rendered scanned PDFs when `PyMuPDF` (`fitz`) was installed; if only `pypdfium2` or `pdf2image` was installed, scanned PDFs failed immediately with `pdf-error`.
  2. When `extract_text_pdf` rendered `<pdf>_page1.png`, `do_ocr` overwrote `file_path = out` without adding `out` to `temp_files`, leaking `<pdf>_page1.png` in `uploads/` on every scanned PDF upload.
  3. Scanned PDF rendering only rasterized `doc[0]` (Page 1), ignoring multi-page scanned PDFs.
- **Impact**: Scanned PDFs failed in environments without `fitz` and leaked temporary PNG files in `uploads/`.
- **Actual Correction Implemented**:
  - Added multi-page (up to 3 pages vertically stitched) rasterization support in `extract_text_pdf` with cascading fallbacks: `PyMuPDF (fitz)` -> `pypdf` (digital text) -> `pypdfium2` -> `pdf2image`.
  - Appended the rendered PNG path `out` to `temp_files` in `do_ocr` so `cleanup()` deletes it in the `finally` block.
- **Verification Test**: `TestEndToEndSamples.test_scanned_pdf_ocr_fallback`.

---

#### **VD-005: Hard `scikit-learn` Dependency Crashing Document Classifier**
- **Severity**: Critical
- **Affected File**: `ml/classifier.py` (`similarities`)
- **Root Cause**: `similarities()` performed an unconditional `from sklearn.feature_extraction.text import TfidfVectorizer`, raising `ModuleNotFoundError` whenever `scikit-learn` was not installed or failed to load.
- **Impact**: Caused 12 unit tests in `test_pipeline.py` and the entire classification & semantic pipeline to crash when `sklearn` was absent.
- **Actual Correction Implemented**:
  - Implemented `_pure_python_tfidf_similarities()` in `ml/classifier.py` using exact sublinear TF (`1 + log(tf)`), smooth IDF (`log((1 + N) / (1 + df)) + 1`), `(1, 2)` word n-grams, and L2-normalized cosine similarity as an automatic fallback when `sklearn` is unavailable.
- **Verification Test**: `TestClassifier` suite (15 tests passing with both `sklearn` and fallback).

---

### 2. High Severity Issues (7)

#### **VD-006: OCR Rotation Invariance Failure & Destructive Binarization**
- **Severity**: High
- **Affected File**: `ml/verify_document.py` (`preprocess_image`, `coherence_of`, `do_ocr`)
- **Root Cause**:
  1. `preprocess_image` applied aggressive block-41 Gaussian adaptive thresholding (`cv2.adaptiveThreshold`) to all images, hollowing out thin strokes on clean renders (`'1994'` misread as `'1984'`).
  2. `coherence_of` scored 1-word upside-down OCR noise (`'av'`) as `0.70`, triggering the `>= 0.60` early exit at `0°` rotation before `180°` was ever tested!
  3. Small text blocks inside large white canvases were not cropped before upscaling.
- **Actual Correction Implemented**:
  - Preserved continuous grayscale anti-aliased strokes in `preprocess_image`, skipping destructive thresholding and auto-cropping excessive white margins on sparse synthetic renders.
  - Updated `coherence_of` to weight by word count (`min(len(words) / 4.0, 1.0)`) and token alpha ratio, requiring both `coherence >= 0.65` and `confidence >= 0.65` for early rotation exit.
- **Verification Test**: `TestQualityGate.test_ocr_rotation_invariant`.

#### **VD-007: `cross_field.py` Phantom Aadhaar Conflict from 16-Digit VID**
- **Severity**: High
- **Affected File**: `ml/cross_field.py` (`_all_id_like`), `ml/verify_document.py` (`syntax_checks`)
- **Root Cause**: When an Aadhaar card included both a 12-digit Aadhaar number (`7260 1815 9081`) and a 16-digit VID (`8142 6709 3358 2917`), `_all_id_like` only collapsed 3-group spaces, leaving the first 3 groups of the VID (`814267093358`) as a separate 12-digit token that matched `\b[2-9]\d{11}\b`. `cross_field.validate` then flagged the document as `SUSPICIOUS` for having "two different Aadhaar numbers"! Additionally, in `passport` syntax checks, `alias_of = {"mrz": "passport_no"}` overwrote `passport_no` with raw MRZ line 1 when the passport number was embedded in MRZ line 2.
- **Actual Correction Implemented**:
  - Updated `_all_id_like` in `ml/cross_field.py` to collapse all horizontal spaces between digits (`(?<=\d)[ \t\u00a0]+(?=\d)`), turning 16-digit VIDs into 16-digit tokens that never match `\b[2-9]\d{11}\b`.
  - Updated `PassportVerifier.extract_fields` and `syntax_checks` to parse the actual 9-char passport number (`line2[0:9].replace("<", "")`) from TD3 MRZ pairs.
- **Verification Test**: `TestEndToEndSamples.test_all_sample_pdfs_verify` (`sample_aadhaar.pdf` and `sample_passport.pdf`).

#### **VD-008: `CertificateVerifier` Extracting `"IFICATE"` as Certificate ID**
- **Severity**: High
- **Affected Files**: `ml/doc_templates.py`, `ml/verifiers.py`
- **Root Cause**: `cert_id` regex `r"\b(?:Certificate|Cert\.?|Credential|ID|No)[\s\.:No\-]*([A-Z0-9\-\/]{4,24})\b"` matched the prefix `"CERT"` inside the heading `"CERTIFICATE OF COMPLETION"` and captured the suffix `"IFICATE"` as the Certificate ID!
- **Actual Correction Implemented**:
  - Anchored `cert_id` in `ml/doc_templates.py` to require an explicit identifier label (`(?:Certificate|Cert\.?|Credential)\s*(?:ID|No\.?|Number|#)` or `(?:ID|No\.?)`) followed by `[:\-]`.
- **Verification Test**: `TestVerifiers.test_certificate_verifier_id_not_matching_header`.

#### **VD-009: `DrivingLicenceVerifier` Date Format, Minimum Age (18+), and RTO State Validation Gaps**
- **Severity**: High
- **Affected Files**: `ml/verifiers.py`, `ml/doc_templates.py`
- **Root Cause**: `DrivingLicenceVerifier.extra_checks` only parsed `%d-%m-%Y` (silently skipping `/` and `.` dates), did not verify whether the driver was at least 18 years old at issue, did not validate the 2-letter Indian State/UT RTO code, and missed `"B Positive"` blood group formats.
- **Actual Correction Implemented**:
  - Added `_parse_date_flexible` supporting `/`, `-`, and `.` separators, 38 valid Indian State/UT RTO codes (`VALID_INDIAN_STATE_CODES`), 18+ driver age validation, issue-to-expiry chronological check, and expanded `blood_group` regex.
- **Verification Test**: `TestClassifier.test_licence_verifier` and `TestClassifier.test_licence_underage_fails`.

#### **VD-010: `PANVerifier` Entity Code Typo, Surname Initial Check, and Multiline Name Bleed**
- **Severity**: High
- **Affected Files**: `ml/verify_document.py`, `ml/verifiers.py`, `ml/doc_templates.py`
- **Root Cause**: `syntax_checks` checked `pan[3] not in "PCHABGJLFTT"` (missing `E` for LLP and duplicating `T`), `PANVerifier` lacked 5th-character surname initial verification, and `name` regex `[A-Z][A-Za-z ]{2,40}` matched across newlines into `Father's Name`.
- **Actual Correction Implemented**:
  - Added full `VALID_PAN_ENTITIES` validation (`P, C, H, F, A, T, B, L, J, G, E`), 5th-character surname initial check for Individual (`P`) PAN cards, and single-line lookahead boundaries on `name` and `father_name`.
- **Verification Test**: `TestVerifiers.test_pan_verifier_surname_and_entity`.

#### **VD-011: Unhealable Aadhaar Checksum Failure Not Escalated to Cross-Field Conflict**
- **Severity**: High
- **Affected Files**: `ml/verifiers.py`, `ml/cross_field.py`, `ml/evidence.py`
- **Root Cause**: When an Aadhaar number failed Verhoeff validation and could not be self-healed, `AadhaarVerifier` only emitted a `warn` note and `cross_field.py` reported `PASS`, allowing forged Aadhaar numbers on clean images to escape `CRITICAL` checksum failure.
- **Actual Correction Implemented**:
  - `AadhaarVerifier` now sets `fields["checksum_valid"] = False` and `status = "fail"` on unhealable Verhoeff failures, and `cross_field.validate` records a `aadhaar_checksum` conflict that `evidence.py` grades as `CRITICAL` on readable images.
- **Verification Test**: `TestVerhoeff.test_non_confusion_invalid_number_rejected`.

#### **VD-012: Hardcoded Windows Python Path & Missing Subprocess Timeout in `server.js`**
- **Severity**: High
- **Affected File**: `server.js`
- **Root Cause**: `server.js` hardcoded `path.join(__dirname, 'ml', 'venv', 'Scripts', 'python.exe')` with no fallback to `ml/venv/bin/python` or system `python3`/`python`, and invoked `PythonShell.run` without a timeout, risking hung requests.
- **Actual Correction Implemented**:
  - Added `resolvePythonExecutable()` probing `VERIDOC_PYTHON`, Windows `Scripts/python.exe`, POSIX `bin/python3`, and system `python3`/`python`, plus `runPythonScript()` with a 90-second kill timeout and UTF-8 encoding enforcement.

---

### 3. Medium & Low Severity Issues (12)

- **VD-013 (Medium — `ml/cross_field.py`, `ml/tamper.py`)**: Wired decoded QR payload mismatches (`qr.data_consistent is False`) into `cross_field.validate` and `evidence.py`, loaded `tolerance_marks` / `tolerance_pct` from `documentVerification.json`, and added space-separated marksheet table parsing.
- **VD-014 (Medium — `ml/config/documentVerification.json`)**: Added missing `driving_licence` weights and normalized all 7 document weight vectors to sum to `1.00`.
- **VD-015 (Medium — `ml/verify_document.py`)**: Cached `_RAPIDOCR_INSTANCE` and `_PADDLEOCR_INSTANCE` as module-level singletons so multi-rotation OCR does not reload neural weights from disk 4 times per image.
- **VD-016 (Medium — `ml/verify_document.py`)**: Removed personal surnames from `CORRECTOR_VOCAB` and raised `difflib` cutoff to `0.86` on words `>= 5` chars so applicant names are never mutated.
- **VD-017 (Medium — `public/app.js`, `public/dashboard.js`)**: Added `escapeHtml()` across all `innerHTML` interpolations to eliminate DOM XSS from OCR-extracted text or filenames, and revoked `URL.createObjectURL` blobs to prevent browser memory leaks.
- **VD-018 (Medium — `server.js`)**: Added IP rate limiting on `/api/auth/login` and `/api/auth/signup`, automatic pruning of expired sessions in `sessions.json`, atomic temp-file-and-rename JSON persistence, and HTTP security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`).
- **VD-019 (Medium — `server.js`, `public/app.js`, `public/index.html`)**: Added `.webp` support, unified the 15 MB upload limit across client and server, and added `413 Payload Too Large` status handling.
- **VD-020 (Medium — `sample/*.pdf`)**: Rebuilt `sample_aadhaar.pdf`, `sample_marksheet.pdf`, and `sample_passport.pdf` with valid PDF 1.4 cross-reference (`xref`) tables (eliminating `pypdf` `incorrect startxref pointer(1)` warnings) and added synthetic `sample_pan.pdf`, `sample_driving_licence.pdf`, and `sample_certificate.pdf`.
- **VD-021 (Low — `start_veridoc.bat`)**: Added automatic port 3000 conflict cleanup and replaced fragile `pip install --no-deps` fallback with a clean core OCR bundle installation.
- **VD-022 (Low — `ml/requirements.txt`, `ml/requirements.render.txt`)**: Pinned `numpy>=1.24.0,<2.0.0` for binary compatibility with OpenCV/ONNX/Paddle wheels on Windows 11 and added `pypdfium2>=4.25.0`.
- **VD-023 (Low — `public/index.html`, `public/styles.css`, `public/app.js`)**: Added Driving Licence and Certificate cards to the "Supported documents" section and hid the broken empty `<img>` tag when previewing PDF files.
- **VD-024 (Low — `README.md`)**: Updated architecture diagram, directory structure, Windows 11 instructions, and API response examples to match the actual implementation.
