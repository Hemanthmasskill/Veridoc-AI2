# VeriDoc-AI Changelog (`CHANGELOG.md`)

## [1.1.0] — 2026-11-12 (Complete Audit & Engineering Repair Release)

### Fixed (Critical & High)
- **Tamper Forensics (`ml/tamper.py`, `ml/evidence.py`)**: Fixed false `SUSPICIOUS` verdicts on authentic Aadhaar and ID card JPEGs by restricting sharpness/noise grid analysis and ELA thresholding to content-bearing pixels (ignoring blank white margins) and calibrating passive overlap severity.
- **Aadhaar Verhoeff Checksum (`ml/verify_document.py`, `ml/verifiers.py`, `ml/cross_field.py`)**: Removed exhaustive `0–9` loop in `verhoeff_suggest` that falsely repaired 100% of invalid 12-digit numbers; restricted self-healing to true single-digit optical confusions and escalated unhealable checksum failures to cross-field conflicts.
- **Aadhaar VID Cross-Field Conflict (`ml/cross_field.py`)**: Fixed regex tokenization where 16-digit VIDs (`XXXX XXXX XXXX XXXX`) were partially matched as a conflicting 12-digit Aadhaar number.
- **OCR Post-Processing (`ml/verify_document.py`)**: Fixed `correct_ocr_text` global digit-space stripping (`(?<=\d) (?=\d)`) that corrupted Driving Licence numbers (`TN01 20230048291`) and space-separated marksheet tables, and removed surnames from `CORRECTOR_VOCAB`.
- **Scanned PDF Pipeline (`ml/verify_document.py`)**: Added multi-page scanned PDF rendering fallback via `pypdfium2` and `pdf2image` when `PyMuPDF` is unavailable, and fixed `<pdf>_page1.png` temporary file leak.
- **TF-IDF Classifier Fallback (`ml/classifier.py`)**: Added pure-Python sublinear TF-IDF cosine similarity fallback so document classification works seamlessly even without `scikit-learn`.
- **OCR Preprocessing & Rotation Invariance (`ml/verify_document.py`)**: Replaced destructive binary thresholding with continuous grayscale contrast normalization, auto-cropped sparse synthetic renders, and fixed rotation voting early-exit criteria.
- **Document Verifiers (`ml/verifiers.py`, `ml/doc_templates.py`)**:
  - Fixed `CertificateVerifier` `cert_id` matching `"IFICATE"` inside `"CERTIFICATE"`.
  - Enhanced `DrivingLicenceVerifier` with flexible date parsing (`/`, `-`, `.`), 18+ minimum age verification, Indian State/UT RTO code validation, and expanded blood group matching.
  - Enhanced `PANVerifier` with complete 4th-char entity code validation (`PCHFATBLJGE`), 5th-char surname initial verification, and non-bleeding multiline name extraction.
  - Enhanced `PassportVerifier` to recover passport numbers from TD3 MRZ line 2 when visual headers are absent.

### Added & Improved (Backend, Frontend, Security & Windows 11)
- **Backend (`server.js`)**: Added cross-platform Python interpreter discovery (`ml/venv/Scripts/python.exe`, `ml/venv/bin/python3`, system `python3`/`python`), 90-second subprocess timeout, auth rate limiting, expired session pruning, atomic JSON writes, and security headers.
- **Frontend (`public/app.js`, `public/dashboard.js`, `public/index.html`, `public/styles.css`)**: Added `escapeHtml()` to prevent DOM XSS, fixed `URL.createObjectURL` memory leaks, added client-side file size/type validation (15 MB, `.jpg/.jpeg/.png/.webp/.pdf`), fixed PDF preview display, and added all 7 supported document cards.
- **Sample Fixtures (`sample/*.pdf`)**: Repaired corrupted `startxref` pointers in `sample_aadhaar.pdf`, `sample_marksheet.pdf`, and `sample_passport.pdf`, and added `sample_pan.pdf`, `sample_driving_licence.pdf`, and `sample_certificate.pdf`.
- **Windows 11 Scripts (`start_veridoc.bat`, `ml/requirements.txt`)**: Pinned `numpy<2.0.0`, added `pypdfium2`, added port `3000` conflict cleanup before server startup, and expanded `tests/test_pipeline.py` from 36 to 45 automated tests.
