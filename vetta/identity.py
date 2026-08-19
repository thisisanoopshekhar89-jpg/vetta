"""Pull a candidate's name, email and phone out of résumé text.

Deliberately conservative: a wrong name attached to a malpractice finding is
worse than no name, so anything ambiguous returns empty and the filename is used
as the label instead.
"""

from __future__ import annotations

import os
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:(?<=\s)|^)(\+?\d[\d\s().\-]{7,17}\d)(?=\s|$)")

# Lines that look like a heading rather than a person's name.
NOT_A_NAME = re.compile(
    r"curriculum\s+vitae|resume|résumé|profile|summary|objective|contact|"
    r"experience|education|skills|references|page\s*\d|confidential",
    re.I)
NAME_SHAPE = re.compile(r"^[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-.]+){0,3}$")
ALLCAPS_NAME = re.compile(r"^[A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-.]+){0,3}$")


def _clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return raw.strip() if 9 <= len(digits) <= 15 else ""


def extract_identity(visible_text: str, fallback_path: str = "") -> dict:
    """Return {name, email, phone, label}. Any field may be empty."""
    text = visible_text or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    email = ""
    m = EMAIL.search(text)
    if m:
        email = m.group(0).strip(".,;:")

    phone = ""
    for pm in PHONE.finditer(text[:2500]):
        phone = _clean_phone(pm.group(1))
        if phone:
            break

    # A name is usually one of the first few lines, before any section heading.
    name = ""
    for ln in lines[:6]:
        if NOT_A_NAME.search(ln) or len(ln) > 42:
            continue
        if EMAIL.search(ln) or re.search(r"\d{4,}", ln):
            continue
        cand = ln.strip(" |·-—,")
        if NAME_SHAPE.match(cand):
            name = cand
            break
        if ALLCAPS_NAME.match(cand) and len(cand.split()) <= 4:
            name = cand.title()
            break

    # Fall back to the local part of the email if it looks like a person.
    if not name and email:
        local = re.split(r"[._\-]", email.split("@")[0])
        parts = [p for p in local if p.isalpha() and len(p) > 1]
        if 2 <= len(parts) <= 3:
            name = " ".join(p.capitalize() for p in parts)

    label = name or email or (os.path.basename(fallback_path) if fallback_path else "")
    return {"name": name, "email": email, "phone": phone, "label": label}
