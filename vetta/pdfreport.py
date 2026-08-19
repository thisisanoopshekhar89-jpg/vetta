"""PDF screening reports.

One page-set per candidate: how the résumé performed against the job description,
term by term, followed by every integrity finding with its evidence and the hidden
text recovered verbatim. A batch gets a ranked summary page first.

Written so the report can be filed against an application record or handed to a
hiring manager without further explanation.
"""

from __future__ import annotations

import html
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

NAVY = colors.HexColor("#12243a")
ACCENT = colors.HexColor("#2f6fbf")
INK = colors.HexColor("#1b2733")
SLATE = colors.HexColor("#5a6b7d")
RULE = colors.HexColor("#cfdae7")
BOXBG = colors.HexColor("#f3f7fb")
FAIL = colors.HexColor("#c0392b")
REVIEW = colors.HexColor("#b9820f")
CLEAN = colors.HexColor("#1e8a54")
GREY = colors.HexColor("#7c8794")

VERDICT_COLOR = {"fail": FAIL, "review": REVIEW, "clean": CLEAN, "error": GREY}
SEV_COLOR = {"high": FAIL, "medium": REVIEW, "low": colors.HexColor("#5a7fa8"),
             "info": GREY}

S = {
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17, leading=21,
                         textColor=NAVY, spaceBefore=2, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15,
                         textColor=ACCENT, spaceBefore=13, spaceAfter=5),
    "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9.3, leading=13,
                        textColor=INK, alignment=TA_JUSTIFY, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.2, leading=11,
                            textColor=SLATE, spaceAfter=4),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.2, leading=10.6,
                           textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName="Helvetica-Bold", fontSize=8.2,
                            leading=10.6, textColor=colors.white),
    "mono": ParagraphStyle("mono", fontName="Courier", fontSize=7.6, leading=9.8,
                           textColor=INK),
    "cover_t": ParagraphStyle("ct", fontName="Helvetica-Bold", fontSize=26, leading=30,
                              textColor=NAVY, spaceAfter=6),
    "cover_s": ParagraphStyle("cs", fontName="Helvetica", fontSize=12, leading=16,
                              textColor=SLATE, spaceAfter=3),
}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _tbl(data, widths, header=True, zebra=True):
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    if zebra:
        style.append(("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1),
                      [colors.white, BOXBG]))
    return Table(data, colWidths=widths, repeatRows=1 if header else 0,
                 style=TableStyle(style))


def _verdict_chip(verdict: str, width: float):
    col = VERDICT_COLOR.get(verdict, GREY)
    t = Table([[Paragraph("<font color='white'><b>%s</b></font>" % _e(verdict.upper()),
                          S["cell"])]], colWidths=[width], rowHeights=[15])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col),
                           ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 4),
                           ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _score_bar(pct: int, width: float):
    pct = max(0, min(100, int(pct or 0)))
    filled = max(0.6, width * pct / 100.0)
    t = Table([[""]], colWidths=[filled], rowHeights=[6])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    outer = Table([[t]], colWidths=[width])
    outer.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RULE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                               ("ALIGN", (0, 0), (-1, -1), "LEFT")]))
    return outer


