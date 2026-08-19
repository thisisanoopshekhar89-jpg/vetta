"""Content-quality and credibility checks on the visible text.

These are about the claims, not the file: filler with no substance, the same line
recycled through several roles, and numbers that cannot be true. Every finding
here is a prompt for a human to read more closely — none of them proves anything,
so severities stay conservative.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from .checks import HIGH, INFO, LOW, MEDIUM, Finding

# Phrases that fill space without evidencing anything.
CLICHES = [
    "results-driven", "results oriented", "results-oriented", "team player",
    "hard working", "hard-working", "hardworking", "self-starter", "self starter",
    "go-getter", "think outside the box", "outside the box", "detail-oriented",
    "detail oriented", "dynamic professional", "proven track record",
    "excellent communication skills", "strong communication skills",
    "works well under pressure", "fast learner", "quick learner",
    "highly motivated", "passionate about", "synergy", "value add", "value-add",
    "best of breed", "world-class", "world class", "cutting edge", "cutting-edge",
    "seasoned professional", "wear many hats", "hit the ground running",
    "responsible for", "duties included", "familiar with", "exposure to",
    "good knowledge of", "well versed", "well-versed",
]

SUPERLATIVES = [
    "expert in all", "mastery of all", "unparalleled", "unmatched", "flawless",
    "never failed", "always exceeded", "best in the industry", "top 1%",
    "world's leading", "revolutionised", "revolutionized", "single-handedly",
]

# A quantified achievement looks like one of these.
METRIC = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|percent|k\b|m\b|bn\b|million|billion|hours?|days?|"
    r"weeks?|months?|fte|users?|clients?|customers?|staff|employees?|accounts?|"
    r"tickets?|records?|projects?)"
    r"|[$£€₹]\s*\d", re.I)

YEARS_CLAIM = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b[^.]{0,30}"
                         r"(?:experience|exp\b)", re.I)
YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
PCT = re.compile(r"\b(\d{2,5})\s*%")


def _lines(text: str) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        ln = re.sub(r"^\s*[•\-\*·●▪>]\s*", "", raw).strip()
        if len(ln) >= 25:
            out.append(ln)
    return out


def check_repetition(visible_text: str) -> list[Finding]:
    """The same bullet reused across roles, or a line pasted repeatedly."""
    out: list[Finding] = []
    lines = _lines(visible_text)
    if len(lines) < 4:
        return out

    norm = [re.sub(r"[^a-z0-9 ]", "", ln.lower()) for ln in lines]
    counts = Counter(norm)
    dupes = [(t, n) for t, n in counts.items() if n >= 2 and len(t) >= 30]
    if dupes:
        dupes.sort(key=lambda kv: -kv[1])
        worst, n = dupes[0]
        out.append(Finding(
            code="REPEATED_LINES",
            severity=MEDIUM if (n >= 3 or len(dupes) >= 3) else LOW,
            title="%d line(s) appear more than once" % len(dupes),
            evidence="x%d: %s" % (n, worst[:140]),
            where="visible text",
            detail=("Identical bullets repeated across roles usually mean padding, or a "
                    "template filled in once and duplicated."),
            meta={"duplicate_lines": len(dupes), "max_repeats": n}))

    # Near-duplicates: same opening seven words, different tail.
    heads = Counter(" ".join(t.split()[:7]) for t in norm if len(t.split()) >= 7)
    near = [(h, n) for h, n in heads.items() if n >= 3]
    if near:
        h, n = max(near, key=lambda kv: kv[1])
        out.append(Finding(
            code="TEMPLATED_LINES", severity=LOW,
            title="%d bullet group(s) start with the same wording" % len(near),
            evidence="x%d: %s..." % (n, h[:110]),
            where="visible text",
            detail="Formulaic phrasing repeated down the page rather than varied detail.",
            meta={"groups": len(near)}))
    return out


def check_genericness(visible_text: str) -> list[Finding]:
    """Filler density against evidence of specifics."""
    out: list[Finding] = []
    text = visible_text or ""
    words = re.findall(r"[a-zA-Z][a-zA-Z'\-]+", text)
    if len(words) < 120:
        return out

    low = text.lower()
    hits = [(c, low.count(c)) for c in CLICHES if c in low]
    total_hits = sum(n for _, n in hits)
    metrics = len(METRIC.findall(text))
    per_100 = 100.0 * total_hits / max(1, len(words))

    if total_hits >= 4 and per_100 >= 0.8:
        top = ", ".join("%s%s" % (c, " x%d" % n if n > 1 else "")
                        for c, n in sorted(hits, key=lambda kv: -kv[1])[:6])
        out.append(Finding(
            code="GENERIC_LANGUAGE",
            severity=MEDIUM if per_100 >= 1.6 else LOW,
            title="Heavy filler phrasing (%d instances)" % total_hits,
            evidence=top, where="visible text",
            detail=("%.1f filler phrases per 100 words, against %d quantified "
                    "statement(s). Describes attributes rather than what was done."
                    % (per_100, metrics)),
            meta={"cliche_hits": total_hits, "metrics": metrics,
                  "per_100_words": round(per_100, 2)}))

    if metrics == 0 and len(words) > 250:
        out.append(Finding(
            code="NO_QUANTIFIED_CLAIMS", severity=LOW,
            title="No quantified achievement anywhere in the document",
            evidence="%d words, zero figures, percentages or volumes" % len(words),
            where="visible text",
            detail=("Not malpractice, but it makes claims unverifiable and is the "
                    "signature of a generic or padded résumé."),
            meta={"words": len(words)}))
    return out


def check_implausible(visible_text: str) -> list[Finding]:
    """Numbers and claims that cannot be taken at face value."""
    out: list[Finding] = []
    text = visible_text or ""

    big = [int(m.group(1)) for m in PCT.finditer(text) if int(m.group(1)) > 300]
    if big:
        out.append(Finding(
            code="IMPLAUSIBLE_METRIC", severity=MEDIUM,
            title="Improbably large percentage claim",
            evidence=", ".join("%d%%" % b for b in sorted(big, reverse=True)[:5]),
            where="visible text",
            detail=("Improvements above roughly 300% are rare and usually a rebased or "
                    "mis-stated figure. Worth asking about rather than assuming."),
            meta={"values": sorted(big, reverse=True)[:8]}))

    low = text.lower()
    sup = [s for s in SUPERLATIVES if s in low]
    if sup:
        out.append(Finding(
            code="UNVERIFIABLE_SUPERLATIVE", severity=LOW,
            title="Absolute claims with no supporting detail",
            evidence=", ".join(sup[:5]), where="visible text",
            detail="Absolute statements that cannot be checked in an interview.",
            meta={"phrases": sup[:8]}))

    # A long undifferentiated tool list is a stuffing pattern even when visible.
    skills_block = re.search(
        r"(?:skills|technologies|tools|technical)\s*[:\n](.{0,900})", text, re.I | re.S)
    if skills_block:
        items = [s.strip() for s in re.split(r"[,;|•\n]", skills_block.group(1))
                 if 2 <= len(s.strip()) <= 30]
        if len(items) >= 35:
            out.append(Finding(
                code="SKILL_LIST_INFLATION", severity=LOW,
                title="Skills section lists %d items" % len(items),
                evidence=", ".join(items[:10]) + " ...",
                where="visible text",
                detail=("Long flat lists claim breadth without depth and are hard to "
                        "assess. Common in résumés written to satisfy keyword filters."),
                meta={"count": len(items)}))
    return out


def check_timeline(visible_text: str, this_year: int | None = None) -> list[Finding]:
    """Cross-check a stated years-of-experience claim against the dates on the page."""
    out: list[Finding] = []
    text = visible_text or ""
    year_now = this_year or datetime.now().year

    years = sorted({int(y) for y in YEAR.findall(text)})
    future = [y for y in years if y > year_now]
    if future:
        out.append(Finding(
            code="FUTURE_DATE", severity=MEDIUM,
            title="Date in the future",
            evidence=", ".join(str(y) for y in future[:5]), where="visible text",
            detail="A year later than the present usually means a typo — or an edit.",
            meta={"years": future[:8]}))

    claims = [int(m.group(1)) for m in YEARS_CLAIM.finditer(text)]
    past = [y for y in years if y <= year_now]
    if claims and past:
        claimed = max(claims)
        span = year_now - min(past)
        if claimed > span + 2:
            out.append(Finding(
                code="EXPERIENCE_CLAIM_MISMATCH", severity=MEDIUM,
                title="Stated experience exceeds the dates shown",
                evidence="claims %d years; earliest date on the résumé is %d (%d years)"
                         % (claimed, min(past), span),
                where="visible text",
                detail=("The claim and the timeline disagree by %d years. May be earlier "
                        "work left off the document — worth one question."
                        % (claimed - span)),
                meta={"claimed": claimed, "earliest_year": min(past), "span": span}))
    return out


def run_all(visible_text: str) -> list[Finding]:
    return (check_repetition(visible_text)
            + check_genericness(visible_text)
            + check_implausible(visible_text)
            + check_timeline(visible_text))
