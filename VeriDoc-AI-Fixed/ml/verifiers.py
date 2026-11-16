"""Modular document verifiers — one adapter per document type.

Common interface:  verifier.verify(text) -> {
    "score": 0..1, "status": PASS|REVIEW|FAIL, "checks": [...], "fields": {...} }

Structure matching itself lives in verify_document.syntax_checks (imported
lazily to avoid a module cycle); adapters add the interface, status mapping,
field extraction for cross-field validation, and per-type extras.
"""
import re
from datetime import datetime

from doc_templates import TEMPLATES

# Valid Indian State & UT RTO 2-letter prefixes (Motor Vehicles Act)
VALID_INDIAN_STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN",
    "GA", "GJ", "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD",
    "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY",
    "RJ", "SK", "TN", "TR", "TS", "TG", "UA", "UK", "UP", "WB",
}

VALID_PAN_ENTITIES = {
    "P": "Individual",
    "C": "Company",
    "H": "Hindu Undivided Family (HUF)",
    "F": "Partnership Firm / LLP",
    "A": "Association of Persons (AOP)",
    "T": "Trust",
    "B": "Body of Individuals (BOI)",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government Agency",
    "E": "Limited Liability Partnership",
}


def _structure(text, doc_type):
    from verify_document import syntax_checks  # lazy: avoids import cycle
    return syntax_checks(text, doc_type)


def _parse_date_flexible(date_str):
    """Parse DD/MM/YYYY, DD-MM-YYYY, or DD.MM.YYYY safely."""
    if not date_str:
        return None
    s = date_str.strip()
    if re.fullmatch(r"(?:19|20)\d{2}", s):
        return datetime(int(s), 1, 1)
    parts = re.split(r"[\/\-\.]", s)
    if len(parts) != 3:
        return None
    try:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:
            y += 2000 if y < 50 else 1900
        return datetime(y, m, d)
    except ValueError:
        return None


class BaseVerifier:
    doc_type = "generic"

    def verify(self, text):
        checks, score = _structure(text, self.doc_type)
        fields = self.extract_fields(text)
        extra = self.extra_checks(text, fields)
        all_checks = list(checks) + (extra or [])

        tpl = TEMPLATES[self.doc_type]
        required_failed = any(
            c["check"] == f"syntax:{r}" and c["status"] == "fail"
            for r in tpl.get("required", []) for c in checks)
        extra_failed = any(c["status"] == "fail" for c in (extra or []))

        if extra:
            extra_pts = sum(1.0 if c["status"] == "pass" else (0.4 if c["status"] == "warn" else 0.0)
                            for c in extra)
            score = round((score * len(checks) + extra_pts) / (len(checks) + len(extra)), 3)

        if required_failed or extra_failed or score < 0.4:
            status = "FAIL"
        elif score < 0.7:
            status = "REVIEW"
        else:
            status = "PASS"
        out = {"score": score, "status": status,
               "checks": checks, "fields": fields}
        if extra:
            out["extra"] = extra
        return out

    def extract_fields(self, text):
        return {}

    def extra_checks(self, text, fields):
        return None


class AadhaarVerifier(BaseVerifier):
    doc_type = "aadhaar"

    def extract_fields(self, text):
        m = re.search(TEMPLATES["aadhaar"]["patterns"]["aadhaar_number"], text)
        d = re.search(TEMPLATES["aadhaar"]["patterns"]["dob"], text, re.IGNORECASE)
        g = re.search(TEMPLATES["aadhaar"]["patterns"]["gender"], text, re.IGNORECASE)
        p = re.search(TEMPLATES["aadhaar"]["patterns"]["pincode"], text)
        raw_num = re.sub(r"\D", "", m.group(0)) if m else None
        return {
            "aadhaar_number": raw_num,
            "dob": d.group(1) if d else None,
            "gender": g.group(1).upper() if g else None,
            "pincode": p.group(0) if p else None,
        }

    def extra_checks(self, text, fields):
        from verify_document import verhoeff_check, verhoeff_suggest  # lazy: avoids cycle
        notes = []
        n = fields.get("aadhaar_number")
        if n:
            if verhoeff_check(n):
                notes.append({
                    "check": "structure:checksum",
                    "status": "pass",
                    "message": "Verhoeff dihedral D8 checksum validates the 12-digit Aadhaar format",
                })
                fields["checksum_valid"] = True
            else:
                fix = verhoeff_suggest(n)
                if fix:
                    notes.append({
                        "check": "structure:checksum",
                        "status": "warn",
                        "message": f"Checksum required single-digit OCR typo tolerance (digit {fix[1]+1}: {fix[2]}->{fix[3]})",
                    })
                    fields["checksum_valid"] = True
                    fields["checksum_self_healed"] = fix[0]
                else:
                    notes.append({
                        "check": "structure:checksum",
                        "status": "fail",
                        "message": "Verhoeff checksum failed and does not match any single-digit OCR confusion",
                    })
                    fields["checksum_valid"] = False
        return notes or None