def _candidate_flow(r, fw: float, role: str = "") -> list:
    """Flowables for one screened résumé."""
    f: list = []
    ident = getattr(r, "identity", {}) or {}
    who = ident.get("name") or ident.get("label") or r.file

    f.append(Paragraph(_e(who), S["h1"]))
    meta = []
    if ident.get("email"):
        meta.append(_e(ident["email"]))
    if ident.get("phone"):
        meta.append(_e(ident["phone"]))
    meta.append("file: %s" % _e(r.file))
    if role:
        meta.insert(0, "applied to: <b>%s</b>" % _e(role))
    f.append(Paragraph(" &nbsp;·&nbsp; ".join(meta), S["small"]))

    if r.error:
        f.append(Paragraph("This file could not be read: %s" % _e(r.error), S["p"]))
        return f

    # --- performance against the job ---
    f.append(Paragraph("Performance against the job description", S["h2"]))
    if r.match.weights:
        head = [Paragraph("Match", S["cellh"]), Paragraph("Requirement terms",
                                                          S["cellh"]),
                Paragraph("Integrity", S["cellh"]), Paragraph("Hidden text", S["cellh"])]
        row = [
            [_score_bar(r.match.score, fw * 0.18 - 12),
             Paragraph("<b>%d%%</b> — %s" % (r.match.score, _e(r.match.band)),
                       S["cell"])],
            Paragraph("%d of %d evidenced in visible text"
                      % (len(r.match.matched), len(r.match.weights)), S["cell"]),
            _verdict_chip(r.verdict, fw * 0.16 - 12),
            Paragraph("%.1f%% of document text" % (r.hidden_ratio * 100), S["cell"]),
        ]
        f.append(_tbl([head, row],
                      [fw * 0.20, fw * 0.34, fw * 0.18, fw * 0.28]))
    else:
        f.append(Paragraph("No job description was supplied, so no match was scored. "
                           "Integrity checks were still run.", S["p"]))

    if r.match.matched:
        f.append(Paragraph("Requirements evidenced", S["h2"]))
        f.append(Paragraph(_e(", ".join(r.match.matched)), S["p"]))
    if r.match.missing:
        f.append(Paragraph("Requirements not evidenced", S["h2"]))
        f.append(Paragraph(_e(", ".join(r.match.missing)), S["p"]))
    if r.match.hidden_only:
        f.append(Paragraph(
            "<b>Excluded from the score:</b> %s — these requirement terms appear only "
            "in text that is not visible on the page, so they earned nothing."
            % _e(", ".join(r.match.hidden_only)), S["p"]))

    # --- integrity ---
    c = r.counts()
    f.append(Paragraph("Integrity findings", S["h2"]))
    if not r.findings:
        f.append(Paragraph("None. Nothing in this document is hidden from a reader, "
                           "and no malpractice signal was raised.", S["p"]))
    else:
        f.append(Paragraph("%d high · %d medium · %d low · %d informational"
                           % (c["high"], c["medium"], c["low"], c["info"]),
                           S["small"]))
        data = [[Paragraph("Severity", S["cellh"]), Paragraph("Finding", S["cellh"]),
                 Paragraph("Evidence", S["cellh"])]]
        for x in r.findings:
            sev = Table([[Paragraph("<font color='white'><b>%s</b></font>"
                                    % _e(x.severity.upper()), S["cell"])]],
                        colWidths=[fw * 0.11 - 10], rowHeights=[13])
            sev.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), SEV_COLOR.get(x.severity, GREY)),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1)]))
            what = "<b>%s</b>" % _e(x.title)
            if x.where:
                what += " <font color='#5a6b7d'>(%s)</font>" % _e(x.where)
            if x.detail:
                what += "<br/><font size='7.6' color='#5a6b7d'>%s</font>" % _e(x.detail)
            data.append([sev, Paragraph(what, S["cell"]),
                         Paragraph(_e(x.evidence[:260]), S["mono"])])
        f.append(_tbl(data, [fw * 0.11, fw * 0.51, fw * 0.38]))

    if r.hidden_text.strip():
        f.append(Paragraph("Hidden text recovered", S["h2"]))
        f.append(Paragraph("Reproduced exactly as extracted. A human reading the page "
                           "would not have seen this.", S["small"]))
        body = r.hidden_text.strip()[:2600]
        box = _tbl([[Paragraph(_e(body), S["mono"])]], [fw], header=False, zebra=False)
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), BOXBG),
                                 ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 7),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                                 ("TOPPADDING", (0, 0), (-1, -1), 6),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        f.append(box)
    return f


