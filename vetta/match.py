"""Score a résumé against a job description.

Design decision that matters: scoring runs on the *visible* text only. Keywords
smuggled in white-on-white text, 1pt fonts or off-page runs earn nothing, so
stuffing cannot lift a score. That is the difference between this and a naive
"extract all text, count keywords" screener.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing down during
each few for from further had has have having he her here hers herself him himself his
how i if in into is it its itself just me more most my myself no nor not of off on once
only or other our ours ourselves out over own same she should so some such than that the
their theirs them themselves then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your
yours yourself yourselves will shall may might must ability able across along already
although always among amount another anyone anything applicant apply applying approach
around available based basic best better beyond candidate candidates career clear
clearly closely come company culture day days deliver ensure ensuring etc every
everything experience expert following full future general get give given global good
great group help high highly hour hours ideal include includes including individual
job join key kind large level like look looking love make makes making many meet member
members mission much need needs new next non offer offers one open opportunity part
people per person plus position preferred prior proven provide providing real really
right role roles set several similar since site skill skills small start strong take
team teams thing things time together top total towards two type understand understanding
use used using various want we well what whether within work working world would year
years benefits feedback growth commercial documents create
""".split())

# Multi-word phrases worth catching as single units when present in a JD.
PHRASES = [
    "process mapping", "value stream mapping", "customer journey", "process improvement",
    "continuous improvement", "six sigma", "lean six sigma", "root cause analysis",
    "business process", "process documentation", "standard operating procedure",
    "change management", "stakeholder management", "project management",
    "business analysis", "requirements gathering", "gap analysis", "process design",
    "workflow automation", "robotic process automation", "intelligent automation",
    "machine learning", "generative ai", "large language model", "prompt engineering",
    "agentic ai", "data analysis", "data analytics", "business intelligence",
    "power bi", "sql", "python", "excel", "erp", "crm", "api", "rest api",
    "project manager", "resource planning", "capacity planning", "kpi",
    "service level agreement", "quality assurance", "risk management",
    "insurance operations", "underwriting", "claims", "policy servicing",
    "digital transformation", "operating model", "target operating model",
    "cost reduction", "operational efficiency", "vendor management",
]

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{1,}")


@dataclass
class MatchResult:
    score: int = 0
    band: str = ""
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    hidden_only: list[str] = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    coverage: float = 0.0

    def as_dict(self) -> dict:
        return {
            "score": self.score, "band": self.band, "coverage": round(self.coverage, 3),
            "matched": self.matched, "missing": self.missing,
            "hidden_only": self.hidden_only,
        }


def normalise(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("&amp;", "&").replace("’", "'").replace("–", "-")
    return re.sub(r"\s+", " ", text)


def jd_terms(jd: str, cap: int = 60) -> dict[str, float]:
    """Weighted requirement terms from a JD. Weight rises with repetition."""
    flat = normalise(jd)
    weights: dict[str, float] = {}

    for phrase in PHRASES:
        n = flat.count(phrase)
        if n:
            weights[phrase] = 2.2 + math.log1p(n)

    words: dict[str, int] = {}
    for m in _WORD.finditer(flat):
        w = m.group(0).strip(".-/")
        if len(w) < 3 or w in STOPWORDS or w.isdigit():
            continue
        if any(w in p for p in weights):        # already covered by a phrase
            continue
        words[w] = words.get(w, 0) + 1

    for w, n in words.items():
        if n >= 2 or len(w) > 6:                # repeated, or specific enough to matter
            weights[w] = 1.0 + math.log1p(n)

    return dict(sorted(weights.items(), key=lambda kv: -kv[1])[:cap])


def score(jd: str, visible_text: str, hidden_text: str = "") -> MatchResult:
    """Score visible résumé text against the JD; report hidden-only terms separately."""
    terms = jd_terms(jd)
    if not terms:
        return MatchResult(score=0, band="no requirements detected")

    vis = normalise(visible_text)
    hid = normalise(hidden_text)

    matched, missing, hidden_only = [], [], []
    got = 0.0
    total = sum(terms.values())
    for term, w in terms.items():
        if term in vis:
            matched.append(term)
            got += w
        else:
            missing.append(term)
            if term and term in hid:
                hidden_only.append(term)

    coverage = got / total if total else 0.0
    pct = int(round(100 * coverage))
    band = ("strong match" if pct >= 65 else
            "good match" if pct >= 50 else
            "partial match" if pct >= 32 else
            "weak match")
    return MatchResult(score=pct, band=band, matched=matched, missing=missing,
                       hidden_only=hidden_only, weights=terms, coverage=coverage)