class PANVerifier(BaseVerifier):
    doc_type = "pan"

    def extract_fields(self, text):
        m = re.search(TEMPLATES["pan"]["patterns"]["pan_number"], text.upper())
        d = re.search(TEMPLATES["pan"]["patterns"]["dob"], text)
        n = re.search(TEMPLATES["pan"]["patterns"]["name"], text)
        fn = re.search(TEMPLATES["pan"]["patterns"]["father_name"], text)
        pan_str = m.group(0) if m else None
        name_str = " ".join(n.group(1).split()) if n else None
        father_str = " ".join(fn.group(1).split()) if fn else None
        out = {
            "pan_number": pan_str,
            "dob": d.group(1) if d else None,
            "name": name_str,
            "father_name": father_str,
            "entity_type": VALID_PAN_ENTITIES.get(pan_str[3]) if pan_str and len(pan_str) == 10 else None,
            "_disclaimer": ("Valid PAN format does not mean the PAN is "
                            "genuine; no official registry check performed."),
        }
        return out

    def extra_checks(self, text, fields):
        notes = []
        pan = fields.get("pan_number")
        name = fields.get("name")
        if pan and len(pan) == 10:
            ent = VALID_PAN_ENTITIES.get(pan[3])
            if ent:
                notes.append({
                    "check": "structure:pan_entity",
                    "status": "pass",
                    "message": f"PAN 4th character '{pan[3]}' is valid ({ent})",
                })
            else:
                notes.append({
                    "check": "structure:pan_entity",
                    "status": "fail",
                    "message": f"PAN 4th character '{pan[3]}' is not a valid Income Tax holder category",
                })
            # For individual PAN ('P'), 5th char is usually the surname initial
            if pan[3] == "P" and name:
                tokens = [t.upper() for t in re.findall(r"[A-Za-z]{2,}", name)]
                initials = {t[0] for t in tokens}
                if tokens and pan[4] in initials:
                    notes.append({
                        "check": "structure:pan_surname_initial",
                        "status": "pass",
                        "message": f"PAN 5th character '{pan[4]}' matches holder name initial ({name})",
                    })
                elif tokens:
                    notes.append({
                        "check": "structure:pan_surname_initial",
                        "status": "warn",
                        "message": f"PAN 5th character '{pan[4]}' does not match extracted name initials ({name})",
                    })
        return notes or None


class PassportVerifier(BaseVerifier):
    doc_type = "passport"

    def extract_fields(self, text):
        m = re.search(TEMPLATES["passport"]["patterns"]["passport_no"], text.upper())
        d = re.search(TEMPLATES["passport"]["patterns"]["dob"], text)
        nat = re.search(TEMPLATES["passport"]["patterns"]["nationality"], text.upper())
        p_no = m.group(0) if m else None
        # Fallback: extract passport number from TD3 MRZ line 2 if visual field missed
        if not p_no:
            from cross_field import find_mrz_lines
            pairs = find_mrz_lines(text.upper().replace(" ", ""))
            if pairs:
                p_no = pairs[0][1][0:9].replace("<", "")
        return {
            "passport_no": p_no,
            "dob": d.group(1) if d else None,
            "nationality": nat.group(0) if nat else None,
        }


