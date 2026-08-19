"""Human-readable and machine-readable reports."""

from __future__ import annotations

import json

from .checks import Finding
from .screen import ScreenResult

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
RED, YEL, GRN, CYA, GRY = ("\033[31m", "\033[33m", "\033[32m", "\033[36m", "\033[90m")

SEV_COLOR = {"high": RED, "medium": YEL, "low": CYA, "info": GRY}
VERDICT_COLOR = {"fail": RED, "review": YEL, "clean": GRN, "error": GRY}
VERDICT_LABEL = {"fail": "FAIL", "review": "REVIEW", "clean": "CLEAN", "error": "ERROR"}


def _c(s: str, col: str, use_color: bool) -> str:
    return "%s%s%s" % (col, s, RESET) if use_color else s


def render_text(results: list[ScreenResult], batch: list[Finding] | None = None,
                color: bool = True, verbose: bool = False) -> str:
    lines: list[str] = []
    batch = batch or []

    for r in results:
        lines.append("")
        lines.append(_c("=" * 78, GRY, color))
        head = "%s   [%s]" % (r.file, VERDICT_LABEL[r.verdict])
        lines.append(_c(head, BOLD + VERDICT_COLOR[r.verdict], color))
        lines.append(_c("=" * 78, GRY, color))

        if r.error:
            lines.append("  could not read file: %s" % r.error)
            continue

        if r.match.weights:
            lines.append("  Match against JD : %d%%  (%s)" % (r.match.score, r.match.band))
            lines.append("  Matched terms    : %d of %d"
                         % (len(r.match.matched), len(r.match.weights)))
            if r.match.missing:
                lines.append("  Top missing      : %s"
                             % ", ".join(r.match.missing[:10]))
        else:
            lines.append("  Match against JD : not scored (no JD supplied)")

        c = r.counts()
        lines.append("  Integrity        : %d high, %d medium, %d low, %d info"
                     % (c["high"], c["medium"], c["low"], c["info"]))
        lines.append("  Hidden text      : %.1f%% of document text" % (r.hidden_ratio * 100))
        lines.append("  Pages / type     : %s / %s" % (r.pages or "-", r.kind))

        if r.findings:
            lines.append("")
            lines.append(_c("  Findings", BOLD, color))
            for f in r.findings:
                tag = _c("[%s]" % f.severity.upper().ljust(6),
                         SEV_COLOR.get(f.severity, ""), color)
                lines.append("  %s %s" % (tag, f.title))
                if f.where:
                    lines.append("           %s" % _c("at %s" % f.where, DIM, color))
                if f.evidence:
                    ev = f.evidence.replace("\n", " ")
                    lines.append("           %s" % _c("evidence: %s" % ev[:150], DIM, color))
                if verbose and f.detail:
                    lines.append("           %s" % _c(f.detail, DIM, color))
        else:
            lines.append("")
            lines.append(_c("  No integrity issues found.", GRN, color))

        if r.hidden_text.strip() and verbose:
            lines.append("")
            lines.append(_c("  Hidden text recovered:", BOLD, color))
            for ln in r.hidden_text.strip().splitlines()[:12]:
                lines.append("    %s" % ln.strip()[:150])

    if batch:
        lines.append("")
        lines.append(_c("Batch findings", BOLD, color))
        for f in batch:
            lines.append("  %s %s" % (_c("[%s]" % f.severity.upper(),
                                         SEV_COLOR.get(f.severity, ""), color), f.title))
            lines.append("           %s" % _c(f.evidence, DIM, color))

    if len(results) > 1:
        lines.append("")
        lines.append(_c("-" * 78, GRY, color))
        tally: dict[str, int] = {}
        for r in results:
            tally[r.verdict] = tally.get(r.verdict, 0) + 1
        lines.append("Screened %d file(s): %s" % (
            len(results),
            ", ".join("%d %s" % (n, k) for k, n in sorted(tally.items()))))
        scored = [r for r in results if r.match.weights]
        if scored:
            ranked = sorted(scored, key=lambda r: -r.match.score)
            lines.append("")
            lines.append(_c("Ranked by match (integrity verdict in brackets)", BOLD, color))
            for r in ranked:
                lines.append("  %3d%%  %-46s [%s]"
                             % (r.match.score, r.file[:46], VERDICT_LABEL[r.verdict]))
    lines.append("")
    return "\n".join(lines)


def render_json(results: list[ScreenResult],
                batch: list[Finding] | None = None) -> str:
    return json.dumps({
        "results": [r.as_dict() for r in results],
        "batch_findings": [f.as_dict() for f in (batch or [])],
        "summary": {
            "files": len(results),
            "fail": sum(1 for r in results if r.verdict == "fail"),
            "review": sum(1 for r in results if r.verdict == "review"),
            "clean": sum(1 for r in results if r.verdict == "clean"),
        },
    }, indent=2)
