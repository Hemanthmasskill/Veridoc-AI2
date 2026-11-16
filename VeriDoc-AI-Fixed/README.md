# VeriDoc: Automated Multi-Document Verification and Tamper Detection System

**MCA Mini Project | Course Code: CAF 7104**  
*A Full-Stack, Local-First Document Fraud Detection & Verification Platform*

---

## Executive Overview

**VeriDoc** is an automated, on-device document fraud detection and verification platform built with **Node.js**, **Express.js**, and an offline **Python Computer Vision & Machine Learning engine** (PaddleOCR, RapidOCR ONNX, Tesseract OCR, PyMuPDF, pypdfium2, pypdf, OpenCV, and TF-IDF).

Traditional document verification services rely on third-party cloud APIs that expose sensitive Personally Identifiable Information (PII). VeriDoc eliminates external cloud dependencies by executing all optical character recognition (OCR), structural syntax checks, image tamper forensics, and cross-field consistency evaluations locally on the host machine, immediately purging uploaded files after analysis.

---

## Key Features

- **Multi-Engine OCR Pipeline**:
  - Primary OCR via **PaddleOCR** and **RapidOCR ONNX** (cached in-memory singletons) for fast, accurate text and tabular detection.
  - Native digital PDF text extraction across all pages via **PyMuPDF / pypdf**.
  - Multi-page scanned PDF rasterization via **PyMuPDF / pypdfium2 / pdf2image** with automatic temporary file cleanup.
  - Automatic 4-way rotation invariance (`0°`, `90°`, `180°`, `270°`) and **Tesseract OCR** (`PSM 6` / `PSM 3`) fallback.
- **Deep Tamper & Forgery Screening**:
  - **Content-Aware Error Level Analysis (ELA)** at 90% JPEG re-compression to detect localized digital splicing while ignoring blank paper margins.
  - **DCT Copy-Move Forgery Detection** targeting duplicated seals, stamps, and cloned text patches.
  - **Content-Cell Sharpness & Noise Analysis** across a 4×4 grid to flag spliced regions with mismatched blur/noise profiles.
  - **QR Code Detection & Payload Cross-Checking** via `cv2.QRCodeDetector` to verify visual credentials against encoded QR payloads.
- **Document-Specific Verification Suite (7 Document Categories)**:
  - **Aadhaar Card**: 12-digit format validation, **Verhoeff Dihedral Group ($D_8$) checksum algorithm**, bounded single-digit optical confusion self-healing, DOB/YOB syntax, gender, and PIN code checks.
  - **PAN Card**: 10-character alphanumeric structure (`[A-Z]{5}[0-9]{4}[A-Z]`), 4th-character Income Tax entity validation (`P`, `C`, `H`, `F`, `A`, `T`, `B`, `L`, `J`, `G`, `E`), 5th-character surname initial verification, father's name, and DOB checks.
  - **Passport**: Full **ICAO Doc 9303 TD3 Machine Readable Zone (MRZ)** parsing, 7-3-1 weight modulo-10 check digit verification for passport number, birth date, expiration date, personal number, and composite checksum, plus visual-to-MRZ parity.
  - **Driving Licence**: State/UT RTO 2-letter prefix validation (`TN`, `MH`, `DL`, `HR`, `KA`, etc.), statutory 18+ minimum driver age verification, chronological birth/issue/validity checks, and blood group extraction.
  - **Academic Marksheet**: Roll and registration number validation, subject marks extraction (both `085/100` and space-separated tabular columns), automated sum and percentage cross-verification, and grade consistency.
  - **Certificate**: Certificate/Credential ID extraction, recipient name, course/program title, and issue date validation.
  - **Generic Documents**: TF-IDF cosine similarity (with pure-Python fallback when `scikit-learn` is absent), phone/email/date/PIN extraction, and lexical coherence scoring.
- **Explainable Evidence & Risk Engine**:
  - Produces transparent multi-layer evidence breakdowns (`VERIFIED`, `NEEDS_REVIEW`, `SUSPICIOUS`) with calibrated risk levels (`LOW`, `MEDIUM`, `HIGH`).
  - Pre-OCR Image Quality Gating (Laplacian blur variance, brightness, contrast, glare) ensures blurry photos trigger `NEEDS_REVIEW` (retake photo) rather than false fraud accusations.
