"""Shared document templates: keywords + syntax patterns per document type.

Imported by verify_document.py, classifier.py and verifiers.py so the
patterns live in exactly one place (no duplicated verification logic).
"""

TEMPLATES = {
    "aadhaar": {
        "label": "Aadhaar Card",
        "keywords": ["government of india", "aadhaar", "uidai", "dob", "date of birth",
                     "year of birth", "male", "female", "address", "vid", "proof of identity",
                     "verification", "authentication", "offline", "qr code", "masked"],
        "patterns": {
            "aadhaar_number": r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b",
            "vid": r"\bVID\s*[:\-]?\s*\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b",
            "dob": r"\b(?:D[o0]B|D\.?\s*[o0O]\.?\s*B\.?|Date\s*of\s*Birth|Year\s*of\s*Birth|YOB|D\.O\.B)[\s:]*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4}|\b(?:19|20)\d{2}\b)",
            "gender": r"\b(MALE|FEMALE|TRANSGENDER)\b",
            "pincode": r"\b[1-9]\d{5}\b",
            "mobile": r"\b[6-9]\d{9}\b",
        },
        "required": ["aadhaar_number", "dob", "gender"],
    },
    "pan": {
        "label": "PAN Card",
        "keywords": ["income tax department", "permanent account number", "pan",
                     "govt of india", "date of birth", "father", "signature"],
        "patterns": {
            "pan_number": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
            "dob": r"\b(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})\b",
            "name": r"\b(?:Name|NAME)[ \t:]+([A-Z][A-Za-z .]{2,40}?)(?=\s*(?:\r?\n|Father|FATHER|DOB|Date|PAN|$))",
            "father_name": r"\b(?:Father(?:'?s)?\s*Name|FATHER(?:'?S)?\s*NAME)[ \t:]+([A-Z][A-Za-z .]{2,40}?)(?=\s*(?:\r?\n|DOB|Date|Signature|$))",
        },
        "required": ["pan_number"],
    },
    "passport": {
        "label": "Passport",
        "keywords": ["republic of india", "passport", "nationality", "indian",
                     "date of birth", "place of birth", "date of issue", "date of expiry"],
        "patterns": {
            # Indian numbers are 1 letter + 7 digits (A1234567), 2 letters + 6 digits
            # (AN192841), or ICAO alphanumeric 8-9 char numbers (L898902C3).
            "passport_no": r"\b(?:[A-Z]{1,2}\d{6,7}|[A-Z]\d{6}[A-Z]\d)\b",
            "dob": r"\b(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})\b",
            "mrz": r"[A-Z0-9<]{30,}",
            "nationality": r"\b(?:INDIAN|UTO|[A-Z]{3})\b",
        },
        "required": ["passport_no"],
    },
    "driving_licence": {
        "label": "Driving Licence",
        "keywords": ["driving", "licence", "license", "transport", "motor",
                     "rto", "validity", "authorisation", "authorization",
                     "holder", "lmv", "mcwg", "nt", "inv", "issued"],
        "patterns": {
            "dl_no": r"\b[A-Z]{2}[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{7}\b",
            "dob": r"\b(?:D[o0]B|D\.?\s*[o0O]\.?\s*B\.?|Date\s*of\s*Birth|D\.O\.B)[\s:]*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})\b",
            "validity": r"\b(?:Validity|Valid(?:\s*(?:Till|Upto))?|\(?NT\)?|\(?INV\)?)[\s:]*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})\b",
            "issue_date": r"\b(?:Issue\s*Date|DOI|Issued\s*On)[\s:]*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})\b",
            "name": r"\b(?:Name|NAME)[ \t:]+([A-Z][A-Za-z .]{2,40}?)(?=\s*(?:\r?\n|DOB|Date|S\/O|D\/O|W\/O|Father|Address|Validity|Issue|$))",
            "blood_group": r"\bBlood\s*Gr(?:ou)?p[\s:]*((?:AB|A|B|O)[ \t]*(?:[+-]|Positive|Negative|Pos|Neg))(?!\w)",
        },
        "required": ["dl_no"],
    },
    "marksheet": {
        "label": "Marksheet",
        "keywords": ["board", "central", "secondary", "examination", "cbse",
                     "university", "marks", "grade", "subject",
                     "theory", "practical", "roll", "enrolment", "enrollment",
                     "registration", "result", "pass", "division", "session",
                     "school", "college", "class", "affiliation", "statement",
                     "birth", "dob", "date", "semester", "total", "maximum"],
        "patterns": {
            "roll_no": r"\b(?:Roll|Rull|Rell|R0ll|RoII|Reg(?:istration)?|Rgn|Enrol[ml](?:ment)?|Enroll?ment)[\s\.:No\-–—a-zA-Z]{0,12}?(\d[\d\-\/]{3,19})\b",
            "roll_no_format": r"\b\d{7,8}\b",
            "year": r"\b(19\d{2}|20[0-3]\d)\b",
            "percentage": r"\b\d{1,3}(?:\.\d{1,2})?\s?%",
            "marks": r"\b(\d{1,3}\s?\/\s?\d{2,4})(?![\/\-\.]\d)\b",
            "subject_marks": r"\b(?:MATHEMATICS|SCIENCE|ENGLISH|HINDI|SOCIAL|SANSKRIT|PHYSICS|CHEMISTRY|BIOLOGY|COMPUTER|MUSIC|LITERATURE|LANGUAGE|COURSE)\b[\s\S]{0,80}?\b(\d{2,3})\b",
            "marks_words": r"\b(?:TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY)(\s+(?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE))?\b",
            "grade": r"\bGrade[\s:]*([A-E][+12]?)\b",
            "grade_token": r"\b[ABCDE][12]\b",
            "dob": r"\b(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{4})\b",
        },
        "required": ["roll_no", "year"],
    },
    "certificate": {
        "label": "Certificate",
        "keywords": ["certificate", "certified", "awarded", "presented",
                     "completion", "achievement", "course", "training",
                     "issuer", "authority", "signatory", "seal", "recipient",
                     "grade", "valid", "credential"],
        "patterns": {
            "cert_id": r"\b(?:(?:Certificate|Cert\.?|Credential)\s*(?:ID|No\.?|Number|#)|(?:ID|No\.?))\s*[:\-]\s*([A-Z0-9][A-Z0-9\-\/]{3,24})\b",
            "date": r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b",
            "year": r"\b(19\d{2}|20[0-3]\d)\b",
            "name": r"\b(?:awarded\s+to|presented\s+to|certified\s+that|certify\s+that)\s+(?:Mr\.?\s+|Ms\.?\s+|Mrs\.?\s+|Dr\.?\s+)?([A-Z][A-Za-z .]{2,40}?)(?=\s+(?:has|have|for|in|on|successfully|completed|participated|\r?\n|$))",
            "course": r"\b(?:course|training|program|workshop)[ \t:]+([A-Za-z][A-Za-z0-9 &\-]{2,50}?)(?=\s*(?:\r?\n|\bDate\b|\bon\b|\bheld\b|\bIssued\b|$))",
        },
        "required": [],
    },
    "generic": {
        "label": "Generic Document",
        "keywords": ["date", "name", "address", "signature", "authorized",
                     "seal", "certificate", "registration", "number", "details",
                     "issued", "valid", "office", "department"],
        "patterns": {
            "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
            "phone": r"\b[6-9]\d{9}\b",
            "date": r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b",
            "pincode": r"\b[1-9]\d{5}\b",
        },
        "required": [],
    },
}

# Types with a dedicated verifier adapter (everything else -> GenericVerifier).
SPECIFIC_TYPES = ["aadhaar", "pan", "passport", "driving_licence",
                  "marksheet", "certificate"]

# Structured ID formats whose carriers legitimately contain low-variety runs
# (e.g. zero-padded licence serials).
KNOWN_STRUCTURED_FORMATS = {
    "driving_licence": [r"[A-Z]{2}[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{7}"],
}
