# Vetta - proprietary software. Copyright (c) 2026 Anoop Shekhar.
# Public to read, not to use. Copying, modification, deployment or commercial
# use requires written permission: thisisanoopshekhar89@gmail.com
"""Malpractice checks beyond hidden text.

These target things a candidate does to game an automated screener rather than
to describe their experience: stuffing keywords, pasting the job description
into the document, and metadata that contradicts the claimed authorship.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from .checks import HIGH, INFO, LOW, MEDIUM, Finding
from .extract import Extraction
from .match import STOPWORDS, normalise

_WORD = re.compile(r"[a-z][a-z0-9+#./-]{2,}")


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(normalise(text))
            if w not in STOPWORDS and not w.isdigit()]


def check_hidden_payload(ex: Extraction) -> list[Finding]:
    """Weigh how much of the document is hidden, and whether it reads like keywords."""
    out: list[Finding] = []
    hid = ex.hidden_text.strip()
    if not hid:
        return out

    ratio = ex.hidden_ratio
    if ratio > 0.02:
        out.append(Finding(
            code="HIDDEN_TEXT_VOLUME",
            severity=HIGH if ratio > 0.10 else MEDIUM,
            title="%.1f%% of the document text is not visible to a reader" % (ratio * 100),
            evidence=hid[:200],
            where="document",
            detail=("A parser or an LLM ingests this text; a human reviewer does not. "
                    "The gap is the attack surface."),
            meta={"hidden_ratio": round(ratio, 4),
                  "hidden_chars": len(re.sub(r"\s", "", hid))},
        ))

    toks = _tokens(hid)
    if len(toks) >= 8:
        uniq = len(set(toks))
        density = uniq / len(toks)
        # A hidden block of mostly distinct nouns reads like a keyword list, not prose.
        if density > 0.72 and not re.search(r"[.!?]", hid):
            out.append(Finding(
                code="HIDDEN_KEYWORD_LIST", severity=HIGH,
                title="Hidden text looks like a keyword list rather than prose",
                evidence=" ".join(toks[:24]),
                where="document",
                detail=("%d hidden terms, %d distinct, no sentence punctuation — "
                        "consistent with keyword stuffing." % (len(toks), uniq)),
                meta={"unique_ratio": round(density, 3)},
            ))
    return out


def check_keyword_stuffing(visible_text: str) -> list[Finding]:
    """Abnormal repetition in the visible text itself."""
    out: list[Finding] = []
    toks = _tokens(visible_text)
    if len(toks) < 120:
        return out
    counts = Counter(toks)
    worst = [(w, n) for w, n in counts.most_common(12) if n >= 12 and len(w) > 3]
    if worst:
        top = ", ".join("%s x%d" % (w, n) for w, n in worst[:6])
        rate = worst[0][1] / len(toks)
        if rate > 0.012:
            out.append(Finding(
                code="KEYWORD_REPETITION", severity=MEDIUM,
                title="A term repeats far more often than normal prose would",
                evidence=top, where="visible text",
                detail=("Top term accounts for %.1f%% of content words. Legitimate CVs "
                        "rarely exceed about 1%%." % (rate * 100)),
                meta={"top": worst[:6], "tokens": len(toks)},
            ))
    return out


def _shingles(text: str, n: int = 8) -> set[str]:
    toks = _tokens(text)
    return {" ".join(toks[i:i + n]) for i in range(max(0, len(toks) - n + 1))}


def check_jd_mirroring(jd: str, visible_text: str) -> list[Finding]:
    """Long verbatim runs copied from the JD into the résumé."""
    out: list[Finding] = []
    if not jd or not visible_text:
        return out
    a, b = _shingles(jd), _shingles(visible_text)
    if not a or not b:
        return out
    shared = a & b
    overlap = len(shared) / max(1, min(len(a), len(b)))
    if len(shared) >= 3 and overlap > 0.04:
        sample = sorted(shared, key=len, reverse=True)[:3]
        out.append(Finding(
            code="JD_MIRRORING",
            severity=HIGH if overlap > 0.15 else MEDIUM,
            title="Résumé reproduces long passages verbatim from the job description",
            evidence=" | ".join(s[:90] for s in sample),
            where="visible text",
            detail=("%d shared 8-word sequences (%.1f%% overlap). Copying the JD back "
                    "inflates keyword matching without evidencing experience."
                    % (len(shared), overlap * 100)),
            meta={"shared_sequences": len(shared), "overlap": round(overlap, 4)},
        ))
    return out


def check_metadata(ex: Extraction, candidate_name: str = "") -> list[Finding]:
    """Authorship and tooling signals from document properties."""
    out: list[Finding] = []
    md = ex.metadata or {}
    blob = " ".join(str(v) for v in md.values())

    author = str(md.get("author", "") or "")
    if candidate_name and author:
        surname = candidate_name.strip().split()[-1].lower()
        if len(surname) > 2 and surname not in author.lower():
            out.append(Finding(
                code="AUTHOR_MISMATCH", severity=LOW,
                title="Document author does not match the candidate name",
                evidence="author=%r candidate=%r" % (author[:60], candidate_name),
                where="metadata",
                detail=("Not conclusive — shared templates and CV services are common "
                        "— but worth noting alongside other flags."),
            ))

    for label, rx in (("resume-writing service",
                       r"resume\s*(writer|writing|service|builder|genius|now)"),
                      ("AI writing tool", r"chatgpt|gpt-4|claude|copilot|jasper|gemini")):
        m = re.search(rx, blob, re.I)
        if m:
            out.append(Finding(
                code="TOOLING_IN_METADATA", severity=INFO,
                title="Metadata mentions a %s" % label,
                evidence=m.group(0), where="metadata",
                detail="Informational only. Using tools is not malpractice.",
            ))

    created, modified = str(md.get("creationDate", "")), str(md.get("modDate", ""))
    if created and modified and modified < created:
        out.append(Finding(
            code="TIMESTAMP_ANOMALY", severity=LOW,
            title="Modification date precedes creation date",
            evidence="created=%s modified=%s" % (created[:24], modified[:24]),
            where="metadata", detail="Suggests the timestamps were edited.",
        ))
    return out


def fingerprint(visible_text: str) -> str:
    """Stable hash of content words, for spotting recycled documents in a batch."""
    toks = _tokens(visible_text)
    return hashlib.sha1(" ".join(sorted(set(toks))).encode("utf-8")).hexdigest()[:16]


def check_batch_duplicates(results: list[dict]) -> list[Finding]:
    """Near-identical résumés submitted under different names."""
    out: list[Finding] = []
    by_fp: dict[str, list[str]] = {}
    for r in results:
        by_fp.setdefault(r.get("fingerprint", ""), []).append(r.get("file", "?"))
    for fp, files in by_fp.items():
        if fp and len(files) > 1:
            out.append(Finding(
                code="DUPLICATE_CONTENT", severity=MEDIUM,
                title="Multiple submissions share near-identical content",
                evidence=", ".join(files[:6]), where="batch",
                detail="Same content fingerprint across %d files." % len(files),
                meta={"fingerprint": fp, "files": files},
            ))
    return out