- **Local Organization Authentication & Session Auditing**:
  - Salted `scrypt` password hashing, timing-safe verification, atomic JSON storage, HttpOnly `SameSite=Lax` cookies, session TTL pruning, and brute-force rate limiting.
  - Real-time client-side analytics dashboard stored in browser `localStorage`.

---

## System Architecture

```mermaid
flowchart TD
    Client["Client Web UI\n(Dropzone / Live Camera)"] -->|POST /api/upload| ExpressServer["Node.js & Express Gateway\n(server.js)"]
    ExpressServer -->|Child Process Bridge (90s timeout)| MLService["VeriDoc ML Pipeline\n(ml/verify_document.py)"]

    subgraph ML_Engine ["VeriDoc Local ML & CV Engine"]
        MLService --> OCREngine["Layer 1: Multi-Engine OCR & PDF Parser\n(PaddleOCR / RapidOCR / Tesseract / PyMuPDF / pypdfium2)"]
        OCREngine --> QualityGate["Layer 2: Image Quality Gate\n(Laplacian Blur, Brightness, Contrast, Glare)"]
        QualityGate --> Classifier["Layer 3: Document Classifier\n(TF-IDF Cosine + MRZ/ID Anchors)"]
        Classifier --> SpecificVerifier["Layer 4: Document Verifiers\n(Aadhaar, PAN, Passport, DL, Marksheet, Certificate)"]
        SpecificVerifier --> TamperAnalysis["Layer 5: Tamper & Forgery Forensics\n(Content ELA, DCT Copy-Move, Noise Grid, QR Decode)"]
        TamperAnalysis --> CrossField["Layer 6: Cross-Field & Checksum Validator\n(Verhoeff D8, ICAO MRZ 7-3-1, Marks Math, QR Parity)"]
        CrossField --> EvidenceEngine["Layer 7: Evidence Aggregator & Decision Engine\n(Calibrated Weights -> VERIFIED / NEEDS_REVIEW / SUSPICIOUS)"]
    end

    EvidenceEngine -->|JSON Dossier| ExpressServer
    ExpressServer -->|fs.unlink() Temp Purge + JSON Response| Client
```

---

## Repository Structure

```text
VeriDoc-AI-Fixed/
├── server.js                          # Express.js HTTP backend, auth store, and Python process runner
├── package.json                       # Node.js project manifest and scripts
├── package-lock.json                  # Locked Node.js dependency tree
├── start_veridoc.bat                  # One-click Windows 11 launcher (auto-setup + port guard)
├── stop_veridoc.bat                   # One-click Windows 11 server stopper
├── README.md                          # Complete system documentation
├── AUDIT_PROGRESS.md                  # Engineering audit & phase tracker
├── AUDIT_REPORT.md                    # Full technical audit report (24 issues analyzed)
├── FIX_SUMMARY.md                     # Detailed summary of code repairs implemented
├── TEST_REPORT.md                     # Execution report of automated test suite
├── CHANGELOG.md                       # Version 1.1.0 release notes
├── public/                            # VeriDoc Web Frontend
│   ├── index.html                     # Responsive UI layout, 7 document cards, upload & camera modal
│   ├── styles.css                     # Design tokens, responsive grid, verdict badges
│   ├── app.js                         # Dropzone, camera capture, XSS-safe result renderer
│   ├── auth.js                        # Organization authentication controller
│   └── dashboard.js                   # Browser-local verification analytics and chart renderer
├── ml/                                # Python Machine Learning & Computer Vision Core
│   ├── verify_document.py             # Main verification orchestrator CLI
│   ├── check_engines.py               # OCR & PDF engine diagnostic probe
│   ├── classifier.py                  # Probabilistic document classifier (sklearn + pure-Python TF-IDF)
│   ├── doc_templates.py               # Shared keyword definitions & regex patterns
│   ├── verifiers.py                   # Specialized verification adapters per document type
│   ├── cross_field.py                 # Cross-field consistency (MRZ, Verhoeff, marks math, QR)
│   ├── tamper.py                      # Computer vision forensics (ELA, DCT copy-move, noise, QR)
│   ├── image_quality.py               # Pre-OCR blur, luminance, contrast, and glare gates
│   ├── number_checks.py               # ID entropy, sequential run, and fabrication screening
│   ├── evidence.py                    # Calibrated evidence aggregator and decision matrix
│   ├── requirements.txt               # Full Python dependency specifications (Windows 11 ready)
│   ├── requirements.render.txt        # Lightweight cloud/container requirements
│   └── config/
│       └── documentVerification.json  # Normalized evidence weights & decision thresholds
├── tests/
│   └── test_pipeline.py               # 45 automated unit & end-to-end tests (synthetic data only)
├── sample/                            # 6 synthetic sample PDFs (Aadhaar, PAN, Passport, DL, Marksheet, Certificate)
└── uploads/                           # Ephemeral upload directory (auto-purged after every request)
```

