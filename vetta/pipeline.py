# Vetta - proprietary software. Copyright (c) 2026 Anoop Shekhar.
# Public to read, not to use. Copying, modification, deployment or commercial
# use requires written permission: thisisanoopshekhar89@gmail.com
"""Multi-posting orchestration.

An employer runs several openings at once and receives résumés against each. This
layer handles intake (including routing a résumé to whichever posting it best
fits), screening the backlog, ranked shortlists, and the checks that only make
sense across the whole pool rather than one document at a time.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from .checks import HIGH, LOW, MEDIUM, Finding
from .extract import extract
from .identity import extract_identity
from .match import score
from .screen import screen_one
from .store import Store, sha256_file

RESUME_EXT = (".pdf", ".docx", ".docm")


def find_resumes(paths: list[str]) -> list[str]:
    """Expand files, globs and directories into a de-duplicated file list."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for ext in RESUME_EXT:
                out += sorted(glob.glob(os.path.join(p, "**", "*" + ext), recursive=True))
        elif any(ch in p for ch in "*?["):
            out += sorted(glob.glob(p, recursive=True))
        elif os.path.splitext(p)[1].lower() in RESUME_EXT:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(p)
    return uniq


@dataclass
class IntakeOutcome:
    path: str
    posting_code: str = ""
    action: str = ""          # screened | skipped | routed | unroutable | error
    score: int = 0
    verdict: str = ""
    label: str = ""
    note: str = ""


def route(store: Store, path: str, min_score: int = 20, ex=None) -> tuple[str, int]:
    """Pick the open posting whose JD best fits this résumé's visible text.

    Pass `ex` to avoid re-extracting; jd_terms is memoised, so scoring one résumé
    against N postings costs N cheap comparisons rather than N JD analyses.
    """
    posts = store.postings(status="open")
    if not posts:
        return "", 0
    if ex is None:
        try:
            ex = extract(path)
        except Exception:
            return "", 0
    best, best_score = "", -1
    for p in posts:
        s = score(p["jd_text"], ex.visible_text, ex.hidden_text).score
        if s > best_score:
            best, best_score = p["code"], s
    if best_score < min_score:
        return "", best_score
    return best, best_score


def intake(store: Store, paths: list[str], posting_code: str = "",
           auto: bool = False, force: bool = False,
           progress=None) -> list[IntakeOutcome]:
    """Screen résumés into one posting, or route each to its best-fit posting."""
    files = find_resumes(paths)
    results: list[IntakeOutcome] = []

    for path in files:
        # Extract once per file. Routing, screening and identity all read from it.
        ex = None
        try:
            ex = extract(path)
        except Exception as exc:
            results.append(IntakeOutcome(
                path=path, action="error",
                note="%s: %s" % (type(exc).__name__, exc)))
            if progress:
                progress(results[-1])
            continue

        code = posting_code
        routed_score = 0
        if auto or not code:
            code, routed_score = route(store, path, ex=ex)
            if not code:
                results.append(IntakeOutcome(
                    path=path, action="unroutable",
                    note="no open posting scored above the routing threshold "
                         "(best %d%%)" % routed_score))
                if progress:
                    progress(results[-1])
                continue

        posting = store.get_posting(code)
        if not posting:
            results.append(IntakeOutcome(path=path, posting_code=code, action="error",
                                         note="unknown posting %r" % code))
            if progress:
                progress(results[-1])
            continue

        fh = sha256_file(path)
        if not force and not store.needs_screening(posting["id"], fh, posting["jd_hash"]):
            results.append(IntakeOutcome(path=path, posting_code=code, action="skipped",
                                         note="already screened against this JD"))
            if progress:
                progress(results[-1])
            continue

        res = screen_one(path, posting["jd_text"], ex=ex)
        ident = extract_identity(ex.visible_text, path)
        cand_id = store.upsert_candidate(ident["name"], ident["email"], ident["phone"])
        store.save_submission(posting["id"], posting["jd_hash"], path, fh, res, cand_id)

        results.append(IntakeOutcome(
            path=path, posting_code=code,
            action="error" if res.error else ("routed" if auto else "screened"),
            score=res.match.score, verdict=res.verdict, label=ident["label"],
            note=res.error))
        if progress:
            progress(results[-1])
    return results