class DrivingLicenceVerifier(BaseVerifier):
    doc_type = "driving_licence"

    def extract_fields(self, text):
        tpl = TEMPLATES["driving_licence"]["patterns"]
        m = re.search(tpl["dl_no"], text.upper())
        d = re.search(tpl["dob"], text, re.IGNORECASE)
        v = re.search(tpl["validity"], text, re.IGNORECASE)
        iss = re.search(tpl["issue_date"], text, re.IGNORECASE)
        nm = re.search(tpl["name"], text)
        bg = re.search(tpl["blood_group"], text, re.IGNORECASE)
        dl_raw = " ".join(m.group(0).split()) if m else None
        return {
            "dl_no": dl_raw,
            "state_code": dl_raw[:2] if dl_raw else None,
            "dob": d.group(1) if d else None,
            "validity": v.group(1) if v else None,
            "issue_date": iss.group(1) if iss else None,
            "name": " ".join(nm.group(1).split()) if nm else None,
            "blood_group": bg.group(1).strip() if bg else None,
        }

    def extra_checks(self, text, fields):
        notes = []
        dl_no = fields.get("dl_no")
        if dl_no:
            sc = dl_no[:2].upper()
            if sc in VALID_INDIAN_STATE_CODES:
                notes.append({
                    "check": "structure:rto_state_code",
                    "status": "pass",
                    "message": f"Valid Indian State/UT RTO prefix '{sc}'",
                })
            else:
                notes.append({
                    "check": "structure:rto_state_code",
                    "status": "fail",
                    "message": f"Unrecognized State/UT prefix '{sc}' in DL number",
                })

        dob_dt = _parse_date_flexible(fields.get("dob"))
        val_dt = _parse_date_flexible(fields.get("validity"))
        iss_dt = _parse_date_flexible(fields.get("issue_date"))

        if val_dt and dob_dt:
            if val_dt > dob_dt:
                notes.append({
                    "check": "structure:validity_logic",
                    "status": "pass",
                    "message": "Licence validity date is after date of birth",
                })
            else:
                notes.append({
                    "check": "structure:validity_logic",
                    "status": "fail",
                    "message": "Validity date precedes date of birth — impossible",
                })

        if iss_dt and val_dt:
            if val_dt > iss_dt:
                notes.append({
                    "check": "structure:issue_expiry_order",
                    "status": "pass",
                    "message": "Expiry/validity date is chronologically after issue date",
                })
            else:
                notes.append({
                    "check": "structure:issue_expiry_order",
                    "status": "fail",
                    "message": "Expiry/validity date precedes issue date — impossible",
                })

        if dob_dt:
            ref_dt = iss_dt or datetime.now()
            age_years = (ref_dt - dob_dt).days / 365.2425
            if age_years >= 18.0:
                notes.append({
                    "check": "structure:driver_minimum_age",
                    "status": "pass",
                    "message": f"Driver age ({int(age_years)} yrs) meets statutory 18+ requirement",
                })
            elif age_years >= 16.0:
                notes.append({
                    "check": "structure:driver_minimum_age",
                    "status": "warn",
                    "message": f"Holder age ({age_years:.1f} yrs) is 16–18 (valid only for MCWOG <=50cc)",
                })
            else:
                notes.append({
                    "check": "structure:driver_minimum_age",
                    "status": "fail",
                    "message": f"Holder age ({age_years:.1f} yrs) is below minimum legal driving age",
                })
        return notes or None


class MarksheetVerifier(BaseVerifier):
    doc_type = "marksheet"

    def extract_fields(self, text):
        tpl = TEMPLATES["marksheet"]["patterns"]
        r = re.search(tpl["roll_no"], text, re.IGNORECASE | re.DOTALL)
        rf = re.search(tpl["roll_no_format"], text)
        y = re.search(tpl["year"], text)
        pct = re.search(tpl["percentage"], text)
        roll_val = r.group(1).strip() if r else (rf.group(0) if rf else None)
        return {
            "roll_no": roll_val,
            "year": y.group(0) if y else None,
            "percentage": pct.group(0) if pct else None,
        }


class CertificateVerifier(BaseVerifier):
    doc_type = "certificate"

    def extract_fields(self, text):
        tpl = TEMPLATES["certificate"]["patterns"]
        c = re.search(tpl["cert_id"], text, re.IGNORECASE)
        d = re.search(tpl["date"], text)
        n = re.search(tpl["name"], text, re.IGNORECASE)
        crs = re.search(tpl["course"], text, re.IGNORECASE)
        return {
            "cert_id": c.group(1).strip() if c else None,
            "date": d.group(1) if d else None,
            "recipient": " ".join(n.group(1).split()) if n else None,
            "course": " ".join(crs.group(1).split()) if crs else None,
        }


class GenericVerifier(BaseVerifier):
    doc_type = "generic"


_VERIFIERS = {
    "aadhaar": AadhaarVerifier,
    "pan": PANVerifier,
    "passport": PassportVerifier,
    "driving_licence": DrivingLicenceVerifier,
    "marksheet": MarksheetVerifier,
    "certificate": CertificateVerifier,
    "generic": GenericVerifier,
}


def get_verifier(doc_type):
    """Return the adapter for doc_type (GenericVerifier fallback)."""
    return _VERIFIERS.get(doc_type, GenericVerifier)()
