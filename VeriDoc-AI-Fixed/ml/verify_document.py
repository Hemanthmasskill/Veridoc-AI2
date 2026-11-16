"""[VeriDoc ML Engine] Automated Document Verification Service
===========================================================
VeriDoc — Automated Multi-Document Verification and Tamper Detection System.
Developed for MCA Mini Project | CAF 7104. Runs locally, zero cloud dependency.

Pipeline:
  1. Preprocess image (OpenCV) — grayscale, denoise, contrast (CLAHE), deskew
  2. OCR with PaddleOCR / RapidOCR / Tesseract (primary) — returns text + confidence
  3. Syntax verification — regex / format checks per document type
  4. Semantic verification — keyword coverage (TF-IDF cosine),
     logical consistency (dates, age, ID checksums), text coherence
  5. Tamper detection — ELA, copy-move detection, noise analysis, QR payload cross-check

Usage:
  python3 verify_document.py <file_path> <document_type>

  document_type: auto | aadhaar | pan | passport | driving_licence | marksheet | certificate | generic

Output:
  Single JSON object printed to stdout (last line). Node.js backend parses it.
"""

import sys
import os
import re
import json
import shutil
import difflib
from datetime import datetime

from doc_templates import TEMPLATES, SPECIFIC_TYPES

GENERIC_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
GENERIC_DATE = re.compile(r"(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})")

# Cached OCR engine singletons (prevents re-loading ONNX/Paddle weights on every rotation pass)
_RAPIDOCR_INSTANCE = None
_PADDLEOCR_INSTANCE = None


def _configure_tesseract_cmd():
    """Auto-configure pytesseract binary path on Windows if not on PATH."""
    if os.name != "nt" or shutil.which("tesseract"):
        return
    try:
        import pytesseract
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                pytesseract.pytesseract.tesseract_cmd = c
                return
    except Exception:
        pass


_configure_tesseract_cmd()


# ---------------------------------------------------------------------------
# 1. Preprocessing + OCR
# ---------------------------------------------------------------------------

