"""Shared finding model, injection lexicon, and Unicode checks."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any

HIGH, MEDIUM, LOW, INFO = "high", "medium", "low", "info"
_ORDER = {HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0}


@dataclass
class Finding:
    """One thing a human reader would not see but a parser would."""

    code: str
    severity: str
    title: str
    evidence: str = ""
    where: str = ""
    detail: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def rank(self) -> int:
        return _ORDER.get(self.severity, 0)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- prompt-injection style phrasing ------------------------------------------
# Deliberately narrow. These are imperative, instruction-shaped strings that have
# no business appearing in a CV; broad keyword lists would flag ordinary prose.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instruction|prompt|direction)",
     "instruction override"),
    (r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above|earlier)",
     "instruction override"),
    (r"forget\s+(everything|all|your)\s+(you|previous|prior|instructions)",
     "instruction override"),
    (r"you\s+are\s+(now\s+)?(an?\s+)?(ai|assistant|language\s+model|recruiter|screener)",
     "role reassignment"),
    (r"(new|updated|revised)\s+(system\s+)?(instruction|prompt|rule)s?\s*[:\-]",
     "injected instruction block"),
    (r"system\s*(prompt|message|instruction)\s*[:\-]", "system prompt spoofing"),
    (r"as\s+an\s+ai\s+language\s+model", "model-directed text"),
    (r"(recommend|advance|move|forward|progress|shortlist|select)\s+(this\s+)?"
     r"(candidate|applicant|resume|cv|profile)?\s*(for|to)\s+(the\s+)?"
     r"(next\s+(round|stage|step)|interview|hire)", "hiring-decision instruction"),
    (r"(this|the)\s+candidate\s+is\s+(highly\s+|extremely\s+|exceptionally\s+)?"
     r"(qualified|the\s+best|a\s+perfect\s+(fit|match)|ideal)",
     "self-assessed verdict aimed at a model"),
    (r"(rate|score|rank)\s+(this|the)\s+(candidate|resume|cv|applicant)\s+"
     r"(as\s+)?(highly|top|100|10/10|maximum)", "scoring instruction"),
    (r"do\s+not\s+(reject|filter|screen|disqualify)", "filter-bypass instruction"),
    (r"(must|should)\s+be\s+(shortlisted|interviewed|hired)", "hiring-decision instruction"),
    (r"</?(system|assistant|user|instruction)s?>", "chat-role tag injection"),
    (r"\{\{\s*[a-z_]+\s*\}\}|\[\[\s*[a-z_]+\s*\]\]", "template placeholder leakage"),
]

_COMPILED = [(re.compile(p, re.I | re.S), label) for p, label in INJECTION_PATTERNS]


def scan_injection(text: str, where: str = "") -> list[Finding]:
    """Find instruction-shaped strings in text a document reader would ingest."""
    out: list[Finding] = []
    if not text:
        return out
    flat = re.sub(r"\s+", " ", text)
    for rx, label in _COMPILED:
        for m in rx.finditer(flat):
            snippet = flat[max(0, m.start() - 40):m.end() + 40].strip()
            out.append(Finding(
                code="INJECTION_PHRASE",
                severity=HIGH,
                title="Instruction-shaped text aimed at an AI reader",
                evidence=snippet[:200],
                where=where,
                detail=("Matched pattern for %s. A CV should describe experience, not "
                        "issue instructions to whatever reads it." % label),
                meta={"pattern": label, "matched": m.group(0)[:120]},
            ))
    return out


# --- Unicode tricks -----------------------------------------------------------
ZERO_WIDTH = {
    0x200B: "ZERO WIDTH SPACE",
    0x200C: "ZERO WIDTH NON-JOINER",
    0x200D: "ZERO WIDTH JOINER",
    0x2060: "WORD JOINER",
    0xFEFF: "ZERO WIDTH NO-BREAK SPACE",
    0x00AD: "SOFT HYPHEN",
}
BIDI = {
    0x202A: "LEFT-TO-RIGHT EMBEDDING",
    0x202B: "RIGHT-TO-LEFT EMBEDDING",
    0x202C: "POP DIRECTIONAL FORMATTING",
    0x202D: "LEFT-TO-RIGHT OVERRIDE",
    0x202E: "RIGHT-TO-LEFT OVERRIDE",
    0x2066: "LEFT-TO-RIGHT ISOLATE",
    0x2067: "RIGHT-TO-LEFT ISOLATE",
    0x2068: "FIRST STRONG ISOLATE",
    0x2069: "POP DIRECTIONAL ISOLATE",
}
# Cyrillic and Greek characters that look identical to Latin ones.
HOMOGLYPHS = set("АВЕКМНОРСТХаеорсхуѕіјԁ") | set("ΑΒΕΗΙΚΜΝΟΡΤΥΧοι")


def scan_unicode(text: str, where: str = "") -> list[Finding]:
    """Invisible or deceptive characters inside otherwise ordinary text."""
    out: list[Finding] = []
    if not text:
        return out

    zw = {}
    bd = {}
    pua = 0
    for ch in text:
        cp = ord(ch)
        if cp in ZERO_WIDTH:
            zw[ZERO_WIDTH[cp]] = zw.get(ZERO_WIDTH[cp], 0) + 1
        elif cp in BIDI:
            bd[BIDI[cp]] = bd.get(BIDI[cp], 0) + 1
        elif 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0x10FFFD:
            pua += 1

    if zw:
        total = sum(zw.values())
        out.append(Finding(
            code="ZERO_WIDTH_CHARS", severity=MEDIUM if total > 3 else LOW,
            title="Zero-width characters present",
            evidence=", ".join("%s x%d" % (k, v) for k, v in zw.items()),
            where=where,
            detail=("Zero-width characters render as nothing. They are used to smuggle "
                    "payloads or to break keyword matching while text still reads "
                    "normally. A handful can be legitimate (soft hyphens from a word "
                    "processor); dozens are not."),
            meta={"counts": zw},
        ))
    if bd:
        out.append(Finding(
            code="BIDI_CONTROLS", severity=HIGH,
            title="Bidirectional override characters present",
            evidence=", ".join("%s x%d" % (k, v) for k, v in bd.items()),
            where=where,
            detail=("Bidi overrides can make displayed text differ from the underlying "
                    "character order, so a human and a parser read different things."),
            meta={"counts": bd},
        ))
    if pua:
        out.append(Finding(
            code="PRIVATE_USE_CHARS", severity=MEDIUM,
            title="Private-use-area characters present",
            evidence="%d character(s)" % pua, where=where,
            detail="Private-use codepoints have no standard glyph and no business in a CV.",
            meta={"count": pua},
        ))

    # Latin words carrying a lookalike from another script.
    sus = []
    for word in re.findall(r"[^\W\d_]{3,}", text, re.UNICODE):
        if any(c in HOMOGLYPHS for c in word) and any(
                "LATIN" in unicodedata.name(c, "") for c in word):
            sus.append(word)
    if sus:
        uniq = sorted(set(sus))[:8]
        out.append(Finding(
            code="MIXED_SCRIPT_WORDS", severity=MEDIUM,
            title="Words mixing Latin with lookalike characters",
            evidence=", ".join(uniq), where=where,
            detail=("Cyrillic or Greek lookalikes inside Latin words defeat exact keyword "
                    "matching while appearing normal to a reader."),
            meta={"words": uniq, "count": len(sus)},
        ))
    return out
