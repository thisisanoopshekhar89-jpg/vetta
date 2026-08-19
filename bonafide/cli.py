"""Command line entry point."""

from __future__ import annotations

import argparse
import glob
import os
import sys

from .report import render_json, render_text
from .screen import screen_many, screen_one

EXIT_CLEAN, EXIT_FLAGGED, EXIT_USAGE = 0, 1, 2


def _expand(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for p in patterns:
        if os.path.isdir(p):
            for ext in ("*.pdf", "*.docx"):
                out += sorted(glob.glob(os.path.join(p, "**", ext), recursive=True))
        elif any(ch in p for ch in "*?["):
            out += sorted(glob.glob(p, recursive=True))
        else:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(p)
    return uniq


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bonafide",
        description="Score résumés against a job description and flag hidden text, "
                    "prompt injection and screening malpractice.")
    ap.add_argument("resumes", nargs="+",
                    help="PDF/DOCX files, glob patterns, or a directory to walk")
    ap.add_argument("--jd", metavar="FILE",
                    help="job description as a text file; enables match scoring")
    ap.add_argument("--jd-text", metavar="TEXT", help="job description inline")
    ap.add_argument("--name", metavar="NAME",
                    help="expected candidate name, for the metadata author check")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="include explanations and recovered hidden text")
    ap.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    ap.add_argument("--fail-on", choices=("high", "medium", "low", "never"),
                    default="high",
                    help="minimum severity that sets a non-zero exit code (default: high)")
    args = ap.parse_args(argv)

    jd = ""
    if args.jd:
        if not os.path.exists(args.jd):
            print("JD file not found: %s" % args.jd, file=sys.stderr)
            return EXIT_USAGE
        with open(args.jd, encoding="utf-8", errors="replace") as fh:
            jd = fh.read()
    elif args.jd_text:
        jd = args.jd_text

    paths = _expand(args.resumes)
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        print("not found: %s" % ", ".join(missing[:5]), file=sys.stderr)
        return EXIT_USAGE
    if not paths:
        print("no .pdf or .docx files matched", file=sys.stderr)
        return EXIT_USAGE

    if len(paths) == 1:
        results = [screen_one(paths[0], jd, args.name or "")]
        batch = []
    else:
        results, batch = screen_many(paths, jd)

    if args.json:
        print(render_json(results, batch))
    else:
        use_color = not args.no_color and sys.stdout.isatty()
        print(render_text(results, batch, color=use_color, verbose=args.verbose))

    if args.fail_on == "never":
        return EXIT_CLEAN
    threshold = {"high": 3, "medium": 2, "low": 1}[args.fail_on]
    for r in results:
        for f in r.findings:
            if f.rank() >= threshold:
                return EXIT_FLAGGED
    for f in batch:
        if f.rank() >= threshold:
            return EXIT_FLAGGED
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
