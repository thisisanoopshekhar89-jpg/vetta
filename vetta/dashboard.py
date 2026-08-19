"""Self-contained HTML report across every posting in a workspace."""

from __future__ import annotations

import html
import json

from .checks import Finding
from .store import Store

CSS = """
:root{--bg:#f6f8fb;--card:#fff;--ink:#16202c;--dim:#5d6b7c;--rule:#dde5ee;
--navy:#12243a;--accent:#2f6fbf;--fail:#c0392b;--review:#c98a12;--clean:#1e8a54;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{background:var(--navy);color:#fff;padding:22px 30px}
header h1{margin:0;font-size:21px;letter-spacing:.2px}
header p{margin:5px 0 0;opacity:.75;font-size:13.5px}
.wrap{max-width:1180px;margin:0 auto;padding:24px 30px 60px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:12px;
margin:18px 0 26px}
.tile{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:14px}
.tile b{display:block;font-size:26px;line-height:1.1}
.tile span{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}
h2{font-size:17px;margin:30px 0 10px;padding-bottom:7px;border-bottom:2px solid var(--rule)}
h3{font-size:15px;margin:22px 0 8px;color:var(--navy)}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--rule);border-radius:10px;overflow:hidden;margin-bottom:8px}
th{background:var(--navy);color:#fff;text-align:left;font-size:12px;
text-transform:uppercase;letter-spacing:.5px;padding:9px 11px}
td{padding:9px 11px;border-top:1px solid var(--rule);font-size:13.5px;vertical-align:top}
tr:nth-child(even) td{background:#fafcfe}
.v{font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:20px;color:#fff;
display:inline-block}
.v.fail{background:var(--fail)}.v.review{background:var(--review)}
.v.clean{background:var(--clean)}.v.error{background:#7c8794}
.bar{height:7px;background:var(--rule);border-radius:4px;overflow:hidden;min-width:70px}
.bar i{display:block;height:100%;background:var(--accent)}
.sev{font-size:11px;font-weight:700;padding:1px 7px;border-radius:4px;color:#fff}
.sev.high{background:var(--fail)}.sev.medium{background:var(--review)}
.sev.low{background:#5a7fa8}.sev.info{background:#98a3b0}
code{background:#eef3f9;padding:1px 5px;border-radius:4px;font-size:12.5px;
word-break:break-word}
.muted{color:var(--dim);font-size:13px}
.ev{color:var(--dim);font-size:12.5px;font-family:ui-monospace,Menlo,Consolas,monospace}
footer{color:var(--dim);font-size:12.5px;margin-top:40px;border-top:1px solid var(--rule);
padding-top:14px}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _verdict(v: str) -> str:
    return '<span class="v %s">%s</span>' % (_e(v), _e(v.upper()))


def _bar(pct: int) -> str:
    return ('<div class="bar"><i style="width:%d%%"></i></div>'
            '<span class="muted">%d%%</span>' % (max(0, min(100, pct)), pct))


def render_html(store: Store, cross: list[Finding] | None = None,
                title: str = "Vetta — screening report") -> str:
    st = store.stats()
    posts = store.postings()
    cross = cross or []

    h: list[str] = []
    h.append("<!doctype html><meta charset='utf-8'>")
    h.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    h.append("<title>%s</title><style>%s</style>" % (_e(title), CSS))
    h.append("<header><h1>Vetta</h1><p>Résumé match and integrity screening &middot; "
             "%d posting(s), %d submission(s)</p></header><div class='wrap'>"
             % (st["postings"], st["submissions"]))

    h.append("<div class='tiles'>")
    for label, val in (("Postings", st["postings"]), ("Open", st["open_postings"]),
                       ("Candidates", st["candidates"]), ("Submissions", st["submissions"]),
                       ("Clean", st["clean"]), ("Review", st["review"]),
                       ("Fail", st["fail"]), ("High findings", st["high_findings"])):
        h.append("<div class='tile'><b>%d</b><span>%s</span></div>" % (val, _e(label)))
    h.append("</div>")

    for p in posts:
        rows = store.submissions(posting_code=p["code"])
        h.append("<h2>%s &nbsp;<span class='muted'>%s &middot; %s &middot; %d applicant(s)"
                 "</span></h2>" % (_e(p["title"]), _e(p["code"]), _e(p["status"]),
                                   len(rows)))
        if not rows:
            h.append("<p class='muted'>No submissions yet.</p>")
            continue
        h.append("<table><tr><th>#</th><th>Candidate</th><th>File</th><th>Match</th>"
                 "<th>Integrity</th><th>Hidden</th><th>Top gaps</th></tr>")
        for i, r in enumerate(rows, 1):
            missing = ", ".join(json.loads(r["missing_terms"] or "[]")[:5])
            who = r["candidate_name"] or r["candidate_email"] or "—"
            h.append("<tr><td>%d</td><td><b>%s</b><br><span class='muted'>%s</span></td>"
                     "<td><code>%s</code></td><td>%s</td><td>%s</td>"
                     "<td>%.1f%%</td><td class='muted'>%s</td></tr>"
                     % (i, _e(who), _e(r["candidate_email"] or ""), _e(r["filename"]),
                        _bar(r["match_score"]), _verdict(r["verdict"]),
                        (r["hidden_ratio"] or 0) * 100, _e(missing)))
        h.append("</table>")

        flagged = [r for r in rows if r["verdict"] in ("fail", "review")]
        for r in flagged:
            fs = store.findings_for(r["id"])
            if not fs:
                continue
            h.append("<h3>%s &mdash; %s</h3>" % (_e(r["filename"]), _verdict(r["verdict"])))
            h.append("<table><tr><th>Severity</th><th>Finding</th><th>Evidence</th></tr>")
            for f in fs:
                h.append("<tr><td><span class='sev %s'>%s</span></td><td>%s"
                         "<br><span class='muted'>%s</span></td>"
                         "<td class='ev'>%s</td></tr>"
                         % (_e(f["severity"]), _e(f["severity"].upper()),
                            _e(f["title"]), _e(f["detail"]), _e(f["evidence"][:220])))
            h.append("</table>")

    if cross:
        h.append("<h2>Across the whole pool</h2>")
        h.append("<table><tr><th>Severity</th><th>Finding</th><th>Evidence</th></tr>")
        for f in cross:
            h.append("<tr><td><span class='sev %s'>%s</span></td><td>%s"
                     "<br><span class='muted'>%s</span></td><td class='ev'>%s</td></tr>"
                     % (_e(f.severity), _e(f.severity.upper()), _e(f.title),
                        _e(f.detail), _e(f.evidence[:220])))
        h.append("</table>")

    h.append("<footer>Scores are computed on visible text only, so hidden keywords "
             "cannot raise a match. An integrity verdict is a prompt to look, not a "
             "conclusion &mdash; read the recovered text before acting on it."
             "</footer></div>")
    return "\n".join(h)
