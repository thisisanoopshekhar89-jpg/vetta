"""Tests for the workspace layer: store, quality checks, pipeline, identity.

Run with: python tests/test_workspace.py   (or python -m pytest -q)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(ROOT, "samples")
sys.path.insert(0, ROOT)

from vetta import pipeline, quality              # noqa: E402
from vetta.identity import extract_identity      # noqa: E402
from vetta.store import Store                    # noqa: E402


def _ensure_samples():
    need = ["clean_resume.pdf", "poisoned_resume.pdf", "padded_resume.pdf",
            "job_description.txt"]
    if not all(os.path.exists(os.path.join(SAMPLES, n)) for n in need):
        subprocess.run([sys.executable, os.path.join(SAMPLES, "make_samples.py")],
                       check=True, capture_output=True)


def _jd():
    _ensure_samples()
    with open(os.path.join(SAMPLES, "job_description.txt"), encoding="utf-8") as fh:
        return fh.read()


def _tmp_db():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(p)
    return p


# --- quality: repetition, filler, implausible claims, timeline ----------------
def test_repeated_lines_detected():
    text = "\n".join(["Responsible for supporting business operations daily."] * 3
                     + ["Something entirely different happened here instead."])
    codes = {f.code for f in quality.check_repetition(text)}
    assert "REPEATED_LINES" in codes


def test_repetition_ignores_short_and_unique_lines():
    text = "Ran workshops.\nBuilt reporting.\nWrote procedures.\n"
    assert not quality.check_repetition(text)


def test_generic_language_detected():
    # Repeated to clear the 120-word floor, which keeps short documents unflagged.
    text = (("Results-driven team player and self-starter, hard working and "
             "detail-oriented with a proven track record and excellent "
             "communication skills. Responsible for many things. Familiar with "
             "tools. Passionate about value. ") * 6)
    codes = {f.code for f in quality.check_genericness(text)}
    assert "GENERIC_LANGUAGE" in codes


def test_short_document_is_not_flagged_as_generic():
    """The word floor is deliberate: a two-line note is not a padded résumé."""
    text = "Results-driven team player. Hard working and detail-oriented."
    assert not quality.check_genericness(text)


def test_specific_prose_is_not_flagged_as_generic():
    text = ("Cut claims handling time 22% across 40 staff. Migrated 12000 CRM "
            "records in 6 weeks. Automated 3 reports saving 333 hours a year. "
            "Ran 12 process mapping workshops with underwriting and claims teams "
            "to document 18 standard operating procedures for policy servicing. "
            "Reduced rework by 15% and cut cycle time from 9 days to 4 days.")
    assert not any(f.code == "GENERIC_LANGUAGE"
                   for f in quality.check_genericness(text))


def test_implausible_metric_detected():
    f = quality.check_implausible("Increased operational efficiency by 4200%.")
    assert any(x.code == "IMPLAUSIBLE_METRIC" for x in f)


def test_ordinary_metric_not_flagged():
    f = quality.check_implausible("Improved throughput by 22% and cut cost 15%.")
    assert not any(x.code == "IMPLAUSIBLE_METRIC" for x in f)


def test_experience_claim_mismatch():
    text = "Seasoned professional with 20+ years experience.\nAnalyst, 2018 to 2021."
    f = quality.check_timeline(text, this_year=2026)
    assert any(x.code == "EXPERIENCE_CLAIM_MISMATCH" for x in f)


def test_consistent_experience_claim_passes():
    text = "Analyst with 8 years experience.\nStarted at Acme in 2018."
    f = quality.check_timeline(text, this_year=2026)
    assert not any(x.code == "EXPERIENCE_CLAIM_MISMATCH" for x in f)


def test_future_date_detected():
    f = quality.check_timeline("Consultant, 2031 to present.", this_year=2026)
    assert any(x.code == "FUTURE_DATE" for x in f)


def test_padded_sample_triggers_quality_checks():
    _ensure_samples()
    from vetta.screen import screen_one
    r = screen_one(os.path.join(SAMPLES, "padded_resume.pdf"), _jd())
    codes = {f.code for f in r.findings}
    assert "REPEATED_LINES" in codes
    assert "GENERIC_LANGUAGE" in codes
    assert "IMPLAUSIBLE_METRIC" in codes
    assert "EXPERIENCE_CLAIM_MISMATCH" in codes
    # padding is a judgement call, not fraud
    assert r.verdict == "review"
    assert r.hidden_ratio == 0.0


# --- identity -----------------------------------------------------------------
def test_identity_from_header():
    text = "Priya Raman\npriya.raman@example.com | +44 7700 900123 | Manchester"
    ident = extract_identity(text)
    assert ident["name"] == "Priya Raman"
    assert ident["email"] == "priya.raman@example.com"
    assert ident["phone"]


def test_identity_skips_headings():
    text = "CURRICULUM VITAE\nProfile\nDev Kapoor\ndev@example.com"
    assert extract_identity(text)["name"] == "Dev Kapoor"


def test_identity_falls_back_to_filename():
    ident = extract_identity("", "/tmp/some_cv.pdf")
    assert ident["label"] == "some_cv.pdf"
    assert ident["name"] == ""


# --- store --------------------------------------------------------------------
def test_posting_roundtrip_and_upsert():
    db = _tmp_db()
    try:
        with Store(db) as st:
            st.add_posting("BA-001", "Business Analyst", "need SQL and process mapping")
            st.add_posting("BA-001", "Business Analyst v2", "need SQL, Power BI")
            posts = st.postings()
            assert len(posts) == 1                      # upserted, not duplicated
            assert posts[0]["title"] == "Business Analyst v2"
    finally:
        os.remove(db)


def test_rescreen_only_when_file_or_jd_changes():
    _ensure_samples()
    db = _tmp_db()
    try:
        with Store(db) as st:
            st.add_posting("R1", "Role", _jd())
            out1 = pipeline.intake(st, [os.path.join(SAMPLES, "clean_resume.pdf")],
                                   posting_code="R1")
            assert out1[0].action == "screened"
            out2 = pipeline.intake(st, [os.path.join(SAMPLES, "clean_resume.pdf")],
                                   posting_code="R1")
            assert out2[0].action == "skipped"
            st.add_posting("R1", "Role", _jd() + "\n- Additional: Kubernetes")
            out3 = pipeline.intake(st, [os.path.join(SAMPLES, "clean_resume.pdf")],
                                   posting_code="R1")
            assert out3[0].action == "screened"         # JD changed, so re-screened
            assert len(st.submissions(posting_code="R1")) == 1
    finally:
        os.remove(db)


def test_stats_and_findings_recorded():
    _ensure_samples()
    db = _tmp_db()
    try:
        with Store(db) as st:
            st.add_posting("R1", "Role", _jd())
            pipeline.intake(st, [SAMPLES], posting_code="R1")
            s = st.stats()
            assert s["postings"] == 1
            assert s["submissions"] >= 3
            assert s["fail"] >= 1
            assert st.all_findings(severity="high")
    finally:
        os.remove(db)


# --- pipeline -----------------------------------------------------------------
def test_shortlist_hides_failed_by_default():
    _ensure_samples()
    db = _tmp_db()
    try:
        with Store(db) as st:
            st.add_posting("R1", "Role", _jd())
            pipeline.intake(st, [SAMPLES], posting_code="R1")
            safe = pipeline.shortlist(st, "R1")
            assert all(r["verdict"] != "fail" for r in safe)
            everything = pipeline.shortlist(st, "R1", include_flagged=True)
            assert len(everything) > len(safe)
    finally:
        os.remove(db)


def test_auto_routing_picks_the_better_posting():
    _ensure_samples()
    db = _tmp_db()
    try:
        with Store(db) as st:
            st.add_posting("BA", "Business Analyst", _jd())
            st.add_posting("CHEF", "Head Chef",
                           "Head chef needed. Menu design, kitchen brigade, food "
                           "hygiene, pastry, butchery, stock rotation, allergens.")
            out = pipeline.intake(st, [os.path.join(SAMPLES, "clean_resume.pdf")],
                                  auto=True)
            assert out[0].posting_code == "BA", out[0].posting_code
    finally:
        os.remove(db)


def test_cross_posting_duplicate_names():
    _ensure_samples()
    db = _tmp_db()
    try:
        with Store(db) as st:
            st.add_posting("R1", "Role", _jd())
            pipeline.intake(st, [SAMPLES], posting_code="R1")
            fs = pipeline.cross_posting_findings(st)
            assert isinstance(fs, list)                 # runs clean on a real pool
    finally:
        os.remove(db)


def test_find_resumes_skips_non_resumes():
    _ensure_samples()
    found = pipeline.find_resumes([SAMPLES])
    assert found
    assert all(f.lower().endswith((".pdf", ".docx", ".docm")) for f in found)
    assert not any("job_description" in f for f in found)


# --- CLI ----------------------------------------------------------------------
def test_cli_workspace_flow():
    _ensure_samples()
    db = _tmp_db()
    env = dict(os.environ)
    try:
        def run(*args):
            return subprocess.run([sys.executable, "-m", "vetta.cli", "--db", db,
                                   *args], cwd=ROOT, capture_output=True, text=True,
                                  env=env)

        r = run("job", "add", "--code", "BA-001", "--jd",
                os.path.join(SAMPLES, "job_description.txt"), "--title", "BA")
        assert r.returncode == 0, r.stderr

        r = run("intake", SAMPLES, "--job", "BA-001")
        assert r.returncode == 0, r.stderr
        assert "screened" in r.stdout

        r = run("shortlist", "--job", "BA-001", "--include-flagged")
        assert r.returncode == 0 and "BA-001" in r.stdout

        r = run("stats")
        assert "submissions" in r.stdout

        out = os.path.join(tempfile.gettempdir(), "bf_report_test.html")
        r = run("report", "--out", out)
        assert r.returncode == 0 and os.path.exists(out)
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        assert "Vetta" in html and "BA-001" in html
        os.remove(out)
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_cli_shorthand_not_confused_by_db_option():
    """`--db X` must not be mistaken for a résumé path."""
    _ensure_samples()
    db = _tmp_db()
    try:
        r = subprocess.run([sys.executable, "-m", "vetta.cli", "--db", db,
                            "job", "list"], cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "invalid choice" not in (r.stderr or "")
    finally:
        if os.path.exists(db):
            os.remove(db)


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print("PASS %s" % name)
            except AssertionError as exc:
                failed += 1
                print("FAIL %s: %s" % (name, exc))
            except Exception as exc:
                failed += 1
                print("ERROR %s: %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d passed, %d failed" % (passed, failed))
    raise SystemExit(1 if failed else 0)