def build_pdf(results, out_path: str, role: str = "", jd_excerpt: str = "",
              batch_findings=None, generated: str | None = None) -> str:
    """Write a PDF report for one or many screened résumés."""
    results = list(results)
    batch_findings = list(batch_findings or [])
    stamp = generated or datetime.now().strftime("%d %B %Y, %H:%M")

    doc = BaseDocTemplate(out_path, pagesize=A4,
                          leftMargin=17 * mm, rightMargin=15 * mm,
                          topMargin=16 * mm, bottomMargin=17 * mm,
                          title="Vetta screening report",
                          author="Vetta")
    fw = doc.width

    def deco(canv, _doc):
        canv.saveState()
        canv.setFillColor(NAVY)
        canv.rect(0, A4[1] - 7 * mm, A4[0], 7 * mm, stroke=0, fill=1)
        canv.setFont("Helvetica-Bold", 7.6)
        canv.setFillColor(SLATE)
        canv.drawString(17 * mm, 11 * mm, "VETTA")
        canv.setFont("Helvetica", 7.4)
        canv.drawString(32 * mm, 11 * mm,
                        "screening report  ·  %s  ·  signals for human review" % stamp)
        canv.drawRightString(A4[0] - 15 * mm, 11 * mm, "Page %d" % canv.getPageNumber())
        canv.setStrokeColor(RULE)
        canv.line(17 * mm, 14 * mm, A4[0] - 15 * mm, 14 * mm)
        canv.restoreState()

    doc.addPageTemplates([PageTemplate(
        id="main",
        frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)],
        onPage=deco)])

    f: list = []
    ok = [r for r in results if not r.error]

    # --- cover / summary ---
    f.append(Spacer(1, 6 * mm))
    f.append(Paragraph("Screening report", S["cover_t"]))
    if role:
        f.append(Paragraph(_e(role), S["cover_s"]))
    f.append(Paragraph("%d résumé%s screened &nbsp;·&nbsp; %s"
                       % (len(results), "" if len(results) == 1 else "s", stamp),
                       S["small"]))
    f.append(Spacer(1, 4 * mm))

    tally = {v: sum(1 for r in results if r.verdict == v)
             for v in ("clean", "review", "fail", "error")}
    f.append(_tbl([
        [Paragraph("Screened", S["cellh"]), Paragraph("Clean", S["cellh"]),
         Paragraph("Review", S["cellh"]), Paragraph("Fail", S["cellh"]),
         Paragraph("Unreadable", S["cellh"])],
        [Paragraph(str(len(results)), S["cell"]),
         Paragraph(str(tally["clean"]), S["cell"]),
         Paragraph(str(tally["review"]), S["cell"]),
         Paragraph(str(tally["fail"]), S["cell"]),
         Paragraph(str(tally["error"]), S["cell"])]],
        [fw * 0.2] * 5))

    if len(results) > 1 and ok:
        f.append(Paragraph("Ranked by match", S["h2"]))
        data = [[Paragraph("#", S["cellh"]), Paragraph("Candidate", S["cellh"]),
                 Paragraph("File", S["cellh"]), Paragraph("Match", S["cellh"]),
                 Paragraph("Integrity", S["cellh"]), Paragraph("Hidden", S["cellh"])]]
        ranked = sorted(ok, key=lambda r: (-r.match.score, r.verdict != "clean"))
        for i, r in enumerate(ranked, 1):
            ident = getattr(r, "identity", {}) or {}
            who = ident.get("name") or ident.get("label") or r.file
            data.append([
                Paragraph(str(i), S["cell"]), Paragraph(_e(who), S["cell"]),
                Paragraph(_e(r.file), S["cell"]),
                Paragraph("%d%%" % r.match.score if r.match.weights else "—", S["cell"]),
                Paragraph("<font color='#%s'><b>%s</b></font>"
                          % (VERDICT_COLOR.get(r.verdict, GREY).hexval()[2:],
                             _e(r.verdict.upper())), S["cell"]),
                Paragraph("%.1f%%" % (r.hidden_ratio * 100), S["cell"])])
        f.append(_tbl(data, [fw * 0.05, fw * 0.27, fw * 0.28, fw * 0.11,
                             fw * 0.15, fw * 0.14]))

    if batch_findings:
        f.append(Paragraph("Across this batch", S["h2"]))
        data = [[Paragraph("Severity", S["cellh"]), Paragraph("Finding", S["cellh"]),
                 Paragraph("Evidence", S["cellh"])]]
        for x in batch_findings:
            data.append([Paragraph(_e(x.severity.upper()), S["cell"]),
                         Paragraph("<b>%s</b><br/><font size='7.6' color='#5a6b7d'>%s"
                                   "</font>" % (_e(x.title), _e(x.detail)), S["cell"]),
                         Paragraph(_e(x.evidence[:200]), S["mono"])])
        f.append(_tbl(data, [fw * 0.12, fw * 0.50, fw * 0.38]))

    if jd_excerpt.strip():
        f.append(Paragraph("Job description used", S["h2"]))
        f.append(Paragraph(_e(jd_excerpt.strip()[:1200]), S["small"]))

    f.append(Paragraph("How to read this report", S["h2"]))
    f.append(Paragraph(
        "The match score is computed on <b>visible text only</b>. Text that a reader "
        "cannot see — white-on-white runs, sub-4pt type, invisible render modes, "
        "content positioned off the page — is excluded from scoring and reported as a "
        "finding instead, so hidden keywords cannot raise a score.", S["p"]))
    f.append(Paragraph(
        "An integrity verdict is a prompt to look more closely, not a conclusion. "
        "<b>review</b> in particular is not an accusation: word processors emit hidden "
        "characters, and some templates use white text for layout. Read the recovered "
        "text before acting on any finding, and keep a human on every decision.",
        S["p"]))

    # --- per candidate ---
    for r in results:
        f.append(PageBreak())
        f.extend(_candidate_flow(r, fw, role))

    f.append(Spacer(1, 8 * mm))
    f.append(Paragraph(
        "Vetta &copy; 2026 Anoop Shekhar. All rights reserved. Not to be copied or "
        "deployed without written permission — thisisanoopshekhar89@gmail.com. This "
        "report records signals for human review and must not be used as the sole "
        "basis for a hiring decision.", S["small"]))

    doc.build(f)
    return out_path


