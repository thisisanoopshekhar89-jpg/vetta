"""Command line interface.

Two modes:

  vetta screen FILE... --jd role.txt     one-off, nothing persisted
  vetta job / intake / shortlist / ...   a workspace holding many postings

Passing files as the first argument implies `screen`, so the quick form stays short.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import pipeline
from .dashboard import render_html
from .pdfreport import build_pdf, build_workspace_pdf
from .report import render_json, render_text
from .screen import screen_many, screen_one
from .store import DEFAULT_DB, Store

EXIT_CLEAN, EXIT_FLAGGED, EXIT_USAGE = 0, 1, 2
RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}


# --- one-off screening --------------------------------------------------------
def cmd_screen(args) -> int:
    jd = ""
    if args.jd:
        if not os.path.exists(args.jd):
            print("JD file not found: %s" % args.jd, file=sys.stderr)
            return EXIT_USAGE
        with open(args.jd, encoding="utf-8", errors="replace") as fh:
            jd = fh.read()
    elif args.jd_text:
        jd = args.jd_text

    paths = pipeline.find_resumes(args.resumes)
    if not paths:
        print("no .pdf or .docx files matched", file=sys.stderr)
        return EXIT_USAGE

    if len(paths) == 1:
        results, batch = [screen_one(paths[0], jd, args.name or "")], []
    else:
        results, batch = screen_many(paths, jd)

    if args.pdf:
        build_pdf(results, args.pdf, role=args.role or "", jd_excerpt=jd,
                  batch_findings=batch)
        print("PDF report written: %s (%d bytes)"
              % (args.pdf, os.path.getsize(args.pdf)))
    if args.json:
        print(render_json(results, batch))
    elif not args.pdf or args.verbose:
        color = not args.no_color and sys.stdout.isatty()
        print(render_text(results, batch, color=color, verbose=args.verbose))

    if args.fail_on == "never":
        return EXIT_CLEAN
    need = RANK[args.fail_on]
    for r in results:
        if any(f.rank() >= need for f in r.findings):
            return EXIT_FLAGGED
    return EXIT_FLAGGED if any(f.rank() >= need for f in batch) else EXIT_CLEAN


# --- postings -----------------------------------------------------------------
def cmd_job_add(args) -> int:
    if not os.path.exists(args.jd):
        print("JD file not found: %s" % args.jd, file=sys.stderr)
        return EXIT_USAGE
    with open(args.jd, encoding="utf-8", errors="replace") as fh:
        jd = fh.read()
    title = args.title or os.path.splitext(os.path.basename(args.jd))[0]
    with Store(args.db) as st:
        st.add_posting(args.code, title, jd, args.location or "")
    print("posting %s saved: %s" % (args.code, title))
    return EXIT_CLEAN


def cmd_job_list(args) -> int:
    with Store(args.db) as st:
        posts = st.postings()
        if not posts:
            print("no postings yet — add one with:")
            print("  vetta job add --code BA-001 --jd role.txt")
            return EXIT_CLEAN
        print("%-14s %-42s %-8s %s" % ("CODE", "TITLE", "STATUS", "APPLICANTS"))
        for p in posts:
            n = len(st.submissions(posting_code=p["code"]))
            print("%-14s %-42s %-8s %d"
                  % (p["code"][:14], p["title"][:42], p["status"], n))
    return EXIT_CLEAN


def cmd_job_close(args) -> int:
    with Store(args.db) as st:
        if not st.get_posting(args.code):
            print("unknown posting: %s" % args.code, file=sys.stderr)
            return EXIT_USAGE
        st.set_posting_status(args.code, "closed")
    print("posting %s closed" % args.code)
    return EXIT_CLEAN


# --- intake and results -------------------------------------------------------
def cmd_intake(args) -> int:
    with Store(args.db) as st:
        if not st.postings():
            print("no postings yet — add one first", file=sys.stderr)
            return EXIT_USAGE
        if args.job and not st.get_posting(args.job):
            print("unknown posting: %s" % args.job, file=sys.stderr)
            return EXIT_USAGE

        def show(o: pipeline.IntakeOutcome) -> None:
            if o.action in ("screened", "routed"):
                print("  %-38s -> %-12s %3d%%  %-7s %s"
                      % (os.path.basename(o.path)[:38], o.posting_code, o.score,
                         o.verdict, o.label[:24]))
            else:
                print("  %-38s -- %s: %s"
                      % (os.path.basename(o.path)[:38], o.action, o.note))

        print("intake:")
        out = pipeline.intake(st, args.resumes, posting_code=args.job or "",
                              auto=args.auto, force=args.force, progress=show)
        tally: dict[str, int] = {}
        for o in out:
            tally[o.action] = tally.get(o.action, 0) + 1
        print("\n%d file(s): %s" % (len(out), ", ".join(
            "%d %s" % (n, k) for k, n in sorted(tally.items())) or "nothing to do"))
    return EXIT_CLEAN


def cmd_shortlist(args) -> int:
    with Store(args.db) as st:
        codes = [args.job] if args.job else [p["code"] for p in st.postings()]
        if not codes:
            print("no postings yet", file=sys.stderr)
            return EXIT_USAGE
        for code in codes:
            p = st.get_posting(code)
            if not p:
                print("unknown posting: %s" % code, file=sys.stderr)
                return EXIT_USAGE
            rows = pipeline.shortlist(st, code, top=args.top,
                                      include_flagged=args.include_flagged,
                                      min_score=args.min_score)
            print("\n%s — %s" % (p["code"], p["title"]))
            print("-" * 76)
            if not rows:
                print("  nothing to show (failed submissions are hidden unless "
                      "--include-flagged)")
                continue
            print("  %-3s %-5s %-32s %-8s %s"
                  % ("#", "MATCH", "CANDIDATE", "VERDICT", "FILE"))
            for i, r in enumerate(rows, 1):
                who = r["candidate_name"] or r["candidate_email"] or "—"
                print("  %-3d %4d%% %-32s %-8s %s"
                      % (i, r["match_score"], who[:32], r["verdict"],
                         r["filename"][:26]))
    return EXIT_CLEAN


def cmd_flags(args) -> int:
    with Store(args.db) as st:
        rows = st.all_findings(severity=args.severity)
        cross = pipeline.cross_posting_findings(st)
        if not rows and not cross:
            print("no findings recorded")
            return EXIT_CLEAN
        for r in rows:
            print("[%-6s] %-12s %-28s %s"
                  % (r["severity"].upper(), r["posting_code"][:12],
                     r["filename"][:28], r["title"]))
            if r["evidence"]:
                print("           %s" % r["evidence"][:150].replace("\n", " "))
        if cross:
            print("\nacross the pool:")
            for f in cross:
                print("[%-6s] %s" % (f.severity.upper(), f.title))
                print("           %s" % f.evidence[:150])
        worst = max([RANK.get(r["severity"], 0) for r in rows]
                    + [f.rank() for f in cross] + [0])
    return EXIT_FLAGGED if worst >= RANK[args.fail_on] else EXIT_CLEAN


def cmd_report(args) -> int:
    with Store(args.db) as st:
        cross = pipeline.cross_posting_findings(st)
        if args.pdf:
            build_workspace_pdf(st, args.pdf, cross=cross)
            print("PDF report written: %s (%d bytes)"
                  % (args.pdf, os.path.getsize(args.pdf)))
            return EXIT_CLEAN
        if args.json:
            data = {
                "stats": st.stats(),
                "postings": [
                    {"code": p["code"], "title": p["title"], "status": p["status"],
                     "submissions": st.submissions(posting_code=p["code"])}
                    for p in st.postings()],
                "cross_posting_findings": [f.as_dict() for f in cross],
            }
            out = json.dumps(data, indent=2, default=str)
        else:
            out = render_html(st, cross)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print("written: %s (%d bytes)" % (args.out, len(out.encode("utf-8"))))
    else:
        print(out)
    return EXIT_CLEAN


def cmd_stats(args) -> int:
    with Store(args.db) as st:
        for k, v in st.stats().items():
            print("%-16s %s" % (k, v))
    return EXIT_CLEAN


# --- parser -------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vetta",
        description="Score résumés against job descriptions and flag hidden text, "
                    "prompt injection and screening malpractice.")
    ap.add_argument("--db", default=DEFAULT_DB, metavar="FILE",
                    help="workspace database (default: %s)" % DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("screen", help="screen files without saving anything")
    s.add_argument("resumes", nargs="+")
    s.add_argument("--jd", metavar="FILE")
    s.add_argument("--jd-text", metavar="TEXT")
    s.add_argument("--name", metavar="NAME")
    s.add_argument("--json", action="store_true")
    s.add_argument("--pdf", metavar="FILE", help="write a PDF screening report")
    s.add_argument("--role", metavar="TITLE", help="role name to print on the PDF")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--no-color", action="store_true")
    s.add_argument("--fail-on", choices=("high", "medium", "low", "never"),
                   default="high")
    s.set_defaults(func=cmd_screen)

    job = sub.add_parser("job", help="manage postings")
    jsub = job.add_subparsers(dest="jobcmd")
    ja = jsub.add_parser("add", help="add or update a posting")
    ja.add_argument("--code", required=True, help="short identifier, e.g. BA-001")
    ja.add_argument("--jd", required=True, metavar="FILE")
    ja.add_argument("--title")
    ja.add_argument("--location")
    ja.set_defaults(func=cmd_job_add)
    jl = jsub.add_parser("list", help="list postings with applicant counts")
    jl.set_defaults(func=cmd_job_list)
    jc = jsub.add_parser("close", help="mark a posting closed")
    jc.add_argument("--code", required=True)
    jc.set_defaults(func=cmd_job_close)

    i = sub.add_parser("intake", help="screen résumés into the workspace")
    i.add_argument("resumes", nargs="+", help="files, globs or folders")
    i.add_argument("--job", metavar="CODE", help="posting to file these under")
    i.add_argument("--auto", action="store_true",
                   help="route each résumé to its best-fitting open posting")
    i.add_argument("--force", action="store_true", help="re-screen already-seen files")
    i.set_defaults(func=cmd_intake)

    sl = sub.add_parser("shortlist", help="ranked candidates per posting")
    sl.add_argument("--job", metavar="CODE", help="default: every posting")
    sl.add_argument("--top", type=int, default=10)
    sl.add_argument("--min-score", type=int, default=0)
    sl.add_argument("--include-flagged", action="store_true",
                    help="include submissions that failed integrity")
    sl.set_defaults(func=cmd_shortlist)

    fl = sub.add_parser("flags", help="every integrity finding in the workspace")
    fl.add_argument("--severity", choices=("high", "medium", "low", "info"))
    fl.add_argument("--fail-on", choices=("high", "medium", "low", "never"),
                    default="never")
    fl.set_defaults(func=cmd_flags)

    rp = sub.add_parser("report", help="HTML or JSON report across all postings")
    rp.add_argument("--out", metavar="FILE")
    rp.add_argument("--json", action="store_true")
    rp.add_argument("--pdf", metavar="FILE", help="write a PDF report instead of HTML")
    rp.set_defaults(func=cmd_report)

    stt = sub.add_parser("stats", help="workspace counts")
    stt.set_defaults(func=cmd_stats)
    return ap


KNOWN = {"screen", "job", "intake", "shortlist", "flags", "report", "stats"}


def _looks_like_resume_arg(a: str) -> bool:
    """True for a résumé path, glob or folder — not for an option value like --db X."""
    if a.startswith("-"):
        return False
    if any(ch in a for ch in "*?["):
        return True
    if os.path.splitext(a)[1].lower() in pipeline.RESUME_EXT:
        return True
    return os.path.isdir(a)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `vetta cv.pdf --jd role.txt` is shorthand for `vetta screen cv.pdf ...`.
    # Only applies when no subcommand is present, and only in front of an argument
    # that really is a résumé path, so option values are never mistaken for one.
    if not any(a in KNOWN for a in argv):
        for idx, a in enumerate(argv):
            if _looks_like_resume_arg(a):
                argv.insert(idx, "screen")
                break

    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
