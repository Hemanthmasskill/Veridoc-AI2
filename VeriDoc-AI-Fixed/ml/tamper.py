"""TamperAnalyzer — humble screening signals, never proof of forgery.

Where technically feasible with installed libs (OpenCV/Pillow/numpy only):
  - QR detection + decoding (cv2.QRCodeDetector). Reports three SEPARATE
    facts: detected / readable / data_consistent. Cryptographic authenticity
    is NEVER claimed: signature_valid is always False with an explicit note.
  - ELA recompression response (JPEG resave difference, excluding uniform
    blank margins and normal text-edge ringing).
  - Copy-move block similarity detection (ORB / DCT patch correlation on
    non-blank content patches to detect cloned stamps/text blocks).
  - Noise/sharpness inconsistency across content-bearing cells (blank paper
    margins are excluded so they never manufacture false outlier flags).

Output: {"status": PASS|REVIEW|NOT_AVAILABLE, "score": 0..1, ...}.
A REVIEW here means "needs a human look", never "fake".
"""
import os


def _qr_check(image_path, text):
    """Detect + decode QR. Compare payload vs OCR facts for consistency."""
    out = {"detected": False, "readable": False, "data_consistent": None,
           "signature_valid": False,
           "note": "Cryptographic authenticity not established"}
    try:
        import cv2
    except ImportError:
        out["note"] += " (OpenCV unavailable)"
        return out
    try:
        img = cv2.imread(image_path)
        if img is None:
            return out
        det = cv2.QRCodeDetector()
        data, points, _ = det.detectAndDecode(img)
        if points is not None and len(points) > 0:
            out["detected"] = True
        if data:
            out["readable"] = True
            out["payload_preview"] = data[:120]
            import re
            flat_text = re.sub(r"(?<=\d)\s+(?=\d)", "", text or "")
            flat_data = re.sub(r"(?<=\d)\s+(?=\d)", "", data)
            nums_qr = set(re.findall(r"\d{4,}", flat_data))
            nums_txt = set(re.findall(r"\d{4,}", flat_text))
            words_txt = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", text or "")[:30]]
            # Only evaluate consistency when the QR payload contains readable text/digits
            # (UIDAI secure QR is a giant base-10 integer >200 digits; don't false-fail it).
            is_opaque_numeric_qr = bool(re.fullmatch(r"\d{100,}", data.strip()))
            if is_opaque_numeric_qr:
                out["data_consistent"] = None
                out["note"] = "Encoded secure QR detected (cryptographic payload not decoded)"
            elif nums_qr or words_txt:
                num_match = bool(nums_qr & nums_txt)
                name_match = any(w in data.lower() for w in words_txt)
                out["data_consistent"] = bool(num_match or name_match)
    except Exception as e:
        out["note"] += f" (qr error: {e})"
    return out


def _ela_regions(image_path, max_regions=3):
    """ELA hotspot boxes: recompress at quality=90, threshold the difference
    map over non-blank regions, and locate localized high-anomaly patches."""
    try:
        import cv2
        import numpy as np
        from PIL import Image
        import io
        img = Image.open(image_path).convert("RGB")
        img.thumbnail((800, 800))
        w, h = img.size
        if min(w, h) < 120:
            return []
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        buf.seek(0)
        re_img = Image.open(buf)
        arr_orig = np.asarray(img).astype(np.float32)
        arr_re = np.asarray(re_img).astype(np.float32)
        diff = np.abs(arr_orig - arr_re).mean(axis=2)

        # Compute statistics over content pixels only (local std > 8) so blank
        # white paper doesn't drag mean/std near zero and flag normal text edges.
        gray = cv2.cvtColor(arr_orig.astype(np.uint8), cv2.COLOR_RGB2GRAY)
        local_mean = cv2.blur(gray.astype(np.float32), (15, 15))
        local_sq = cv2.blur((gray.astype(np.float32) ** 2), (15, 15))
        local_std = np.sqrt(np.maximum(0.0, local_sq - local_mean ** 2))
        content_mask = local_std > 10.0
        if content_mask.sum() < 200:
            return []

        content_diffs = diff[content_mask]
        thr = float(content_diffs.mean() + 3.2 * content_diffs.std())
        # Require a meaningful absolute compression residual (> 18.0 per channel)
        mask = ((diff > max(thr, 18.0)) & content_mask).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if area < 0.008 * w * h or area > 0.35 * w * h:
                continue
            patch_diff = float(diff[y:y + bh, x:x + bw].mean())
            if patch_diff < 12.0:
                continue
            boxes.append((area, {"x": round(x / w, 3), "y": round(y / h, 3),
                                 "width": round(bw / w, 3),
                                 "height": round(bh / h, 3),
                                 "signals": ["compression_mismatch"],
                                 "severity": "MEDIUM",
                                 "label": "compression response differs here"}))
        boxes.sort(reverse=True)
        return [b for _, b in boxes[:max_regions]]
    except Exception:
        return []


