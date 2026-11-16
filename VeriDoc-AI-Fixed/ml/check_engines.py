"""[VeriDoc ML Engine] Report which OCR/text backends are usable in this environment.

Used by server.js /api/health so the frontend can warn when photo OCR
is unavailable (digital PDFs still work via pypdf/PyMuPDF).

Usage: python3 ml/check_engines.py   -> prints single JSON line.
"""
import importlib.util
import json
import os
import shutil


def _try_import(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _find_tesseract_binary():
    path = shutil.which("tesseract")
    if path:
        return path
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None


def main():
    tess_bin = _find_tesseract_binary()
    engines = {
        "paddleocr": _try_import("paddleocr"),
        "rapidocr": _try_import("rapidocr_onnxruntime"),
        "pytesseract_lib": _try_import("pytesseract"),
        "tesseract_binary": tess_bin is not None,
        "pymupdf": _try_import("fitz"),
        "pypdfium2": _try_import("pypdfium2"),
        "pypdf": _try_import("pypdf"),
        "opencv": _try_import("cv2"),
        "sklearn": _try_import("sklearn"),
        "tfidf_engine": "sklearn" if _try_import("sklearn") else "builtin-tfidf",
    }
    engines["image_ocr_ready"] = bool(
        engines["paddleocr"] or engines["rapidocr"]
        or (engines["pytesseract_lib"] and engines["tesseract_binary"])
    )
    engines["pdf_text_ready"] = bool(
        engines["pymupdf"] or engines["pypdf"] or engines["pypdfium2"]
    )
    print(json.dumps(engines))


if __name__ == "__main__":
    main()
