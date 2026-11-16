"""DocumentClassifier — probabilistic type prediction, not blind selection.

Corpus-fit TF-IDF cosine (shared filler words down-weighted, distinctive
words decisive) converted to softmax probabilities, PLUS two deterministic
anchors that don't depend on word counts:
  - MRZ lines present  -> passport (near-definitive, OCR-proof).
  - namesake word present ("passport", "aadhaar", ...) -> similarity bonus,
    so a sparse but on-topic read still classifies.

Includes a pure-Python/NumPy TF-IDF cosine fallback when scikit-learn is
not installed, ensuring 100% reliability across minimal environments.
"""
import math
import os
import re
import json
from collections import Counter

from doc_templates import TEMPLATES, SPECIFIC_TYPES

# Distinctive singletons: if the document literally names itself, believe it.
NAMESAKE = {
    "aadhaar": r"aadhaar|uidai",
    "pan": r"\bpan\b|permanent\s+account\s+number|income\s+tax\s+department",
    "passport": r"passport|republic\s+of\s+india",
    "driving_licence": r"driving|licen[sc]e|\bdl\s*no\b|dlnumber|\bdrive\b|\brto\b|transport|validity|authorisation|authorization|\bholder\b|\blmv\b|\bmcwg\b",
    "marksheet": r"marksheet|cbse|marks\s+statement|statement\s+of\s+marks",
    "certificate": r"certificate",
}

# Structured ID numbers are near-definitive anchors (like MRZ for
# passports): their formats are distinctive enough to decide on.
NUMBER_ANCHORS = [
    ("driving_licence", r"[A-Z]{2}[-\s]?\d{2}[-\s]?\d{4}[-\s]?\d{7}", 0.90),
    ("pan", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", 0.90),
]


def _thresholds():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "config", "documentVerification.json")
    with open(here) as f:
        return json.load(f)["classification"]


def _tokenize_ngrams(text):
    """Extract 1-grams and 2-grams matching sklearn's default token pattern."""
    tokens = re.findall(r"(?u)\b\w\w+\b", (text or "").lower())
    bigrams = [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]
    return tokens + bigrams


def _pure_python_tfidf_similarities(docs, query_text, types):
    """Pure-Python sublinear TF-IDF cosine similarity (matches sklearn behavior)."""
    all_docs = docs + [(query_text or "").lower()]
    n_docs = len(all_docs)
    tokenized = [_tokenize_ngrams(d) for d in all_docs]

    # Document frequency per term
    df = Counter()
    for toks in tokenized:
        for term in set(toks):
            df[term] += 1

    # Smooth IDF: log((1 + N) / (1 + df)) + 1.0 (exact sklearn smooth_idf=True formula)
    idf = {term: math.log((1.0 + n_docs) / (1.0 + count)) + 1.0
           for term, count in df.items()}

    # Compute L2-normalized sublinear TF-IDF vectors
    vectors = []
    for toks in tokenized:
        tf = Counter(toks)
        vec = {}
        norm_sq = 0.0
        for term, count in tf.items():
            if term in idf:
                val = (1.0 + math.log(count)) * idf[term]
                vec[term] = val
                norm_sq += val * val
        norm = math.sqrt(norm_sq) if norm_sq > 0 else 1.0
        for term in vec:
            vec[term] /= norm
        vectors.append(vec)

    q_vec = vectors[-1]
    out = {}
    for i, t in enumerate(types):
        d_vec = vectors[i]
        dot = sum(val * q_vec.get(term, 0.0) for term, val in d_vec.items())
        out[t] = float(dot)
    return out


def similarities(text, types=None):
    """Raw cosine similarities vs templates (corpus-fit TF-IDF)."""
    types = types or list(TEMPLATES)
    docs = [" ".join(TEMPLATES[t]["keywords"] + [TEMPLATES[t]["label"]])
            for t in types]
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vec = TfidfVectorizer(ngram_range=(1, 2),
                              sublinear_tf=True).fit_transform(docs + [(text or "").lower()])
        sims = cosine_similarity(vec[:-1], vec[-1:])
        return {t: float(sims[i][0]) for i, t in enumerate(types)}
    except ImportError:
        return _pure_python_tfidf_similarities(docs, text, types)


def _softmax(scores, temp=0.08):
    """Sharpen small cosine gaps into readable probabilities."""
    vals = [(t, scores[t] / temp) for t in scores]
    m = max(v for _, v in vals)
    exps = {t: math.exp(v - m) for t, v in vals}
    total = sum(exps.values()) or 1.0
    return {t: exps[t] / total for t in exps}


def classify(text):
    """Classify text. Generic is fallback-only (never competes)."""
    low = (text or "").lower()
    # Anchor 1: MRZ lines are near-definitive for passports.
    try:
        from cross_field import detect_mrz
        blob = detect_mrz(text)
        if blob:
            sims = similarities(text, SPECIFIC_TYPES)
            return {"predicted": "passport", "confidence": 0.90,
                    "best_raw": "passport", "method": "mrz",
                    "raw_similarities": {t: round(s, 3) for t, s in sims.items()},
                    "alternatives": {t: 0.02 for t in SPECIFIC_TYPES if t != "passport"},
                    "confident": True}
    except Exception:
        pass
    # Anchor 1b: distinctive structured ID numbers decide their type.
    up = (text or "").upper()
    for dtype, pat, conf in NUMBER_ANCHORS:
        try:
            if re.search(pat, up):
                sims = similarities(text, SPECIFIC_TYPES)
                return {"predicted": dtype, "confidence": conf,
                        "best_raw": dtype, "method": "id_number",
                        "raw_similarities": {t: round(s, 3) for t, s in sims.items()},
                        "alternatives": {t: 0.02 for t in SPECIFIC_TYPES if t != dtype},
                        "confident": True}
        except Exception:
            continue
    sims = similarities(text, SPECIFIC_TYPES)
    # Anchor 2: namesake bonus for sparse-but-on-topic reads.
    for t, pat in NAMESAKE.items():
        if re.search(pat, low):
            sims[t] = sims.get(t, 0) + 0.20
    probs = _softmax(sims)
    ranked = sorted(probs, key=probs.get, reverse=True)
    best, second = ranked[0], ranked[1]
    th = _thresholds()
    confident = (sims[best] >= th["confident"]
                 and (probs[best] - probs[second]) >= th["margin"])
    predicted = best if confident else "generic"
    return {"predicted": predicted,
            "confidence": round(probs[best], 3),
            "best_raw": best,
            "method": "tfidf",
            "raw_similarities": {t: round(s, 3) for t, s in sims.items()},
            "alternatives": {t: round(probs[t], 3) for t in ranked[1:]},
            "confident": confident}
