"""Vetta desktop app — résumé matcher and integrity screener for employers.

A local Flask UI wrapped as a single Windows executable. Paste a job description,
drop in the résumés you received, and get a ranked shortlist with integrity
findings. Nothing leaves the machine: no network calls, no uploads, no telemetry.

Dev:    python app/app.py
Build:  python app/build_exe.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import webbrowser

from flask import Flask, render_template, request, send_from_directory

# Allow running both from the repo and from inside a PyInstaller bundle.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vetta import pipeline                      # noqa: E402
from vetta.dashboard import render_html         # noqa: E402
from vetta.pdfreport import build_pdf           # noqa: E402
from vetta.screen import screen_many, screen_one  # noqa: E402

FROZEN = getattr(sys, "frozen", False)
APP_DIR = (os.path.dirname(sys.executable) if FROZEN
           else os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(APP_DIR, "reports")
os.makedirs(OUT_DIR, exist_ok=True)

ALLOWED = (".pdf", ".docx", ".docm")
MAX_FILES = 300


def _resource(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


app = Flask(__name__, template_folder=_resource("templates"))
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024      # 256 MB of résumés


def _render(**kw):
    base = dict(results=None, batch=None, jd="", error=None, report=None,
                counts=None, jd_title="", pdf_name=None)
    base.update(kw)
    return render_template("index.html", **base)


@app.route("/", methods=["GET"])
def index():
    return _render()


@app.route("/screen", methods=["POST"])
def do_screen():
    jd = (request.form.get("jd") or "").strip()
    jd_title = (request.form.get("jd_title") or "").strip()
    uploads = [f for f in request.files.getlist("resumes") if f and f.filename]

    if not uploads:
        return _render(jd=jd, jd_title=jd_title,
                       error="Add at least one résumé (PDF or DOCX).")
    if len(uploads) > MAX_FILES:
        return _render(jd=jd, jd_title=jd_title,
                       error="Too many files at once — %d is the limit." % MAX_FILES)

    bad = [f.filename for f in uploads
           if os.path.splitext(f.filename)[1].lower() not in ALLOWED]
    if bad:
        return _render(jd=jd, jd_title=jd_title,
                       error="Unsupported file type: %s. Use PDF or DOCX."
                             % ", ".join(bad[:4]))

    # Résumés are written to a temporary folder and deleted straight after.
    tmp = tempfile.mkdtemp(prefix="vetta_")
    paths = []
    try:
        for f in uploads:
            safe = os.path.basename(f.filename).replace("\\", "_")
            p = os.path.join(tmp, safe)
            f.save(p)
            paths.append(p)

        if len(paths) == 1:
            results, batch = [screen_one(paths[0], jd)], []
        else:
            results, batch = screen_many(paths, jd)

        results.sort(key=lambda r: (-r.match.score, r.verdict != "clean"))
        counts = {"total": len(results),
                  "clean": sum(1 for r in results if r.verdict == "clean"),
                  "review": sum(1 for r in results if r.verdict == "review"),
                  "fail": sum(1 for r in results if r.verdict == "fail"),
                  "error": sum(1 for r in results if r.verdict == "error")}

        # A filed report is the point of this for most employers, so always
        # produce the PDF and offer it rather than making them ask.
        pdf_name = "Vetta_report_%s.pdf" % time.strftime("%Y%m%d_%H%M%S")
        try:
            build_pdf(results, os.path.join(OUT_DIR, pdf_name),
                      role=jd_title, jd_excerpt=jd, batch_findings=batch)
        except Exception as exc:            # a report failure must not lose the results
            print("  PDF report failed: %s: %s" % (type(exc).__name__, exc))
            pdf_name = None

        return _render(results=results, batch=batch, jd=jd, jd_title=jd_title,
                       counts=counts, pdf_name=pdf_name)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


@app.route("/report/<path:name>")
def report(name: str):
    """Serve a generated PDF report. Filenames are generated, never user-supplied."""
    if "/" in name or "\\" in name or ".." in name:
        return "not found", 404
    return send_from_directory(OUT_DIR, name, as_attachment=True)


def main() -> None:
    port = int(os.environ.get("PORT", "5099"))
    url = "http://127.0.0.1:%d/" % port
    if FROZEN:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print("\n  Vetta — vet the applicants")
    print("  running at %s" % url)
    print("  everything stays on this machine. Ctrl+C to stop.\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