def shortlist(store: Store, posting_code: str, top: int = 10,
              include_flagged: bool = False, min_score: int = 0) -> list[dict]:
    """Ranked candidates for one posting. Flagged submissions are excluded by default."""
    rows = store.submissions(posting_code=posting_code)
    out = []
    for r in rows:
        if r["error"]:
            continue
        if r["match_score"] < min_score:
            continue
        if not include_flagged and r["verdict"] == "fail":
            continue
        out.append(r)
    out.sort(key=lambda r: (-r["match_score"], r["verdict"] != "clean"))
    return out[:top]


# --- checks that need the whole pool -----------------------------------------
def cross_posting_findings(store: Store) -> list[Finding]:
    """Patterns only visible across postings and candidates."""
    out: list[Finding] = []
    subs = store.submissions()

    # The same document content submitted under different candidate identities.
    by_fp: dict[str, list[dict]] = {}
    for s in subs:
        if s["fingerprint"]:
            by_fp.setdefault(s["fingerprint"], []).append(s)
    for fp, group in by_fp.items():
        names = {(s["candidate_name"] or "").lower() for s in group if s["candidate_name"]}
        files = {s["filename"] for s in group}
        if len(names) > 1:
            out.append(Finding(
                code="SHARED_CONTENT_DIFFERENT_NAMES", severity=HIGH,
                title="Identical résumé content submitted under different names",
                evidence=", ".join(sorted(names)[:6]),
                where="pool",
                detail=("Same content fingerprint across %d submissions. Consistent with "
                        "one document recycled under multiple identities."
                        % len(group)),
                meta={"fingerprint": fp, "files": sorted(files)[:8]}))
        elif len(files) > 1 and len({s["posting_code"] for s in group}) == 1:
            out.append(Finding(
                code="DUPLICATE_IN_POSTING", severity=LOW,
                title="Near-identical résumés submitted to the same posting",
                evidence=", ".join(sorted(files)[:5]), where="pool",
                detail="Often a re-upload; worth a glance rather than an alarm.",
                meta={"fingerprint": fp}))

    # One candidate applying to many postings is normal; flag only heavy volume.
    by_cand: dict[str, set[str]] = {}
    for s in subs:
        key = (s["candidate_email"] or s["candidate_name"] or "").lower()
        if key:
            by_cand.setdefault(key, set()).add(s["posting_code"])
    for who, codes in by_cand.items():
        if len(codes) >= 4:
            out.append(Finding(
                code="BROAD_APPLICATION", severity=LOW,
                title="Candidate applied to %d postings" % len(codes),
                evidence="%s -> %s" % (who, ", ".join(sorted(codes)[:8])),
                where="pool",
                detail=("Not malpractice in itself. Relevant when the same CV is sent "
                        "unchanged to unrelated roles."),
                meta={"postings": sorted(codes)}))

    # Hidden text aimed at a different posting's requirements than the one applied to.
    # Postings and their JD text are read once: this loop is submissions x postings,
    # so a query inside it would dominate everything else at scale.
    all_posts = store.postings()
    jd_by_code = {p["code"]: p["jd_text"] for p in all_posts}
    for s in subs:
        hid = (s["hidden_text"] or "").strip()
        if not hid:
            continue
        applied = s["posting_code"]
        mine = score(jd_by_code.get(applied, ""), "", hid)
        for p in all_posts:
            if p["code"] == applied:
                continue
            other = score(p["jd_text"], "", hid)
            if other.hidden_only and len(other.hidden_only) > len(mine.hidden_only) + 2:
                out.append(Finding(
                    code="HIDDEN_TEXT_TARGETS_OTHER_ROLE", severity=MEDIUM,
                    title="Hidden text matches a different posting's requirements",
                    evidence="%s: hidden terms fit %s better than %s"
                             % (s["filename"], p["code"], applied),
                    where="pool",
                    detail=("Suggests a generic hidden keyword block reused across "
                            "applications rather than anything role-specific."),
                    meta={"applied_to": applied, "better_fit": p["code"],
                          "terms": other.hidden_only[:12]}))
                break
    return out