---

## Windows 11 Installation & Execution

### 1. Prerequisites
- **Node.js**: v18.x or v20.x LTS ([https://nodejs.org](https://nodejs.org))
- **Python**: Python 3.11.x ([https://www.python.org](https://www.python.org)) — ensure **"Add python.exe to PATH"** is checked during installation.
- **Tesseract OCR** *(Optional fallback)*: UB-Mannheim Windows installer (`C:\Program Files\Tesseract-OCR\tesseract.exe` is auto-detected even if not in `PATH`).

### 2. Option A: One-Click Windows 11 Launch (Recommended)
1. Extract `VeriDoc-AI-Fixed.zip` to a local folder (e.g., `C:\Projects\VeriDoc-AI-Fixed`).
2. Double-click **`start_veridoc.bat`**.
   - Automatically checks Node.js and Python 3.11.
   - Frees port `3000` if a previous instance is still running.
   - Runs `npm install` and creates `ml\venv` with all required Python packages on first launch.
   - Starts the server and opens `http://localhost:3000` in your default browser.
3. To stop the server at any time, double-click **`stop_veridoc.bat`**.

### 3. Option B: Manual PowerShell Setup
```powershell
# 1. Install Node.js dependencies
npm install

# 2. Create and activate Python 3.11 virtual environment
py -3.11 -m venv ml\venv
.\ml\venv\Scripts\Activate.ps1

# 3. Install Python ML & OCR packages
python -m pip install --upgrade pip
pip install -r ml\requirements.txt

# 4. Verify OCR & PDF engines
python ml\check_engines.py

# 5. Run automated test suite (45 tests)
python -m unittest discover -s tests -v

# 6. Start the VeriDoc server
npm start
```

---

## API Documentation

### 1. `POST /api/upload`
Uploads a document image or PDF and returns the multi-layer verification dossier.
- **Form Fields**:
  - `document`: File (`.jpg`, `.jpeg`, `.png`, `.webp`, `.pdf`), max `15 MB`.
  - `documentType`: `auto` (default), `aadhaar`, `pan`, `passport`, `driving_licence`, `marksheet`, `certificate`, or `generic`.
- **Response (`200 OK`)**:
  - Returns `verdict` (`VERIFIED`, `NEEDS_REVIEW`, or `SUSPICIOUS`), `overallScore`, `ocrConfidence`, `syntaxScore`, `semanticScore`, `fields`, `reasons`, `findings`, `evidence`, `cross_field`, `tamper`, and `checks`.

### 2. `GET /api/health`
Returns server status, resolved `pythonPath`, and active OCR/PDF engine capabilities (`paddleocr`, `rapidocr`, `pytesseract_lib`, `tesseract_binary`, `pymupdf`, `pypdfium2`, `pypdf`, `opencv`, `sklearn`, `tfidf_engine`).

### 3. `GET /api/doc-types`
Returns the list of supported document types.

### 4. Organization Auth Endpoints
- `POST /api/auth/signup` — `{ orgName, email, password }`
- `POST /api/auth/login` — `{ email, password }`
- `POST /api/auth/logout` — clears session cookie
- `GET /api/auth/me` — returns active organization profile or `401`

---

## Known Limitations & Honesty Disclaimers
- **Format & Mathematical Validation vs. Official Registry**: VeriDoc validates structural rules, check digits (Verhoeff $D_8$, ICAO 9303 MRZ), cross-field consistency, and passive pixel forensics entirely offline. It does not query external government databases (UIDAI, NSDL/Income Tax, Passport Seva, or Parivahan Sarathi) to preserve 100% local privacy.
- **Cryptographic QR Signatures**: While VeriDoc decodes readable QR payloads and cross-checks them against printed text, UIDAI cryptographic signature verification requires issuer public-key certificates and is explicitly reported as `NOT_AVAILABLE`.
- **Passive Image Forensics**: ELA, DCT copy-move, and sharpness grids are screening heuristics; heavy social-media re-compression or glare can trigger warnings, which VeriDoc conservatively routes to `NEEDS_REVIEW` unless paired with mathematical or structural contradictions.