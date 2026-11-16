"""VeriDoc pipeline tests — SYNTHETIC data only.

Never use real people's identity documents in tests. Aadhaar numbers here
are generated to satisfy the Verhoeff checksum mathematically; they are not
real. MRZ data is the ICAO Doc 9303 published example.
Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ml"))

from verify_document import (  # noqa: E402
    verhoeff_check,
    verhoeff_suggest,
    correct_ocr_text,
    syntax_checks,
    semantic_checks,
    do_ocr,
)
from cross_field import (  # noqa: E402
    mrz_check_digit,
    parse_mrz_td3,
    marksheet_arithmetic,
    validate,
)
from classifier import classify, _pure_python_tfidf_similarities  # noqa: E402
from image_quality import check_image_quality  # noqa: E402
from evidence import aggregate, decide, build_findings, load_config  # noqa: E402
from verifiers import get_verifier  # noqa: E402
from number_checks import plausibility, scan_text, mask_id  # noqa: E402
from tamper import analyze as tamper_analyze  # noqa: E402
from doc_templates import TEMPLATES  # noqa: E402


def make_verhoeff_number(prefix11):
    for d in "0123456789":
        if verhoeff_check(prefix11 + d):
            return prefix11 + d
    raise AssertionError("no check digit found")


class TestVerhoeff(unittest.TestCase):
    def test_self_heal_single_digit(self):
        real = make_verhoeff_number("23456789012")
        bad = real[:4] + ("7" if real[4] != "7" else "1") + real[5:]
        self.assertFalse(verhoeff_check(bad))
        fix = verhoeff_suggest(bad)
        self.assertIsNotNone(fix)
        self.assertTrue(verhoeff_check(fix[0]))

    def test_random_number_does_not_heal_toxic(self):
        fix = verhoeff_suggest("111111111111")
        if fix is not None:
            self.assertEqual(sum(a != b for a, b in
                                 zip("111111111111", fix[0])), 1)

    def test_non_confusion_invalid_number_rejected(self):
        # 726018159081 is valid. Changing last digit from '1' to '3' is NOT an
        # optical confusion ('3' only confuses with '8','5'), so it must NOT heal.
        self.assertTrue(verhoeff_check("726018159081"))
        self.assertFalse(verhoeff_check("483276483503"))
        self.assertIsNone(verhoeff_suggest("483276483503"))


class TestMRZ(unittest.TestCase):
    L1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
    L2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"

    def test_icao_example_valid(self):
        r = parse_mrz_td3(self.L1, self.L2)
        self.assertTrue(r["valid"], r.get("checks"))
        self.assertEqual(r["document_number"], "L898902C3")

    def test_tampered_mrz_fails(self):
        bad = self.L2[:21] + ("M" if self.L2[21] != "M" else "F") + self.L2[22:]
        r = parse_mrz_td3(self.L1, bad)
        self.assertFalse(r["valid"])

    def test_check_digit_known(self):
        self.assertEqual(mrz_check_digit("L898902C3"), 6)


class TestMarksheetArithmetic(unittest.TestCase):
    GOOD = ("Roll No 12345678 2024 Mathematics 085/100 Science 092/100 "
            "TOTAL 177 Percentage 88.5%")
    BAD = ("Roll No 12345678 2024 Mathematics 085/100 Science 092/100 "
           "TOTAL 999 Percentage 11%")

    def test_consistent_passes(self):
        rules, ok_n, tot_n = marksheet_arithmetic(self.GOOD)
        self.assertEqual(tot_n, ok_n)
        self.assertGreater(tot_n, 0)

    def test_inconsistent_flagged(self):
        r = validate(self.BAD, "marksheet", {})
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any(c["field"] == "marks_arithmetic"
                            for c in r["conflicts"]))

    def test_space_separated_marksheet_table(self):
        txt = ("Roll No 26843179 2024 MATHEMATICS 090 100 SCIENCE 080 100 "
               "TOTAL 170 Percentage 85.0%")
        rules, ok_n, tot_n = marksheet_arithmetic(txt)
        self.assertEqual(ok_n, 3)
        self.assertEqual(tot_n, 3)


class TestClassifier(unittest.TestCase):
    def test_types(self):
        cases = {
            "aadhaar": "Government of India UIDAI Aadhaar Card DOB MALE address vid",
            "pan": "Income Tax Department Permanent Account Number father signature",
            "passport": "Republic of India Passport nationality INDIAN date of expiry",
            "marksheet": "Central Board Secondary Examination CBSE Statement of Marks Roll No Result",
            "certificate": "Certificate awarded presented completion course signatory credential",
        }
        for want, text in cases.items():
            with self.subTest(want=want):
                r = classify(text)
                self.assertEqual(r["predicted"], want)
                self.assertTrue(r["confident"])

    def test_random_is_generic(self):
        r = classify("Hello this is a note about a meeting next week.")
        self.assertEqual(r["predicted"], "generic")
        self.assertFalse(r["confident"])

    def test_mrz_forces_passport(self):
        mrz = ("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
               "L898902C36UTO7408122F1204159ZE184226B<<<<<10")
        r = classify("tiny photo text\n" + mrz)
        self.assertEqual(r["predicted"], "passport")
        self.assertTrue(r["confident"])
        self.assertEqual(r.get("method"), "mrz")

    def test_namesake_bonus_on_sparse_read(self):
        r = classify("REPUBLIC INDIA passport 31/12/2005 112026")
        self.assertEqual(r["predicted"], "passport")

    def test_driving_licence_classified(self):
        r = classify("DLNUMBER HR03 20250000058 NAME PRIYANSHU DOB Validity NT "
                     "Issue Date Authorisation to Drive Blood Grp Driving Licence")
        self.assertEqual(r["predicted"], "driving_licence")
        self.assertTrue(r["confident"])

    def test_dl_number_anchor_beats_aadhaar_words(self):
        r = classify("DLNUMBER HR03 20250000058 DOB Address Aadhaar No "
                     "Authorisation to Drive NT Holder")
        self.assertEqual(r["predicted"], "driving_licence")
        self.assertEqual(r.get("method"), "id_number")

    def test_pan_number_anchor(self):
        r = classify("Income Tax Dept ABCDE1234F Rahul")
        self.assertEqual(r["predicted"], "pan")
        self.assertTrue(r["confident"])

    def test_passport_number_formats(self):
        import re
        pat = TEMPLATES["passport"]["patterns"]["passport_no"]
        self.assertTrue(re.search(pat, "AN192841"))
        self.assertTrue(re.search(pat, "A1234567"))
        self.assertTrue(re.search(pat, "L898902C3"))
        self.assertFalse(re.search(pat, "HR0320250000058"))

    def test_loose_mrz_detect_on_broken_lines(self):
        from cross_field import detect_mrz, find_mrz_lines
        broken = "P<INDTANEJA<<DHRUV<<<<<<<<<<<<<< AN192841<8IND0512310M3601143F0665459<<<<<<12"
        self.assertTrue(detect_mrz(broken))
        self.assertEqual(find_mrz_lines(broken), [])

    def test_mrz_rescues_passport_number(self):
        text = ("REPUBLIC OF INDIA PASSPORT ANNA MARIA INDIAN "
                "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
                "L898902C36UTO7408122F1204159ZE184226B<<<<<10")
        checks, _ = syntax_checks(text, "passport")
        pn = [c for c in checks if c["check"] == "syntax:passport_no"][0]
        self.assertEqual(pn["status"], "pass")
        self.assertIn("mrz", pn["message"].lower())

    def test_licence_verifier(self):
        v = get_verifier("driving_licence").verify(
            "Driving Licence DLNUMBER HR03 20250000058 Name Priyanshu Rana "
            "DOB 05-08-2006 Validity NT 04-06-2046 Issue Date 03-01-2025 "
            "Blood Group B Positive Address Haryana Transport Authority")
        self.assertEqual(v["fields"].get("dl_no"), "HR03 20250000058")
        self.assertEqual(v["fields"].get("blood_group"), "B Positive")
        self.assertGreater(v["score"], 0.5)

    def test_licence_underage_fails(self):
        v = get_verifier("driving_licence").verify(
            "Driving Licence DL No TN01 20230048291 Name Karthik "
            "DOB 15/05/2012 Issue Date 10/06/2023 Validity NT 14/05/2040")
        self.assertEqual(v["status"], "FAIL")

    def test_licence_zero_serial_not_fabricated(self):
        import re
        from doc_templates import KNOWN_STRUCTURED_FORMATS
        text = "DLNUMBER HR03 20250000058 Driving Licence"
        hits = scan_text(text)
        self.assertTrue(hits)
        structured = set()
        for pats in KNOWN_STRUCTURED_FORMATS.values():
            for pat in pats:
                for m in re.finditer(pat, text, re.IGNORECASE):
                    structured.add(re.sub(r"[-\s]+", "", m.group(0)).upper())
        kept = [h for h in hits
                if not (h.get("kind") == "entropy"
                        and any(h["number"] in s for s in structured))]
        self.assertEqual(kept, [])

    def test_dl_cross_field_scoped_to_dl_numbers(self):
        dl = ("DLNUMBER HR03 20250000058 DOB 05-08-2006 Driving Licence "
              "HR03 20250000058")
        r = validate(dl, "driving_licence", {})
        self.assertNotIn("aadhaar_occurrences", r["details"])
        self.assertNotIn("pan_occurrences", r["details"])
        self.assertIn("dl_occurrences", r["details"])
        r2 = validate(dl + " HR03 20241111111", "driving_licence", {})
        self.assertEqual(r2["status"], "FAIL")
        self.assertEqual(r2["conflicts"][0]["field"], "dl_number")

    def test_crisp_tell_survives_carveout(self):
        hits = scan_text("DLNUMBER HR03 20257777777 Driving Licence")
        kinds = {h.get("kind") for h in hits}
        self.assertTrue({"repeated", "block", "groups", "sequential"} & kinds)


class TestQualityGate(unittest.TestCase):
    def _img(self, blur=False):
        import cv2
        import numpy as np
        img = np.full((1500, 2000, 3), 255, np.uint8)
        cv2.putText(img, "TEST DOCUMENT 123", (200, 750),
                    cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 5)
        if blur:
            img = cv2.GaussianBlur(img, (31, 31), 0)
        p = "/tmp/dv_test_%s.png" % ("blur" if blur else "sharp")
        cv2.imwrite(p, img)
        return p

    def test_sharp_beats_blur(self):
        sharp = check_image_quality(self._img(False))
        blurred = check_image_quality(self._img(True))
        self.assertGreater(sharp["overall"], blurred["overall"])
        self.assertEqual(sharp["status"], "PASS")
        self.assertNotEqual(blurred["status"], "PASS")

    def test_ocr_rotation_invariant(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1200, 500), "white")
        d = ImageDraw.Draw(img)
        for i, ln in enumerate(["ORIENTATION TEST", "RAHUL KUMAR",
                                "DOB: 15-08-1994"]):
            d.text((60, 60 + i * 120), ln, fill="black")
        img.save("/tmp/dv_ori_up.png")
        img.rotate(180, expand=True).save("/tmp/dv_ori_down.png")
        up = do_ocr("/tmp/dv_ori_up.png")["text"]
        down = do_ocr("/tmp/dv_ori_down.png")["text"]
        for label, t in (("upright", up), ("upside-down", down)):
            with self.subTest(orientation=label):
                self.assertIn("RAHUL", t.upper())
                self.assertIn("15-08-1994", t)


class TestDecision(unittest.TestCase):
    def _ev(self, **kw):
        base = {"ocr": {"status": "PASS", "score": 0.9},
                "structure": {"status": "PASS", "score": 0.9},
                "semantic": {"status": "PASS", "score": 0.9},
                "cross_field": {"status": "PASS", "conflicts": []},
                "tamper": {"status": "PASS", "score": 0.9},
                "cryptographic": {"status": "NOT_AVAILABLE"},
                "image_quality": {"status": "PASS", "score": 0.9}}
        base.update(kw)
        return base

    def test_verified(self):
        ev = self._ev()
        s, _ = aggregate("aadhaar", ev)
        d, reasons, findings, risk, conf = decide(
            "aadhaar", ev, s, {"confident": True, "auto": True},
            {"status": "PASS"})
        self.assertEqual(d, "VERIFIED")
        self.assertEqual(risk, "LOW")

    def test_contradiction_is_suspicious_not_fraud_by_ocr(self):
        ev = self._ev(cross_field={"status": "FAIL",
                                   "conflicts": [{"field": "x"}]})
        s, _ = aggregate("passport", ev)
        d, _, _, risk, _ = decide(
            "passport", ev, s, {"confident": True, "auto": True},
            {"status": "PASS"})
        self.assertEqual(d, "SUSPICIOUS")
        self.assertEqual(risk, "HIGH")

    def test_poor_image_is_review_never_suspicious(self):
        ev = self._ev(ocr={"status": "FAIL", "score": 0.1})
        s, _ = aggregate("aadhaar", ev)
        d, reasons, _, _, _ = decide(
            "aadhaar", ev, s, {"confident": True, "auto": True},
            {"status": "FAIL"})
        self.assertEqual(d, "NEEDS_REVIEW")
        self.assertIn("quality", reasons[0].lower())

    def test_clean_aadhaar_jpeg_not_falsely_suspicious(self):
        import cv2
        import numpy as np
        img = np.full((1000, 1500, 3), 250, np.uint8)
        cv2.putText(img, "GOVERNMENT OF INDIA", (100, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 20, 20), 3)
        cv2.putText(img, "Name: Aarav Sharma", (100, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 2)
        cv2.putText(img, "DOB: 14/08/1999", (100, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 2)
        cv2.putText(img, "Aadhaar: 7260 1815 9081", (100, 500), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 20, 20), 3)
        p = "/tmp/dv_clean_aadhaar.jpg"
        cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        res = tamper_analyze(p, "GOVERNMENT OF INDIA Name: Aarav Sharma DOB: 14/08/1999 7260 1815 9081")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["regions"], [])


class TestVerifiers(unittest.TestCase):
    def test_adapter_interface(self):
        v = get_verifier("marksheet")
        r = v.verify("Central Board Secondary Examination 2024 Roll No 12345678 "
                     "Name Aarav Sharma Date of Birth 14/08/2008 Mathematics 085/100 "
                     "Science 092/100 English 078/100 Result PASS Division FIRST")
        self.assertIn("score", r)
        self.assertIn("status", r)
        self.assertIn("fields", r)
        self.assertGreater(r["score"], 0.5)

    def test_unknown_falls_back_to_generic(self):
        self.assertEqual(get_verifier("voter_id").doc_type, "generic")

    def test_pan_verifier_surname_and_entity(self):
        v = get_verifier("pan")
        r = v.verify("INCOME TAX DEPARTMENT GOVT OF INDIA Permanent Account Number "
                     "ABCPS1234F Name: Aarav Sharma Father Name: Rajesh Sharma DOB: 14/08/1999")
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["fields"]["pan_number"], "ABCPS1234F")
        self.assertEqual(r["fields"]["name"], "Aarav Sharma")
        self.assertEqual(r["fields"]["entity_type"], "Individual")

    def test_certificate_verifier_id_not_matching_header(self):
        v = get_verifier("certificate")
        r = v.verify("CERTIFICATE OF COMPLETION\nCertificate ID: CERT-2024-8821\n"
                     "This is to certify that Mr. Aarav Sharma has completed the course "
                     "Machine Learning Foundations\nDate: 15/08/2024")
        self.assertEqual(r["fields"]["cert_id"], "CERT-2024-8821")
        self.assertEqual(r["fields"]["recipient"], "Aarav Sharma")
        self.assertEqual(r["fields"]["course"], "Machine Learning Foundations")

    def test_correct_ocr_text_preserves_dl_and_names(self):
        txt = "Driving Licence: TN01 20230048291 Name: Karthik Rajan Marks: 085 100"
        self.assertEqual(correct_ocr_text(txt), txt)


class TestFabricationScreen(unittest.TestCase):
    def test_sequential_groups_flagged(self):
        ok, reason, kind = plausibility("000011112222")
        self.assertFalse(ok)
        self.assertIn("fabricated", reason)
        self.assertEqual(kind, "groups")

    def test_all_same_flagged(self):
        self.assertFalse(plausibility("999999999999")[0])
        self.assertFalse(plausibility("123456789012")[0])

    def test_genuine_random_passes(self):
        self.assertTrue(plausibility("726018159081")[0])
        self.assertTrue(plausibility("17231670")[0])
        self.assertTrue(plausibility("26843179")[0])

    def test_scan_finds_collapsed_runs(self):
        hits = scan_text("Aadhaar No: 0000 1111 2222")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["number"], "000011112222")

    def test_fake_aadhaar_decision_is_suspicious(self):
        fab = scan_text("GOVERNMENT India RAHUL KUMAR DOB 15-08-1994 MALE 0000 1111 2222")
        ev = {"ocr": {"status": "PASS", "score": 0.76},
              "structure": {"status": "REVIEW", "score": 0.5},
              "semantic": {"status": "PASS", "score": 0.67},
              "cross_field": {"status": "PASS", "conflicts": []},
              "tamper": {"status": "REVIEW", "score": 0.5},
              "cryptographic": {"status": "NOT_AVAILABLE"},
              "image_quality": {"status": "PASS", "score": 0.74},
              "fabrication": {"status": "FAIL", "hits": fab}}
        s, _ = aggregate("aadhaar", ev)
        d, _, findings, risk, _ = decide(
            "aadhaar", ev, s, {"confident": True, "auto": False},
            {"status": "PASS"})
        self.assertEqual(d, "SUSPICIOUS")
        self.assertEqual(risk, "HIGH")
        self.assertTrue(any(f["severity"] == "CRITICAL" for f in findings))

    def test_masking(self):
        self.assertEqual(mask_id("911407563214"), "91••••••••14")
        self.assertEqual(mask_id("ABC"), "ABC")

    def test_generic_template_match_never_fails(self):
        checks, _ = semantic_checks("xyz blarg foo 31/12/2005", "generic", 0.7)
        tm = [c for c in checks if c["check"] == "semantic:template_match"]
        self.assertTrue(tm)
        self.assertNotEqual(tm[0]["status"], "fail")

    def test_structure_absent_downgraded_on_sparse_text(self):
        ev = {"structure": {"status": "FAIL", "score": 0.2},
              "ocr": {"status": "PASS", "score": 0.8},
              "cross_field": {"status": "PASS", "conflicts": []},
              "tamper": {"status": "PASS"}}
        struct = [{"check": "syntax:text_volume", "status": "fail",
                   "message": "little text", "value": 9}]
        f = build_findings("passport", ev, {"confident": True},
                           {"status": "PASS"}, 0.8, struct)
        sa = [x for x in f if x["check"] == "structure_absent"]
        self.assertTrue(sa)
        self.assertEqual(sa[0]["severity"], "WARNING")


class TestEndToEndSamples(unittest.TestCase):
    def test_all_sample_pdfs_verify(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        script = os.path.join(root, "ml", "verify_document.py")
        expected = {
            "sample_aadhaar.pdf": "aadhaar",
            "sample_pan.pdf": "pan",
            "sample_passport.pdf": "passport",
            "sample_driving_licence.pdf": "driving_licence",
            "sample_marksheet.pdf": "marksheet",
            "sample_certificate.pdf": "certificate",
        }
        for fname, exp_type in expected.items():
            with self.subTest(sample=fname):
                fpath = os.path.join(root, "sample", fname)
                out = subprocess.check_output(
                    [sys.executable, script, fpath, "auto"], text=True
                )
                res = json.loads(out.strip().splitlines()[-1])
                self.assertEqual(res["documentType"], exp_type)
                self.assertEqual(res["verdict"], "VERIFIED")
                self.assertGreaterEqual(res["overallScore"], 0.90)

    def test_scanned_pdf_ocr_fallback(self):
        # Create a scanned image-only PDF (no text layer) and verify OCR extracts it
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1400, 900), "white")
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=32)
        except TypeError:
            font = ImageFont.load_default()
        lines = [
            "INCOME TAX DEPARTMENT",
            "GOVT OF INDIA",
            "Permanent Account Number Card",
            "PAN: ABCPS1234F",
            "Name: Aarav Sharma",
            "DOB: 14/08/1999",
        ]
        for i, ln in enumerate(lines):
            d.text((80, 80 + i * 90), ln, fill="black", font=font)
        scan_pdf = "/tmp/dv_scanned_pan.pdf"
        img.save(scan_pdf, "PDF", resolution=200.0)
        res = do_ocr(scan_pdf)
        self.assertIn("ABCPS1234F", res["text"].upper())
        self.assertFalse(os.path.exists(scan_pdf + "_page1.png"))


if __name__ == "__main__":
    unittest.main()