def _sharpness_regions(image_path, max_regions=3):
    """4x4 sharpness grid over CONTENT-BEARING cells only: blank background
    cells (low pixel std-dev) are excluded so white document margins never
    get flagged as 'local_noise_mismatch'."""
    try:
        import cv2
        import numpy as np
        gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return []
        h, w = gray.shape
        if min(h, w) < 200:
            return []
        gh, gw = h // 4, w // 4
        cells = []
        for i in range(4):
            for j in range(4):
                cell = gray[i * gh:(i + 1) * gh, j * gw:(j + 1) * gw]
                std_val = float(cell.std())
                lap_var = float(cv2.Laplacian(cell, cv2.CV_64F).var()) + 1e-6
                # Only include cells that actually contain text/graphics (std >= 18)
                if std_val >= 18.0 and lap_var >= 15.0:
                    cells.append((i, j, lap_var, std_val))

        # Need at least 4 content cells to compare sharpness consistency
        if len(cells) < 4:
            return []

        vars_ = [c[2] for c in cells]
        med = float(np.median(vars_))
        out = []
        for i, j, v, _ in cells:
            # Flag only extreme sharpness/blur splicing outliers (>12x or <1/12x of content median)
            if v < med / 12.0 or v > med * 12.0:
                out.append({"x": round(j / 4, 3), "y": round(i / 4, 3),
                            "width": 0.25, "height": 0.25,
                            "signals": ["local_noise_mismatch"],
                            "severity": "MEDIUM",
                            "label": "sharpness/noise differs from surrounding content"})
                if len(out) >= max_regions:
                    break
        return out
    except Exception:
        return []


def _copy_move_regions(image_path, max_regions=2):
    """Detect duplicated non-blank patches via quantized DCT block descriptors."""
    try:
        import cv2
        import numpy as np
        gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return []
        h, w = gray.shape
        if min(h, w) < 160:
            return []
        scale = min(1.0, 512.0 / max(h, w))
        if scale < 1.0:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        sh, sw = gray.shape
        bs = 32
        step = 16
        blocks = {}
        clones = []
        for y in range(0, sh - bs, step):
            for x in range(0, sw - bs, step):
                patch = gray[y:y + bs, x:x + bs].astype(np.float32)
                if patch.std() < 22.0:
                    continue  # skip uniform/blank background patches
                dct = cv2.dct(patch)[:6, :6].flatten()
                # Normalize and quantize low-frequency AC coefficients (skip DC [0])
                ac = dct[1:]
                norm = np.linalg.norm(ac)
                if norm < 40.0:
                    continue
                sig = tuple(np.round((ac / norm) * 6.0).astype(int))
                if sig in blocks:
                    px, py = blocks[sig]
                    # Must be spatially separated (not adjacent overlapping windows)
                    if abs(x - px) > bs * 1.5 or abs(y - py) > bs * 1.5:
                        prev_patch = gray[py:py + bs, px:px + bs].astype(np.float32)
                        mae = float(np.abs(patch - prev_patch).mean())
                        if mae < 3.5:
                            clones.append({
                                "x": round(x / sw, 3),
                                "y": round(y / sh, 3),
                                "width": round(bs / sw, 3),
                                "height": round(bs / sh, 3),
                                "signals": ["copy_move_clone"],
                                "severity": "MEDIUM",
                                "label": "duplicated image patch detected",
                            })
                            if len(clones) >= max_regions:
                                return clones
                else:
                    blocks[sig] = (x, y)
        return clones
    except Exception:
        return []


