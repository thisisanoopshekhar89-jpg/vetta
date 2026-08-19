# Vetta - proprietary software. Copyright (c) 2026 Anoop Shekhar.
# Public to read, not to use. Copying, modification, deployment or commercial
# use requires written permission: thisisanoopshekhar89@gmail.com
"""Extract two views of a document: what a machine ingests, and what a human sees.

The gap between those two is the whole point of this tool. A parser or an LLM
reads every character in the content stream. A human reads only what is actually
rendered legibly. Anything in the first set but not the second is, by definition,
hidden.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from .checks import HIGH, LOW, MEDIUM, Finding

# A glyph is treated as invisible/illegible if any of these hold.
INVISIBLE_RENDER_MODE = 3
MIN_LEGIBLE_PT = 4.0
NEAR_WHITE = 0.94          # per-channel
MIN_CONTRAST = 0.22        # luminance delta from the background below which text
                           # is effectively unreadable, whichever way round it is


@dataclass
class Extraction:
    """Both views of one document, plus anything found while splitting them."""

    path: str
    kind: str                                   # "pdf" | "docx"
    machine_text: str = ""                      # everything a parser can read
    visible_text: str = ""                      # what a human would actually see
    hidden_segments: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    pages: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def hidden_text(self) -> str:
        return "\n".join(self.hidden_segments)

    @property
    def hidden_ratio(self) -> float:
        total = len(re.sub(r"\s", "", self.machine_text)) or 1
        hid = len(re.sub(r"\s", "", self.hidden_text))
        return hid / total


def _lum(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _page_fills(page) -> list:
    """Filled shapes on the page, in draw order, as (rect, rgb).

    Needed because "is this text legible" depends on what is behind it. Assuming
    white paper misses black text drawn on a black box - a real hiding technique.
    """
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return out
    for d in drawings:
        fill = d.get("fill")
        rect = d.get("rect")
        if fill is None or rect is None:
            continue
        try:
            out.append((fitz.Rect(rect), _norm_color(tuple(fill))))
        except Exception:
            continue
    return out


def _background_behind(bbox, fills: list) -> tuple[float, float, float]:
    """Colour behind a text span: the last drawn fill that covers it, else white."""
    bg = (1.0, 1.0, 1.0)
    try:
        span = fitz.Rect(bbox)
    except Exception:
        return bg
    area = abs(span.get_area()) or 1.0
    for rect, col in fills:                       # draw order, so later wins
        try:
            overlap = abs((rect & span).get_area())
        except Exception:
            continue
        if overlap >= 0.6 * area:
            bg = col
    return bg


def _norm_color(color) -> tuple[float, float, float]:
    """get_texttrace reports colour as an int or an RGB tuple depending on build."""
    if color is None:
        return (0.0, 0.0, 0.0)
    if isinstance(color, (list, tuple)):
        vals = [c / 255.0 if c > 1 else float(c) for c in color[:3]]
        while len(vals) < 3:
            vals.append(vals[0] if vals else 0.0)
        return tuple(vals[:3])  # type: ignore[return-value]
    try:
        v = int(color)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)
    return (((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0)


def _span_text(span) -> str:
    """Rebuild a span's text from its glyph list."""
    out = []
    for ch in span.get("chars", ()):
        try:
            cp = ch[0]
            if isinstance(cp, int) and cp > 0:
                out.append(chr(cp))
        except (IndexError, TypeError, ValueError):
            continue
    return "".join(out)


def extract_pdf(path: str) -> Extraction:
    doc = fitz.open(path)
    ex = Extraction(path=path, kind="pdf", pages=doc.page_count,
                    metadata=dict(doc.metadata or {}))

    machine_parts, visible_parts = [], []
    for pno in range(doc.page_count):
        page = doc[pno]
        rect = page.rect
        machine_parts.append(page.get_text())

        fills = _page_fills(page)
        for span in page.get_texttrace():
            text = _span_text(span)
            if not text.strip():
                continue
            mode = span.get("type", 0)
            size = float(span.get("size", 0) or 0)
            rgb = _norm_color(span.get("color"))
            bbox = span.get("bbox", (0, 0, 0, 0))
            where = "page %d" % (pno + 1)

            reasons = []
            if mode == INVISIBLE_RENDER_MODE:
                reasons.append("invisible render mode (Tr 3)")
            if 0 < size < MIN_LEGIBLE_PT:
                reasons.append("font size %.1fpt" % size)
            bg = _background_behind(bbox, fills)
            contrast = abs(_lum(rgb) - _lum(bg))
            if contrast < MIN_CONTRAST:
                if all(c >= NEAR_WHITE for c in rgb) and all(c >= NEAR_WHITE for c in bg):
                    reasons.append("near-white text on near-white background "
                                   "rgb(%.2f, %.2f, %.2f)" % rgb)
                elif _lum(bg) < 0.2:
                    reasons.append("dark text on a dark background "
                                   "(text %.2f, background %.2f luminance)"
                                   % (_lum(rgb), _lum(bg)))
                else:
                    reasons.append("contrast %.2f against its background is below the "
                                   "legibility threshold" % contrast)
            try:
                if (bbox[3] < rect.y0 - 2 or bbox[1] > rect.y1 + 2
                        or bbox[2] < rect.x0 - 2 or bbox[0] > rect.x1 + 2):
                    reasons.append("drawn outside the page box")
            except (IndexError, TypeError):
                pass

            if reasons:
                ex.hidden_segments.append(text)
                ex.findings.append(Finding(
                    code="HIDDEN_TEXT_PDF",
                    severity=HIGH if (mode == INVISIBLE_RENDER_MODE
                                      or all(c >= NEAR_WHITE for c in rgb)) else MEDIUM,
                    title="Text present in the file but not legible on the page",
                    evidence=text.strip()[:200],
                    where=where,
                    detail="; ".join(reasons),
                    meta={"render_mode": mode, "size": size, "rgb": list(rgb)},
                ))
            else:
                visible_parts.append(text)

        # Optional-content layers can be switched off for display yet still extract.
        try:
            for ocg in (doc.get_ocgs() or {}).values():
                if ocg.get("on") is False:
                    ex.findings.append(Finding(
                        code="HIDDEN_LAYER", severity=MEDIUM,
                        title="Optional-content layer is off by default",
                        evidence=str(ocg.get("name", ""))[:120],
                        where="document",
                        detail=("Content on a disabled layer does not display but is "
                                "still extractable."),
                    ))
        except Exception:
            pass

    ex.machine_text = "\n".join(machine_parts)
    ex.visible_text = "\n".join(visible_parts)

    # A text layer far larger than what is drawn suggests an image-over-text overlay.
    if ex.pages and len(re.sub(r"\s", "", ex.visible_text)) == 0 \
            and len(re.sub(r"\s", "", ex.machine_text)) > 200:
        ex.findings.append(Finding(
            code="NO_VISIBLE_TEXT", severity=HIGH,
            title="Document has an extractable text layer but nothing legible",
            evidence="%d extractable characters, none rendered legibly"
                     % len(ex.machine_text),
            where="document",
            detail=("Typical of a scanned or image-covered page with a hidden text "
                    "layer underneath."),
        ))
    doc.close()
    return ex