def deskew(gray):
    """Straighten rotated scans. Returns deskewed image (no-op if angle tiny)."""
    import cv2
    import numpy as np
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5 or abs(angle) > 20:
        return gray
    (h, w) = gray.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def preprocess_image(image_path):
    """Photo -> clean normalized grayscale page: EXIF-orientation, gentle denoise,
    deskew, contrast enhancement (CLAHE), upscale, and white border.

    NOTE: Preserves continuous grayscale anti-aliased strokes rather than hard
    binary thresholding, which hollows out thin digits ('9' -> '8') in neural
    and LSTM OCR models. Returns temp path.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return image_path
    try:
        from PIL import Image, ImageOps
        pil = ImageOps.exif_transpose(Image.open(image_path).convert("RGB"))
        img = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        return image_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Check if image is already a clean digital render (high white background ratio)
    blown_ratio = float((gray > 240).mean())
    is_clean_synthetic = blown_ratio > 0.65 and float(gray.std()) > 20.0

    if not is_clean_synthetic:
        # 1. Gentle denoise for noisy camera photos
        gray = cv2.fastNlMeansDenoising(gray, None, 7, 7, 21)
        # 2. Deskew rotated scans
        try:
            gray = deskew(gray)
        except Exception:
            pass
        # 3. Contrast: CLAHE fixes uneven indoor lighting
        try:
            clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
        except Exception:
            pass

    # 4. Auto-crop excessive blank margins on clean digital renders so tiny text scales up
    if is_clean_synthetic:
        ys, xs = np.where(gray < 245)
        if len(ys) > 20:
            bh, bw = int(ys.max() - ys.min()), int(xs.max() - xs.min())
            if bh * bw < 0.25 * gray.shape[0] * gray.shape[1]:
                y1, y2 = max(0, int(ys.min()) - 25), min(gray.shape[0], int(ys.max()) + 25)
                x1, x2 = max(0, int(xs.min()) - 25), min(gray.shape[1], int(xs.max()) + 25)
                gray = gray[y1:y2, x1:x2]
                if max(gray.shape) < 900:
                    sc = min(3.0, 900.0 / max(gray.shape))
                    gray = cv2.resize(gray, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # 5. Upscale small images so character x-height is comfortable for OCR
    h, w = gray.shape
    if max(h, w) < 1600 and not is_clean_synthetic:
        scale = 1600.0 / max(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # 5. White border so characters touching edges aren't clipped
    gray = cv2.copyMakeBorder(gray, 20, 20, 20, 20,
                              cv2.BORDER_CONSTANT, value=255)
    tmp = image_path + "_pre.png"
    cv2.imwrite(tmp, gray)
    return tmp


# ---------------------------------------------------------------------------
# 1b. OCR output cleanup
# ---------------------------------------------------------------------------

# Vocabulary restricted strictly to structural document keywords & labels
# (surnames and general nouns excluded so applicant names are never mutated).
CORRECTOR_VOCAB = sorted({
    "government", "india", "unique", "identification", "authority", "uidai",
    "aadhaar", "card", "proof", "identity", "citizenship", "verification",
    "authentication", "verify", "scanning", "scan", "offline", "code",
    "masked", "resident", "download", "update", "valid", "validity",
    "passport", "republic", "income", "tax", "department",
    "permanent", "account", "number", "certificate", "board", "central",
    "secondary", "university", "examination", "examinations", "cbse",
    "school", "college", "marks", "statement", "grade", "subject",
    "theory", "practical", "additional", "scholastic", "achievements",
    "achieved", "obtained", "result", "controller", "roll", "registration",
    "enrolment", "enrollment", "session", "division", "course", "standard",
    "mother", "father", "guardian", "birth", "date", "gender", "male",
    "female", "transgender", "address", "nationality", "indian", "expiry",
    "issue", "place", "signature", "authorized", "authorisation",
    "driving", "licence", "license", "transport", "motor", "holder",
    "mathematics", "science", "english", "hindi", "social", "sanskrit",
    "physics", "chemistry", "biology", "computer", "literature",
    "percentage", "total", "maximum",
})


def fix_id_confusions(text):
    """OCR mixes 0/O, 1/I/l, 5/S, 8/B inside numbers — normalize digit runs."""
    def fix_run(m):
        run = m.group(0)
        digit_count = sum(c.isdigit() for c in run)
        # Do not alter valid PAN formats (5 letters + 4 digits + 1 letter)
        if re.fullmatch(r"[A-Za-z]{5}\d{4}[A-Za-z]", run):
            return run
        # Require majority (>60%) actual digits so alphanumeric codes stay intact
        if digit_count == len(run) or digit_count <= len(run) * 0.6:
            return run
        return (run.replace("O", "0").replace("o", "0").replace("D", "0")
                   .replace("I", "1").replace("l", "1").replace("|", "1")
                   .replace("S", "5").replace("B", "8"))
    return re.sub(r"\b[0-9OoDIl|SB]{4,}\b", fix_run, text)


def correct_ocr_text(text):
    """Lightweight structural label spell-fix using stdlib difflib.

    - Only snaps words >=5 chars that are very close (cutoff=0.86) to
      structural document keywords (never mutating proper names).
    - Never touches tokens containing digits.
    - Collapses single-digit OCR splits ('2 3 4 5') without merging multi-digit
      columns or Driving Licence blocks.
    """
    if not text:
        return text
    text = re.sub(r"(?<=[A-Za-z])['’`\"](?=[A-Za-z])", "", text)
    text = re.sub(r"(?<=[A-Za-z])@+", "", text)
    text = re.sub(r"(?<=[A-Za-z])['’`\"](?=\s|$)", "", text)

    def fix_word(m):
        w = m.group(0)
        if len(w) < 5 or not w.isalpha():
            return w
        low = w.lower()
        if low in CORRECTOR_VOCAB:
            return w
        hit = difflib.get_close_matches(low, CORRECTOR_VOCAB, n=1, cutoff=0.86)
        if hit:
            fixed = hit[0]
            if w.isupper():
                return fixed.upper()
            if w[0].isupper():
                return fixed.capitalize()
            return fixed
        return w

    text = re.sub(r"\b[A-Za-z]{5,}\b", fix_word, text)
    text = fix_id_confusions(text)
    # Collapse isolated single-digit runs split by OCR (e.g., "2 3 4 5" -> "2345")
    # without collapsing "HR03 20250000058" or "085 100" table columns.
    text = re.sub(r"\b(\d)(?: (\d)){3,}\b",
                  lambda m: m.group(0).replace(" ", ""), text)
    return text


def canonicalize_labels(text):
    """Rewrite OCR-mangled labels to their canonical form for display."""
    if not text:
        return text
    rules = [
        (r"\bRull\b", "Roll"), (r"\bRell\b", "Roll"),
        (r"\bR0ll\b", "Roll"), (r"\bRoII\b", "Roll"),
        (r"\bRgn\b", "Reg"),
        (r"\bD0B\b", "DOB"),
        (r"\bGennder\b", "Gender"), (r"\bGendar\b", "Gender"),
        (r"\bAdhsar\b", "Aadhaar"), (r"\bAadhar\b", "Aadhaar"),
        (r"\bAdhar\b", "Aadhaar"),
        (r"\bMuthet\b", "Mother"),
        (r"\blng\b", "Lang"),
        (r"\bTHi\b", "The"),
        (r"\bNa\.", "No."),
        (r"\bCUN\b(?=\s+CERTIFICATE)", "CUM"),
    ]
    for pat, repl in rules:
        text = re.sub(pat, repl, text)
    return text


def coherence_of(text):
    """0..1 score: how much does this look like real readable document text."""
    if not text or not text.strip():
        return 0.0
    words = re.findall(r"[A-Za-z]{3,}", text.lower())
    if not words:
        return 0.0
    tokens = re.findall(r"\S+", text)
    alpha_tokens = sum(1 for t in tokens if re.search(r"[A-Za-z]{3,}", t))
    token_ratio = alpha_tokens / max(len(tokens), 1)
    avg_len = sum(len(w) for w in words) / len(words)
    vocab_hits = sum(1 for w in words if w in CORRECTOR_VOCAB)
    vocab_ratio = vocab_hits / max(len(words), 4)
    # Require at least 2 words for a high score so 1-word noise ("av") never scores 0.70
    word_count_factor = min(len(words) / 4.0, 1.0)
    score = word_count_factor * (
        0.40 * token_ratio
        + 0.25 * (1.0 if 3.5 <= avg_len <= 9.5 else 0.2)
        + 0.35 * min(vocab_ratio * 3.5, 1.0)
    )
    return round(score, 3)


def extract_text_pdf(pdf_path):
    """Extract embedded text across all pages (digital PDFs), else rasterize pages for OCR."""
    # Attempt 1: PyMuPDF (extracts digital text or renders pages to PNG)
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        parts = [page.get_text() for page in doc]
        text = "\n".join(parts).strip()
        if len(text) > 30:
            doc.close()
            return text, 0.95, "pymupdf-text"
        # Scanned PDF — render up to 3 pages vertically stitched into one image
        import cv2
        import numpy as np
        imgs = []
        for idx in range(min(len(doc), 3)):
            pix = doc[idx].get_pixmap(dpi=200)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            elif pix.n == 3:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            imgs.append(arr)
        doc.close()
        if imgs:
            max_w = max(im.shape[1] for im in imgs)
            padded = [cv2.copyMakeBorder(im, 0, 0, 0, max_w - im.shape[1],
                                         cv2.BORDER_CONSTANT, value=(255, 255, 255))
                      for im in imgs]
            combined = np.vstack(padded)
            img_path = pdf_path + "_page1.png"
            cv2.imwrite(img_path, combined)
            return img_path, 0.0, "render-for-ocr"
    except ImportError:
        pass
    except Exception:
        pass

    # Attempt 2: pypdf for digital PDF text layer
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(parts).strip()
        if len(text) > 20:
            return text, 0.92, "pypdf-text"
    except Exception:
        pass

    # Attempt 3: pypdfium2 for scanned PDF rasterization (when PyMuPDF is not installed)
    try:
        import pypdfium2 as pdfium
        import cv2
        import numpy as np
        pdf = pdfium.PdfDocument(pdf_path)
        imgs = []
        for idx in range(min(len(pdf), 3)):
            page = pdf[idx]
            pil_img = page.render(scale=200 / 72.0).to_pil()
            arr = cv2.cvtColor(np.asarray(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)
            imgs.append(arr)
        pdf.close()
        if imgs:
            max_w = max(im.shape[1] for im in imgs)
            padded = [cv2.copyMakeBorder(im, 0, 0, 0, max_w - im.shape[1],
                                         cv2.BORDER_CONSTANT, value=(255, 255, 255))
                      for im in imgs]
            combined = np.vstack(padded)
            img_path = pdf_path + "_page1.png"
            cv2.imwrite(img_path, combined)
            return img_path, 0.0, "render-for-ocr"
    except Exception:
        pass

    # Attempt 4: pdf2image fallback
    try:
        from pdf2image import convert_from_path
        import cv2
        import numpy as np
        pil_pages = convert_from_path(pdf_path, dpi=200, first_page=1, last_page=3)
        if pil_pages:
            imgs = [cv2.cvtColor(np.asarray(p.convert("RGB")), cv2.COLOR_RGB2BGR) for p in pil_pages]
            max_w = max(im.shape[1] for im in imgs)
            padded = [cv2.copyMakeBorder(im, 0, 0, 0, max_w - im.shape[1],
                                         cv2.BORDER_CONSTANT, value=(255, 255, 255))
                      for im in imgs]
            combined = np.vstack(padded)
            img_path = pdf_path + "_page1.png"
            cv2.imwrite(img_path, combined)
            return img_path, 0.0, "render-for-ocr"
    except Exception as e:
        return "", 0.0, f"pdf-error: scanned PDF could not be rendered ({e})"

    return "", 0.0, "pdf-error: scanned PDF has no text layer and no PDF renderer available"


def run_tesseract(image_path):
    """Tesseract with document-tuned config. Tries PSM 6 (uniform block),
    falls back to PSM 3 (fully automatic) if output is sparse."""
    import pytesseract
    from PIL import Image
    img = Image.open(image_path)
    base_cfg = "--oem 1 --psm 6 -c preserve_interword_spaces=1"
    last = None
    best_res = None
    for cfg in (base_cfg, "--oem 1 --psm 3"):
        try:
            data = pytesseract.image_to_data(img, config=cfg,
                                             output_type=pytesseract.Output.DICT)
        except Exception as e:
            last = e
            continue
        words, confs = [], []
        for w, c in zip(data.get("text", []), data.get("conf", [])):
            try:
                c = float(c)
            except (TypeError, ValueError):
                continue
            if w and w.strip() and c >= 0:
                words.append(w)
                confs.append(c / 100.0)
        full_text = pytesseract.image_to_string(img, config=cfg)
        if full_text and full_text.strip():
            avg_conf = round(sum(confs) / len(confs), 3) if confs else 0.6
            boxes = [{"text": w, "confidence": round(c, 3)}
                     for w, c in zip(words[:50], confs[:50])]
            cand = (full_text, avg_conf, boxes)
            if best_res is None or (coherence_of(full_text), avg_conf) > (coherence_of(best_res[0]), best_res[1]):
                best_res = cand
            if coherence_of(full_text) >= 0.55 and avg_conf >= 0.65:
                return cand
    if best_res is not None:
        return best_res
    raise RuntimeError(f"tesseract produced no usable text ({last})")


def run_rapidocr(image_path):
    """Fallback OCR via RapidOCR-ONNX (cached singleton).
    Returns (full_text, avg_confidence, line_details)."""
    global _RAPIDOCR_INSTANCE
    from rapidocr_onnxruntime import RapidOCR
    if _RAPIDOCR_INSTANCE is None:
        _RAPIDOCR_INSTANCE = RapidOCR()
    result, _ = _RAPIDOCR_INSTANCE(image_path)
    lines, scores, boxes = [], [], []
    if result:
        for item in result:
            try:
                txt, conf = item[1], float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            lines.append(txt)
            scores.append(conf)
            boxes.append({"text": txt, "confidence": round(conf, 3)})
    full_text = "\n".join(lines)
    avg_conf = round(sum(scores) / len(scores), 3) if scores else 0.0
    return full_text, avg_conf, boxes


def run_paddleocr(image_path):
    """Primary OCR via PaddleOCR (cached singleton).
    Returns (full_text, avg_confidence, line_details)."""
    global _PADDLEOCR_INSTANCE
    from paddleocr import PaddleOCR
    if _PADDLEOCR_INSTANCE is None:
        _PADDLEOCR_INSTANCE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    result = _PADDLEOCR_INSTANCE.ocr(image_path, cls=True)
    lines, scores, boxes = [], [], []
    if result and result[0]:
        for line in result[0]:
            box, (txt, conf) = line[0], line[1]
            lines.append(txt)
            scores.append(float(conf))
            boxes.append({"text": txt, "confidence": round(float(conf), 3)})
    full_text = "\n".join(lines)
    avg_conf = round(sum(scores) / len(scores), 3) if scores else 0.0
    return full_text, avg_conf, boxes


def do_ocr(file_path):
    """Unified OCR entry. Returns dict with text, confidence, engine."""
    ext = os.path.splitext(file_path)[1].lower()
    temp_files = []

    if ext == ".pdf":
        out, conf, how = extract_text_pdf(file_path)
        if how in ("pymupdf-text", "pypdf-text"):
            return {"text": out, "confidence": conf, "engine": f"{how.split('-')[0]} (digital pdf)"}
        if how.startswith("pdf-error"):
            return {"text": "", "confidence": 0.0, "engine": how}
        file_path = out  # rendered image -> continue to OCR
        temp_files.append(out)

    processed = preprocess_image(file_path)
    if processed != file_path:
        temp_files.append(processed)
    cleanup = lambda: [os.remove(p) for p in temp_files if os.path.exists(p)]
    try:
        candidates = []  # (text, conf, boxes, "engine+rot")
        errors = []
        orientations = [(processed, 0)]
        try:
            import cv2
            base = cv2.imread(processed, cv2.IMREAD_COLOR)
            if base is not None:
                codes = [(cv2.ROTATE_90_CLOCKWISE, 90),
                         (cv2.ROTATE_180, 180),
                         (cv2.ROTATE_90_COUNTERCLOCKWISE, 270)]
                for code, deg in codes:
                    rp = f"{processed}_r{deg}.png"
                    if cv2.imwrite(rp, cv2.rotate(base, code)):
                        orientations.append((rp, deg))
                        temp_files.append(rp)
        except Exception as e:
            errors.append(f"rotate [{e}]")

        for img_path, deg in orientations:
            for name, fn in (("paddleocr", run_paddleocr),
                             ("rapidocr", run_rapidocr),
                             ("tesseract", run_tesseract)):
                try:
                    t, c, b = fn(img_path)
                    if t and t.strip():
                        tag = name if deg == 0 else f"{name}+rot{deg}"
                        candidates.append((t, c, b, tag))
                except Exception as e:
                    if deg == 0:
                        errors.append(f"{name} [{e}]")
            # Early exit only when BOTH coherence AND engine confidence are solid
            if candidates and any(coherence_of(t) >= 0.65 and c >= 0.65 for t, c, _, _ in candidates):
                break

        if not candidates:
            return {"text": "", "confidence": 0.0,
                    "engine": "ocr-unavailable: " + " | ".join(errors) + ". "
                              "FIX: (a) digital PDFs work without OCR — try one; "
                              "(b) for photos run: pip install rapidocr-onnxruntime "
                              "(pip-only, easiest) OR install tesseract + pip install pytesseract.",
                    "lines": []}

        # Rank by combined quality (coherence + engine confidence) so upside-down
        # low-confidence reads never beat upright high-confidence reads.
        ranked = sorted(candidates,
                        key=lambda cb: (round(0.6 * coherence_of(cb[0]) + 0.4 * cb[1], 3), cb[1]),
                        reverse=True)
        raw_text, conf, boxes, winner = ranked[0]
        engine_used = winner + (" (best of %d)" % len(candidates)
                                if len(candidates) > 1 else "")

        mrz_lines, mrz_hint = [], ""
        try:
            from cross_field import find_mrz_lines, detect_mrz
            for t, _, _, _ in candidates:
                for pair in find_mrz_lines(t.upper().replace(" ", "")):
                    if pair not in mrz_lines:
                        mrz_lines.append(pair)
            for t, _, _, _ in ranked:
                blob = detect_mrz(t)
                if blob and len(blob) > len(mrz_hint):
                    mrz_hint = blob
        except Exception:
            pass
        text = canonicalize_labels(correct_ocr_text(raw_text))
        return {"text": text, "confidence": conf, "engine": engine_used,
                "lines": boxes[:50],
                "enginesTried": [c[3] for c in candidates],
                "mrz_lines": mrz_lines[:2], "mrz_hint": mrz_hint}
    except Exception as e:
        return {"text": "", "confidence": 0.0, "engine": f"ocr-error: {e}", "lines": []}
    finally:
        try:
            cleanup()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 2. Syntax verification (format / regex)
# ---------------------------------------------------------------------------

def verhoeff_suggest(num_str):
    """Single-digit OCR-error correction for 12-digit numbers.

    Tries ONLY known optical character confusions (0<->6/8/9, 1<->7/4, 2<->7,
    3<->8/5, 5<->6/8, etc.). Does NOT run an exhaustive 0-9 loop because
    in the Verhoeff dihedral group D5, EVERY 11-digit prefix has a valid 12th
    check digit, so an exhaustive loop would falsely 'heal' 100% of fake numbers!
    """
    if not num_str or len(num_str) != 12 or not num_str.isdigit():
        return None
    confusions = {
        "0": "8",
        "1": "7",
        "2": "",
        "3": "8",
        "4": "",
        "5": "6",
        "6": "58",
        "7": "1",
        "8": "036",
        "9": "",
    }
    tried = set()

    for i, ch in enumerate(num_str):
        for new_ch in confusions.get(ch, ""):
            if new_ch == ch or (i, new_ch) in tried:
                continue
            if i == 0 and new_ch in "01":
                continue  # Aadhaar never starts with 0 or 1
            tried.add((i, new_ch))
            cand = num_str[:i] + new_ch + num_str[i + 1:]
            if verhoeff_check(cand):
                return (cand, i, ch, new_ch)
    return None


def verhoeff_check(num_str):
    """Verhoeff checksum — real Aadhaar numbers pass this."""
    if not num_str or not str(num_str).isdigit():
        return False
    d = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
         [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
         [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
         [9,8,7,6,5,4,3,2,1,0]]
    p = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
         [8,9,1,6,0,4,3,7,2,5],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
         [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
    c = 0
    for i, ch in enumerate(reversed(str(num_str))):
        c = d[c][p[i % 8][int(ch)]]
    return c == 0


def _display_value(name, raw):
    """Clean check value for the frontend: collapse whitespace and mask
    sensitive identifiers (Aadhaar/VID/PAN/passport/mobile) middle-out."""
    val = " ".join(str(raw).split())[:60]
    if name in ("aadhaar_number", "vid", "pan_number", "passport_no",
                "dl_no", "mobile", "roll_no_format"):
        from number_checks import mask_id
        m = re.search(r"(?:[A-Z0-9][-\s]?){8,}", val)
        if m:
            flat = re.sub(r"[-\s]+", "", m.group(0))
            val = val.replace(m.group(0), mask_id(flat))
    return val


def syntax_checks(text, doc_type):
    """Regex checks -> list of {check, status, message, value} + syntax_score."""
    tpl = TEMPLATES.get(doc_type, TEMPLATES["generic"])
    checks = []
    passed = 0
    total = 0

    alias_of = {"roll_no_format": "roll_no", "grade_token": "grade",
                "subject_marks": "marks", "marks_words": "marks",
                "mrz": "passport_no"}
    alias_hits = {}

    for name, pat in tpl["patterns"].items():
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if not m:
            if doc_type == "generic" and name not in tpl.get("required", []):
                continue
            if name in alias_of:
                continue
            total += 1
            is_required = name in tpl.get("required", [])
            checks.append({"check": f"syntax:{name}", "status": "fail" if is_required else "warn",
                           "message": f"{'Missing required' if is_required else 'Not found (optional)'}: {name.replace('_',' ')}",
                           "value": None})
            continue
        if name in alias_of:
            val_str = " ".join(m.group(0).split())[:60]
            if name == "mrz":
                # Extract actual passport number from TD3 MRZ line 2 if present
                from cross_field import find_mrz_lines
                pairs = find_mrz_lines(text.upper().replace(" ", ""))
                if pairs:
                    val_str = pairs[0][1][0:9].replace("<", "") + f" (via MRZ: {pairs[0][0][:20]}...)"
            alias_hits[alias_of[name]] = (name, val_str)
            continue
        total += 1
        extra = ""
        status = "pass"
        if name == "aadhaar_number":
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) == 12 and digits[0] not in ("0", "1"):
                if verhoeff_check(digits):
                    extra = " (Verhoeff checksum valid)"
                else:
                    fix = verhoeff_suggest(digits)
                    if fix:
                        _, pos, old_ch, new_ch = fix
                        extra = (f" (valid after allowing one likely OCR misread "
                                 f"at digit {pos + 1}: {old_ch}->{new_ch})")
                    else:
                        status = "fail"
                        extra = (" (Verhoeff checksum failed — invalid 12-digit Aadhaar number)")
            else:
                status = "fail"
                extra = " (must be 12 digits, not starting with 0/1)"
        if name == "pan_number":
            pan = m.group(0).upper()
            if pan[3] not in "PCHFATBLJGE":
                status = "warn"
                extra = " (4th character should be holder type, e.g. P)"
        checks.append({"check": f"syntax:{name}", "status": status,
                       "message": f"Found {name.replace('_',' ')}{extra}",
                       "value": _display_value(name, m.group(0))})
        if status == "pass":
            passed += 1
        elif status == "warn":
            passed += 0.4

    # Rescue: primary failed/warned but alternate OCR-proof evidence passed.
    for c in checks:
        base = c["check"].split(":", 1)[1]
        if base in alias_hits and c["status"] in ("fail", "warn"):
            old = c["status"]
            alias_name, alias_val = alias_hits[base]
            c["status"] = "pass"
            c["message"] = (f"Found {base.replace('_',' ')} "
                            f"(via {alias_name.replace('_',' ')}: {alias_val})")
            c["value"] = c["value"] or alias_val
            passed += 1.0 if old == "fail" else 0.6

    # Generic quality signals
    total += 1
    words = re.findall(r"[A-Za-z]{2,}", text)
    if len(words) >= 10:
        checks.append({"check": "syntax:text_volume", "status": "pass",
                       "message": f"Readable text extracted ({len(words)} words)", "value": len(words)})
        passed += 1
    else:
        checks.append({"check": "syntax:text_volume", "status": "fail",
                       "message": "Very little readable text — image may be blurry/dark",
                       "value": len(words)})

    syntax_score = round(passed / total, 3) if total else 0.0
    return checks, syntax_score


def _corpus_similarities(text, types):
    """Delegates to classifier.similarities — one implementation only."""
    from classifier import similarities
    return similarities(text, types)


def template_similarity(text, tpl_name_or_tpl):
    """TF-IDF cosine between one template and document text (0..1)."""
    name = tpl_name_or_tpl if isinstance(tpl_name_or_tpl, str) \
        else next((t for t in TEMPLATES if TEMPLATES[t] is tpl_name_or_tpl),
                  "generic")
    return _corpus_similarities(text, list(TEMPLATES))[name]


def detect_document_type(text):
    """Score text against every template -> (best_type, {type: score})."""
    from classifier import classify
    result = classify(text)
    sims = result["raw_similarities"]
    if not result["confident"]:
        return "generic", sims
    return result["predicted"], sims


# ---------------------------------------------------------------------------
# 3. Semantic verification (meaning / consistency)
# ---------------------------------------------------------------------------

def semantic_checks(text, doc_type, ocr_conf, is_best_fit=False):
    """Keyword coverage (TF-IDF) + logic consistency + coherence."""
    tpl = TEMPLATES.get(doc_type, TEMPLATES["generic"])
    checks = []
    score_parts = []

    # --- 3a. Keyword / template similarity via TF-IDF cosine ---
    sim = template_similarity(text, tpl)
    pass_at, warn_at = (0.08, 0.03) if doc_type == "generic" else (0.15, 0.05)
    if sim >= pass_at:
        score_parts.append(sim)
        checks.append({"check": "semantic:template_match", "status": "pass",
                       "message": f"Document reads like a {tpl['label']} (similarity {sim:.2f})",
                       "value": round(sim, 3)})
    elif sim >= warn_at:
        score_parts.append(sim)
        msg = (f"Partial match to {tpl['label']} template (similarity {sim:.2f}) — "
               f"this is still the best-fit type, OCR noise is diluting the wording"
               if is_best_fit else
               f"Weak match to {tpl['label']} template (similarity {sim:.2f}) — wrong doc type?")
        checks.append({"check": "semantic:template_match", "status": "warn",
                       "message": msg,
                       "value": round(sim, 3)})
    elif doc_type == "generic":
        score_parts.append(warn_at)
        checks.append({"check": "semantic:template_match", "status": "warn",
                       "message": f"No template resemblance (similarity {sim:.2f}) — "
                                  f"uncharacterized document, neutral (not a failure)",
                       "value": round(sim, 3)})
    else:
        score_parts.append(sim)
        checks.append({"check": "semantic:template_match", "status": "fail",
                       "message": f"Text does not resemble a {tpl['label']} (similarity {sim:.2f})",
                       "value": round(sim, 3)})

    # --- 3b. Required-field semantics: does the doc actually identify someone? ---
    has_name_like = bool(re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b|\b[A-Z]{3,}\s+[A-Z]{2,}\b", text))
    has_id_num = bool(re.search(r"\d{4,}", text))
    has_date = bool(GENERIC_DATE.search(text))
    present = sum([has_name_like, has_id_num, has_date])
    field_score = present / 3
    score_parts.append(field_score)
    checks.append({"check": "semantic:entity_completeness", "status": "pass" if present >= 2 else ("warn" if present == 1 else "fail"),
                   "message": f"Identity signals: name={'yes' if has_name_like else 'no'}, id-number={'yes' if has_id_num else 'no'}, date={'yes' if has_date else 'no'}",
                   "value": f"{present}/3"})

    # --- 3c. Date logic: DOB must not be in the future; expiry dates allowed in future ---
    date_score = 0.5
    dob_dates = []
    all_dates = []
    for m in GENERIC_DATE.finditer(text):
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000 if y < 50 else 1900
            dt = datetime(y, mo, d)
            all_dates.append(dt)
            ctx = text[max(0, m.start() - 30):m.end()].lower()
            if any(k in ctx for k in ("dob", "birth", "born")):
                dob_dates.append(dt)
        except ValueError:
            continue
    now = datetime.now()
    if all_dates:
        # Future DOB is always invalid; future dates on non-Passport/DL docs without expiry labels are suspicious
        has_expiry_doc = doc_type in ("passport", "driving_licence", "certificate")
        bad_future = [d for d in dob_dates if d > now] if has_expiry_doc else [d for d in all_dates if d > now]
        if bad_future:
            checks.append({"check": "semantic:date_logic", "status": "fail",
                           "message": f"{len(bad_future)} birth/issue date(s) lie in the future — suspicious",
                           "value": [d.strftime("%d/%m/%Y") for d in bad_future][:3]})
            date_score = 0.0
        else:
            checks.append({"check": "semantic:date_logic", "status": "pass",
                           "message": f"All {len(all_dates)} date(s) are chronologically plausible",
                           "value": [d.strftime("%d/%m/%Y") for d in all_dates][:3]})
            date_score = 1.0
    else:
        checks.append({"check": "semantic:date_logic", "status": "warn",
                       "message": "No dates found to validate", "value": None})
    score_parts.append(date_score)

    # --- 3d. Coherence: ratio of dictionary-like words (OCR garbage detection) ---
    words = re.findall(r"[A-Za-z]{3,}", text.lower())
    if words:
        alpha_ratio = sum(1 for w in words if re.fullmatch(r"[a-z]+", w)) / len(words)
        avg_len = sum(len(w) for w in words) / len(words)
        coherent = alpha_ratio > 0.7 and 3.5 <= avg_len <= 9
        coh_score = 1.0 if coherent else 0.3
        checks.append({"check": "semantic:coherence", "status": "pass" if coherent else "warn",
                       "message": f"Text coherence {'looks natural' if coherent else 'looks garbled — retake photo'} (alpha-ratio {alpha_ratio:.2f})",
                       "value": round(alpha_ratio, 3)})
    else:
        coh_score = 0.0
        checks.append({"check": "semantic:coherence", "status": "fail",
                       "message": "No readable words — cannot assess meaning", "value": 0})
    score_parts.append(coh_score)

    semantic_score = round(sum(score_parts) / len(score_parts), 3)
    return checks, semantic_score


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def final_verdict(ocr_conf, syntax_score, semantic_score):
    overall = round(0.35 * ocr_conf + 0.35 * syntax_score + 0.30 * semantic_score, 3)
    if overall >= 0.70 and ocr_conf >= 0.5:
        verdict = "VERIFIED"
    elif overall >= 0.40:
        verdict = "NEEDS_REVIEW"
    else:
        verdict = "SUSPICIOUS"
    return verdict, overall


def _ocr_evidence(ocr_conf, has_text):
    """OCR evidence: readability only — NEVER fraud evidence."""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config", "documentVerification.json")
    try:
        with open(cfg_path) as f:
            th = json.load(f)["ocr"]
    except Exception:
        th = {"pass": 0.60, "review": 0.35}
    if not has_text:
        return {"status": "FAIL", "score": 0.0,
                "note": "No text extracted — readability failure, not fraud evidence"}
    if ocr_conf >= th["pass"]:
        return {"status": "PASS", "score": ocr_conf, "note": "Text readable"}
    if ocr_conf >= th["review"]:
        return {"status": "REVIEW", "score": ocr_conf,
                "note": "Text partially readable"}
    return {"status": "FAIL", "score": ocr_conf,
            "note": "Text barely readable — readability failure, not fraud evidence"}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: verify_document.py <file_path> <document_type>"}))
        return
    file_path, requested = sys.argv[1], sys.argv[2].lower()
    if requested not in list(TEMPLATES) + ["auto"]:
        requested = "auto"
    if not os.path.exists(file_path):
        print(json.dumps({"error": f"File not found: {file_path}"}))
        return

    from image_quality import check_image_quality, digital_pdf_quality
    from classifier import classify
    from verifiers import get_verifier
    from cross_field import validate as cross_validate
    from tamper import analyze as tamper_analyze
    from number_checks import scan_text as fab_scan
    from evidence import aggregate, decide

    is_pdf = os.path.splitext(file_path)[1].lower() == ".pdf"

    # ---- Layer 1: OCR (needed before quality-gating the pixels) ----
    ocr = do_ocr(file_path)
    text = ocr.get("text", "")
    ocr_conf = float(ocr.get("confidence", 0.0) or 0.0)
    engine = ocr.get("engine", "unknown")
    is_digital = is_pdf and "digital pdf" in engine

    # ---- Layer 2: Image Quality Gate (poor image != fake) ----
    if is_digital:
        quality = digital_pdf_quality()
        photo_path = None
    else:
        photo_path = file_path
        quality = check_image_quality(file_path)

    if not text.strip():
        if quality.get("status") == "FAIL":
            decision, reasons = "NEEDS_REVIEW", [
                "Insufficient image quality for reliable verification — retake the photo."]
        else:
            decision, reasons = "NEEDS_REVIEW", [
                f"OCR could not read any text ({engine}). "
                "Try better lighting / higher resolution."]
        print(json.dumps({
            "documentType": requested if requested != "auto" else "generic",
            "verdict": decision, "decision": decision, "reasons": reasons,
            "overallScore": 0.0, "ocrConfidence": 0.0,
            "syntaxScore": 0.0, "semanticScore": 0.0,
            "engine": engine, "extractedText": "",
            "image_quality": quality,
            "document_classification": {"predicted": "generic",
                                        "confidence": 0.0, "confident": False},
            "evidence": {"ocr": _ocr_evidence(0.0, False)},
            "checks": [{"check": "ocr:readable", "status": "fail",
                        "message": reasons[0], "value": None}],
        }))
        return

    # ---- Layer 3: Document Classification (probabilistic) ----
    cls_input = text + ("\n" + ocr.get("mrz_hint", "") if ocr.get("mrz_hint") else "")
    cls = classify(cls_input)
    detected = cls["predicted"] if cls["confident"] else "generic"
    if requested == "auto":
        doc_type, auto_used = detected, True
    else:
        doc_type, auto_used = requested, False
    classification = {"predicted": doc_type,
                      "confidence": cls["confidence"],
                      "alternatives": cls["alternatives"],
                      "raw_similarities": cls["raw_similarities"],
                      "confident": cls["confident"] or not auto_used,
                      "auto": auto_used}

    # ---- Layer 4: Document-Specific Verification (adapter) ----
    verifier = get_verifier(doc_type)
    struct = verifier.verify(text)
    syn_checks = struct["checks"]
    syntax_score = struct["score"]
    if struct.get("extra"):
        syn_checks = syn_checks + struct["extra"]

    # ---- Layer 5b: Fabrication screen (type-independent) ----
    fab_hits = fab_scan(text)
    if fab_hits:
        from doc_templates import KNOWN_STRUCTURED_FORMATS
        structured = set()
        for pats in KNOWN_STRUCTURED_FORMATS.values():
            for pat in pats:
                for m in re.finditer(pat, text, re.IGNORECASE):
                    structured.add(re.sub(r"[-\s]+", "", m.group(0)).upper())
        kept = [h for h in fab_hits
                if not (h.get("kind") == "entropy" and
                        any(h["number"] in s for s in structured))]
        if len(kept) != len(fab_hits):
            fab_hits = kept
    fab_status = "FAIL" if fab_hits else "PASS"

    # ---- Layer 5: Semantics ----
    sem_checks, semantic_score = semantic_checks(text, doc_type, ocr_conf,
                                                 is_best_fit=(detected == doc_type))
    if auto_used:
        sem_checks.insert(0, {"check": "semantic:auto_detect", "status": "pass",
                              "message": f"Auto-detected as {TEMPLATES[doc_type]['label']} "
                                         f"(confidence {cls['confidence']:.2f})",
                              "value": doc_type})
    elif detected != requested and detected != "generic":
        gap = cls["raw_similarities"].get(detected, 0) - cls["raw_similarities"].get(requested, 0)
        if gap > 0.12:
            hint_msg = (f"You selected {TEMPLATES[requested]['label']} but this looks "
                        f"more like a {TEMPLATES[detected]['label']} — switch type or use Auto-detect")
            sem_checks.insert(0, {"check": "semantic:type_hint", "status": "warn",
                                  "message": hint_msg,
                                  "value": detected})
            classification["type_hint_msg"] = hint_msg
    sem_status = ("PASS" if semantic_score >= 0.6 else
                  "REVIEW" if semantic_score >= 0.35 else "FAIL")

    # ---- Layer 6: Tamper Analysis (run before cross-field so QR payload can be cross-checked) ----
    tamper = tamper_analyze(photo_path, text, is_digital_pdf=is_digital)

    # ---- Layer 7: Cross-Field Validation ----
    cross = cross_validate(text, doc_type, struct.get("fields", {}),
                           mrz_lines=ocr.get("mrz_lines") or None,
                           qr_info=tamper.get("qr"))

    # ---- Layers 8–9: Evidence Aggregation + Decision ----
    evidence = {
        "ocr": _ocr_evidence(ocr_conf, True),
        "structure": {"status": struct["status"], "score": syntax_score},
        "semantic": {"status": sem_status, "score": semantic_score},
        "cross_field": {"status": cross["status"],
                        "conflicts": cross.get("conflicts", [])},
        "tamper": {"status": tamper["status"], "score": tamper.get("score"),
                   "flags": tamper.get("flags", []), "qr": tamper.get("qr")},
        "fabrication": {"status": fab_status, "hits": fab_hits,
                        "note": "Long ID numbers screened for fabricated patterns "
                                "(repetition/sequential/low variety)" if not fab_hits else
                                f"{len(fab_hits)} fabricated-looking number(s) found"},
        "cryptographic": {"status": "NOT_AVAILABLE",
                          "note": "Cryptographic authenticity not established — "
                                  "no official validator configured"},
        "image_quality": {"status": quality["status"] if quality["status"] != "N/A" else "PASS",
                          "score": quality["overall"]},
    }
    agg_score, breakdown = aggregate(doc_type, evidence)
    if classification.get("type_hint_msg"):
        classification["type_hint"] = classification["type_hint_msg"]
    decision, reasons, findings, risk, confidence = decide(
        doc_type, evidence, agg_score, classification, quality,
        struct_checks=syn_checks)

    if doc_type == "pan" and decision == "VERIFIED":
        reasons.append("Note: valid PAN format does not mean the PAN is genuine; "
                       "no registry check performed.")
    from evidence import load_config as _load_cfg
    mode = _load_cfg().get("verification_mode", "strict")
    critical = [f for f in findings if f["severity"] == "CRITICAL"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]
    highs = [f for f in findings if f["severity"] == "SUSPICIOUS"]
    passed = [f for f in findings if f["severity"] in ("PASS", "INFO")]
    explanation = reasons[0] if reasons else decision
    limitations = [f["message"] for f in findings if f["severity"] == "INFO"]

    print(json.dumps({
        "documentType": doc_type,
        "requestedType": requested,
        "detectedType": detected,
        "documentLabel": TEMPLATES[doc_type]["label"],
        "verdict": decision, "decision": decision, "reasons": reasons,
        "risk_level": risk, "confidence": confidence,
        "verification_mode": mode,
        "overallScore": agg_score,
        "ocrConfidence": ocr_conf,
        "syntaxScore": syntax_score,
        "semanticScore": semantic_score,
        "engine": engine,
        "extractedText": text[:4000],
        "fields": {k: v for k, v in struct.get("fields", {}).items() if not k.startswith("_")},
        "image_quality": quality,
        "document_classification": classification,
        "evidence": evidence,
        "evidence_breakdown": breakdown,
        "findings": findings,
        "critical_findings": critical,
        "warnings": warnings,
        "high_findings": highs,
        "passed_checks": passed,
        "suspicious_regions": tamper.get("regions", []),
        "limitations": limitations,
        "explanation": explanation,
        "cross_field": cross,
        "tamper": tamper,
        "checks": syn_checks + sem_checks,
        "lines": ocr.get("lines", [])[:30],
    }))


if __name__ == "__main__":
    main()