def _noise_inconsistency(image_path):
    """Max/min sharpness ratio across content quadrants."""
    try:
        import cv2
        import numpy as np
        gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return None, False
        h, w = gray.shape
        quads = [gray[:h // 2, :w // 2], gray[:h // 2, w // 2:],
                 gray[h // 2:, :w // 2], gray[h // 2:, w // 2:]]
        vars_ = [cv2.Laplacian(q, cv2.CV_64F).var() + 1e-6
                 for q in quads if q.std() >= 18.0]
        if len(vars_) < 2:
            return 1.0, False
        ratio = max(vars_) / min(vars_)
        return round(float(ratio), 2), bool(ratio > 12.0)
    except Exception:
        return None, False


def _overlap(a, b):
    """Normalized-box IoU > 0."""
    ax2, ay2, bx2, by2 = (a["x"] + a["width"], a["y"] + a["height"],
                          b["x"] + b["width"], b["y"] + b["height"])
    ix = max(0, min(ax2, bx2) - max(a["x"], b["x"]))
    iy = max(0, min(ay2, by2) - max(a["y"], b["y"]))
    return ix > 0.005 and iy > 0.005


def _merge_regions(*region_lists):
    """Overlapping boxes from independent methods merge — and overlapping
    independent signals escalate severity."""
    regions = []
    for rlist in region_lists:
        for s in rlist:
            hit = next((r for r in regions if _overlap(r, s)), None)
            if hit:
                for sig in s["signals"]:
                    if sig not in hit["signals"]:
                        hit["signals"].append(sig)
                hit["severity"] = "HIGH" if len(hit["signals"]) >= 2 else "MEDIUM"
                hit["label"] = ("multiple independent signals overlap here — "
                                "needs a human look")
            else:
                regions.append(dict(s, signals=list(s["signals"])))
    return regions[:4]


def analyze(image_path, text, is_digital_pdf=False):
    """Run all feasible tamper screens. Returns evidence dict."""
    if is_digital_pdf or not image_path or not os.path.exists(image_path):
        return {"status": "NOT_AVAILABLE", "score": None, "regions": [],
                "note": "Pixel forensics need the source photo (N/A for digital PDFs)"}
    qr = _qr_check(image_path, text or "")
    regions = _merge_regions(
        _ela_regions(image_path),
        _sharpness_regions(image_path),
        _copy_move_regions(image_path),
    )

    flags = []
    for r in regions:
        where = f"region x={r['x']} y={r['y']} w={r['width']} h={r['height']}"
        flags.append(f"{where}: {', '.join(r['signals'])}")
    if qr["detected"] and not qr["readable"]:
        flags.append("QR-like region detected but unreadable")
    if qr.get("data_consistent") is False:
        flags.append("Decoded QR payload conflicts with visual text")

    score = 1.0
    if regions or flags:
        penalty = 0.25 * sum(len(r["signals"]) for r in regions)
        if qr.get("data_consistent") is False:
            penalty += 0.5
        score = round(max(0.0, 1.0 - penalty), 3)
    status = "REVIEW" if flags else "PASS"
    return {"status": status, "score": score, "flags": flags,
            "regions": regions, "qr": qr,
            "note": "Screening signals only — none of this proves forgery. "
                    "Regions are 'suspicious, needs review', never 'forged'." if flags
                    else "No tamper indicators found (screening only, not proof)"}