# --- DOCX ---------------------------------------------------------------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(path: str) -> Extraction:
    """Read the XML directly: python-docx skips the parts that matter here."""
    ex = Extraction(path=path, kind="docx")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def read(n):
            return z.read(n).decode("utf-8", "replace") if n in names else ""

        body = read("word/document.xml")
        ex.metadata = {
            "core": read("docProps/core.xml")[:4000],
            "app": read("docProps/app.xml")[:2000],
            "custom": read("docProps/custom.xml")[:2000],
        }

        # Runs whose properties make them invisible.
        for m in re.finditer(r"<w:r(?:\s[^>]*)?>(.*?)</w:r>", body, re.S):
            run = m.group(1)
            text = "".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", run, re.S))
            text = re.sub(r"<[^>]+>", "", text)
            if not text.strip():
                continue
            reasons = []
            if re.search(r"<w:vanish\s*/?>|<w:vanish\s+w:val=\"(?:true|1|on)\"", run):
                reasons.append("marked hidden (w:vanish)")
            cm = re.search(r'<w:color\s+w:val="([0-9A-Fa-f]{6})"', run)
            if cm:
                v = cm.group(1).upper()
                if all(int(v[i:i + 2], 16) >= 240 for i in (0, 2, 4)):
                    reasons.append("near-white font colour #%s" % v)
            sm = re.search(r'<w:sz\s+w:val="(\d+)"', run)
            if sm and int(sm.group(1)) < 2 * MIN_LEGIBLE_PT * 2:   # half-points
                reasons.append("font size %.1fpt" % (int(sm.group(1)) / 2.0))
            if re.search(r"<w:webHidden\s*/?>", run):
                reasons.append("hidden in web view")

            if reasons:
                ex.hidden_segments.append(text)
                ex.findings.append(Finding(
                    code="HIDDEN_TEXT_DOCX", severity=HIGH,
                    title="Run is present in the file but not visible when read",
                    evidence=text.strip()[:200], where="word/document.xml",
                    detail="; ".join(reasons),
                ))
            else:
                ex.visible_text += text + "\n"

        # Parts a reader will not see but an extractor will happily ingest.
        for part, label, sev in (
            ("word/comments.xml", "comment", LOW),
            ("word/footnotes.xml", "footnote", LOW),
            ("word/endnotes.xml", "endnote", LOW),
            ("word/header1.xml", "header", LOW),
            ("word/footer1.xml", "footer", LOW),
        ):
            raw = read(part)
            if not raw:
                continue
            txt = re.sub(r"<[^>]+>", " ", raw)
            txt = re.sub(r"\s+", " ", txt).strip()
            if len(txt) > 20:
                ex.hidden_segments.append(txt)
                ex.findings.append(Finding(
                    code="OUT_OF_BAND_TEXT", severity=sev,
                    title="Text in a %s, outside the main body" % label,
                    evidence=txt[:200], where=part,
                    detail=("Extractors usually concatenate these parts into the "
                            "document text even though they sit outside the flow."),
                ))

        # Deleted-but-retained tracked changes.
        dels = re.findall(r"<w:delText(?:\s[^>]*)?>(.*?)</w:delText>", body, re.S)
        if dels:
            joined = re.sub(r"\s+", " ", " ".join(dels))[:300]
            ex.hidden_segments.append(joined)
            ex.findings.append(Finding(
                code="TRACKED_DELETIONS", severity=MEDIUM,
                title="Deleted text retained as a tracked change",
                evidence=joined[:200], where="word/document.xml",
                detail="Shows as deleted to a reader, still extractable as text.",
            ))

        all_text = re.sub(r"<[^>]+>", " ", body)
        ex.machine_text = re.sub(r"[ \t]+", " ", all_text)
        for part in ("word/comments.xml", "word/footnotes.xml", "word/endnotes.xml"):
            raw = read(part)
            if raw:
                ex.machine_text += " " + re.sub(r"<[^>]+>", " ", raw)
    return ex


def extract(path: str) -> Extraction:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_pdf(path)
    if ext in (".docx", ".docm"):
        return extract_docx(path)
    raise ValueError("unsupported file type: %s (expected .pdf or .docx)" % ext)
