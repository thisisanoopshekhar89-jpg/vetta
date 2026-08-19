"""Orchestration: one résumé in, a match score and an integrity verdict out."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import malpractice, quality
from .checks import HIGH, MEDIUM, Finding, scan_injection, scan_unicode
from .extract import extract
from .identity import extract_identity
from .match import MatchResult, score


@dataclass
class ScreenResult:
    file: str
    match: MatchResult
    findings: list[Finding] = field(default_factory=list)
    pages: int = 0
    kind: str = ""
    fingerprint: str = ""
    hidden_text: str = ""
    hidden_ratio: float = 0.0
    identity: dict = field(default_factory=dict)
    error: str = ""

    @property
    def verdict(self) -> str:
        """Integrity verdict, independent of how well the candidate matches."""
        if self.error:
            return "error"
        if any(f.severity == HIGH for f in self.findings):
            return "fail"
        if any(f.severity == MEDIUM for f in self.findings):
            return "review"
        return "clean"

    def counts(self) -> dict[str, int]:
        c = {"high": 0, "medium": 0, "low": 0, "info": 0}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c

    def as_dict(self) -> dict:
        return {
            "file": self.file, "kind": self.kind, "pages": self.pages,
            "verdict": self.verdict, "match": self.match.as_dict(),
            "hidden_ratio": round(self.hidden_ratio, 4),
            "hidden_text": self.hidden_text[:2000],
            "fingerprint": self.fingerprint,
            "identity": self.identity,
            "counts": self.counts(),
            "findings": [f.as_dict() for f in self.findings],
            "error": self.error,
        }


def screen_one(path: str, jd: str = "", candidate_name: str = "",
               ex=None) -> ScreenResult:
    """Extract, score against the JD, then run every integrity check.

    Pass `ex` when the caller has already extracted the document; extraction
    is the most expensive step, so the batch path reuses one Extraction
    instead of repeating it.
    """
    if ex is None:
        try:
            ex = extract(path)
        except Exception as exc:                   # unreadable or unsupported
            return ScreenResult(file=os.path.basename(path),
                                match=MatchResult(),
                                error="%s: %s" % (type(exc).__name__, exc))

    m = score(jd, ex.visible_text, ex.hidden_text) if jd else MatchResult()

    findings: list[Finding] = list(ex.findings)

    # Injection is judged on what the machine reads, since that is what would act on it.
    findings += scan_injection(ex.machine_text, where="document text")
    for key, blob in (ex.metadata or {}).items():
        findings += scan_injection(str(blob), where="metadata:%s" % key)
    findings += scan_unicode(ex.machine_text, where="document text")

    findings += malpractice.check_hidden_payload(ex)
    findings += malpractice.check_keyword_stuffing(ex.visible_text)
    findings += malpractice.check_metadata(ex, candidate_name)
    if jd:
        findings += malpractice.check_jd_mirroring(jd, ex.visible_text)

    # Claim-level quality: padding, recycled bullets, filler, impossible numbers.
    findings += quality.run_all(ex.visible_text)

    if m.hidden_only:
        findings.append(Finding(
            code="HIDDEN_KEYWORDS_SCORED_ZERO", severity=HIGH,
            title="JD keywords appear only in hidden text",
            evidence=", ".join(m.hidden_only[:14]),
            where="document",
            detail=("These requirement terms are present in the file but not visible on "
                    "the page. They were excluded from the match score, which is why "
                    "stuffing does not lift it here."),
            meta={"terms": m.hidden_only},
        ))

    findings.sort(key=lambda f: (-f.rank(), f.code))
    return ScreenResult(
        file=os.path.basename(path), match=m, findings=findings, pages=ex.pages,
        kind=ex.kind, fingerprint=malpractice.fingerprint(ex.visible_text),
        hidden_text=ex.hidden_text, hidden_ratio=ex.hidden_ratio,
        identity=extract_identity(ex.visible_text, path),
    )


def screen_many(paths: list[str], jd: str = "") -> tuple[list[ScreenResult], list[Finding]]:
    """Screen a batch and add cross-document checks."""
    results = [screen_one(p, jd) for p in paths]
    batch = malpractice.check_batch_duplicates([r.as_dict() for r in results])
    return results, batch