def build_workspace_pdf(store, out_path: str, cross=None) -> str:
    """PDF for a whole workspace, straight from stored rows (no re-screening)."""
    from .checks import Finding

    class _M:
        def __init__(self, row):
            import json as _json
            self.score = row["match_score"]
            self.band = row["band"]
            self.matched = _json.loads(row["matched_terms"] or "[]")
            self.missing = _json.loads(row["missing_terms"] or "[]")
            self.hidden_only = []
            self.weights = {"_": 1} if row["band"] else {}

    class _R:
        def __init__(self, row, findings):
            self.file = row["filename"]
            self.match = _M(row)
            self.findings = findings
            self.verdict = row["verdict"]
            self.hidden_text = row["hidden_text"] or ""
            self.hidden_ratio = row["hidden_ratio"] or 0.0
            self.error = row["error"] or ""
            self.identity = {"name": row["candidate_name"] or "",
                             "email": row["candidate_email"] or "",
                             "phone": "", "label": row["filename"]}
            self.posting = row["posting_title"]

        def counts(self):
            c = {"high": 0, "medium": 0, "low": 0, "info": 0}
            for x in self.findings:
                c[x.severity] = c.get(x.severity, 0) + 1
            return c

    results = []
    for row in store.submissions():
        fs = [Finding(code=d["code"], severity=d["severity"], title=d["title"],
                      evidence=d["evidence"], where=d["location"], detail=d["detail"])
              for d in store.findings_for(row["id"])]
        results.append(_R(row, fs))

    posts = store.postings()
    role = (posts[0]["title"] if len(posts) == 1
            else "%d postings" % len(posts) if posts else "")
    return build_pdf(results, out_path, role=role, batch_findings=cross or [])
