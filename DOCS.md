# Vetta — Documentation

Résumé matcher and integrity screener for employers.

Copyright © 2026 Anoop Shekhar. All rights reserved.
Not to be copied, deployed or integrated without written permission —
**thisisanoopshekhar89@gmail.com**. See [LICENSE](LICENSE).

---

## Contents

1. [What problem this solves](#1-what-problem-this-solves)
2. [How it works](#2-how-it-works)
3. [The desktop app](#3-the-desktop-app)
4. [Command reference](#4-command-reference)
5. [The workspace model](#5-the-workspace-model)
6. [Finding codes](#6-finding-codes)
7. [Scoring](#7-scoring)
8. [Python API](#8-python-api)
9. [Scale](#9-scale)
10. [Deployment notes](#10-deployment-notes)
11. [Limits and responsible use](#11-limits-and-responsible-use)

---

## 1. What problem this solves

An employer posts several roles and receives résumés against each. Two things then
need answering per document, and they are different questions:

- **Fit** — how well does this candidate match the job description?
- **Integrity** — is this document honest about itself?

Conventional screeners answer only the first, by extracting all the text and
counting keywords. That is exactly the behaviour a candidate can exploit: text
hidden in white-on-white runs, 1pt type or off-page positions is invisible to the
reviewer but fully visible to the parser, so stuffing raises the score.

Roughly 10% of résumés scanned by large staffing firms now contain hidden text,
and in one 2025 survey of US job seekers, 41% admitted to embedding instructions
aimed at AI screeners. If a screening pipeline hands extracted résumé text to a
language model, that text is untrusted input and can carry instructions.

Vetta answers both questions and keeps them separate.

## 2. How it works

Every document has two readings:

| View | What it contains |
|---|---|
| **Machine text** | Every character in the content stream — what a parser or an LLM ingests |
| **Visible text** | Only what is rendered legibly — what a human reviewer actually reads |

A clean résumé makes these identical. The gap between them is the entire attack
surface, so Vetta computes both and treats the difference as a finding.

**Scoring runs on the visible view only.** Hidden keywords earn nothing; they are
reported under `HIDDEN_KEYWORDS_SCORED_ZERO` instead. This is the design decision
the whole tool rests on.

A glyph counts as not-visible when any of these hold: PDF text render mode 3,
font size below 4pt, near-white fill (all channels ≥ 0.94), luminance contrast
against white below 0.22, or a bounding box outside the page box.

### Modules

| Module | Responsibility |
|---|---|
| `extract.py` | Splits a PDF or DOCX into machine text and visible text |
| `match.py` | Weighted JD-term extraction and scoring |
| `checks.py` | Finding model, injection lexicon, Unicode checks |
| `malpractice.py` | Stuffing, JD mirroring, metadata, fingerprints |
| `quality.py` | Repetition, filler, implausible claims, timeline consistency |
| `identity.py` | Candidate name, email, phone from the visible text |
| `screen.py` | Orchestrates one document end to end |
| `store.py` | SQLite persistence for postings, candidates, submissions, findings |
| `pipeline.py` | Multi-posting intake, routing, shortlists, cross-pool checks |
| `dashboard.py` | Self-contained HTML report |
| `report.py` | Terminal and JSON output |
| `cli.py` | Command line interface |
| `app/` | Flask desktop UI, packaged as a Windows executable |

## 3. The desktop app

For employers who do not want a command line.

```bash
python app/app.py            # development, opens http://127.0.0.1:5099/
python app/build_exe.py      # produces dist/Vetta.exe
```

`Vetta.exe` is a single file and needs no Python installed. Double-click it and
a browser opens on the local UI. Paste the job description, select the résumés you
received, and you get a ranked table with expandable findings per candidate.

Résumés are written to a temporary folder for the duration of the scan and deleted
immediately afterwards. There are no network calls and no telemetry — everything
stays on the machine, which matters because these are other people's personal data.

### Reports

Three output formats, from the same screening pass:

```bash
vetta screen ./applications --jd role.txt --pdf report.pdf --role "Business Analyst"
vetta report --out dashboard.html          # workspace-wide HTML
vetta report --pdf workspace.pdf           # workspace-wide PDF
vetta screen cv.pdf --jd role.txt --json   # machine-readable
```

The **PDF report** is the one to hand a hiring manager. A summary page ranks every
candidate by match with their integrity verdict, then each candidate gets their own
page: performance against the job description term by term (evidenced versus not),
every finding with its severity and evidence, and any hidden text reproduced verbatim
so the reader can judge it themselves. Requirement terms that appeared *only* in hidden
text are listed separately as excluded from the score.

The desktop app builds the same PDF automatically after every batch and offers it as a
download.

## 4. Command reference

### One-off screening

```bash
vetta screen FILE... [--jd FILE | --jd-text TEXT] [options]
vetta FILE... --jd role.txt          # `screen` is implied
```

| Option | Effect |
|---|---|
| `--jd FILE` | Job description file; enables match scoring |
| `--jd-text TEXT` | Job description inline |
| `--name NAME` | Expected candidate name, for the metadata author check |
| `--json` | JSON instead of the terminal report |
| `-v`, `--verbose` | Explanations plus recovered hidden text |
| `--no-color` | Disable ANSI colour |
| `--fail-on LEVEL` | Severity that triggers exit 1: `high` (default), `medium`, `low`, `never` |

### Workspace commands

```bash
vetta job add --code BA-001 --jd role.txt [--title T] [--location L]
vetta job list
vetta job close --code BA-001

vetta intake PATH... [--job CODE] [--auto] [--force]
vetta shortlist [--job CODE] [--top N] [--min-score N] [--include-flagged]
vetta flags [--severity high|medium|low|info] [--fail-on LEVEL]
vetta report [--out FILE] [--json]
vetta stats
```

`--db FILE` selects the workspace (default `vetta.db`) and goes **before** the
subcommand.

`intake` accepts files, glob patterns or folders, and walks folders recursively.
With `--auto` each résumé is routed to whichever open posting its visible text
best fits, which is what you want when applications arrive in one shared inbox.

### Exit codes

`0` clean · `1` findings at or above `--fail-on` · `2` usage error.

So it drops into a pipeline as a gate:

```bash
vetta screen incoming/*.pdf --jd role.txt --fail-on high || notify_recruiter
```

### A full session

```bash
vetta job add --code BA-001 --jd roles/ba.txt --title "Business Analyst"
vetta job add --code PM-002 --jd roles/pm.txt --title "Project Manager"

vetta intake ./inbox --auto          # route everything to the best-fit role
vetta shortlist --job BA-001 --top 10
vetta flags --severity high
vetta report --out report.html
```

## 5. The workspace model

One SQLite file holds everything, so results accumulate instead of being
recomputed.

```
postings   ──1:N──  submissions  ──1:N──  findings
                         │
candidates ──1:N─────────┘
```

| Table | Notes |
|---|---|
| `postings` | Keyed by `code`; `jd_hash` detects a changed JD |
| `candidates` | Unique on (email, name); may be empty when identity is unclear |
| `submissions` | Unique on (posting_id, file_hash) — one row per document per posting |
| `findings` | Replaced wholesale whenever a submission is re-screened |

Screening is deterministic for a given file and JD, so `intake` skips work it has
already done. A submission is re-screened only when the file content changes or
the posting's JD changes. `--force` overrides this.

## 6. Finding codes

### Hidden text
| Code | Severity | Meaning |
|---|---|---|
| `HIDDEN_TEXT_PDF` | high / medium | Glyphs present but not legible |
| `HIDDEN_TEXT_DOCX` | high | Run marked hidden, near-white or sub-4pt |
| `HIDDEN_TEXT_VOLUME` | high / medium | Share of the document that is invisible |
| `HIDDEN_KEYWORD_LIST` | high | Hidden block reads as keywords, not prose |
| `HIDDEN_KEYWORDS_SCORED_ZERO` | high | JD terms found only in hidden text |
| `NO_VISIBLE_TEXT` | high | Text layer with nothing rendered — image overlay |
| `HIDDEN_LAYER` | medium | Optional-content layer off by default |
| `OUT_OF_BAND_TEXT` | low | Comments, footnotes, headers or footers |
| `TRACKED_DELETIONS` | medium | Deleted text retained in the file |

### Injection and Unicode
| Code | Severity | Meaning |
|---|---|---|
| `INJECTION_PHRASE` | high | Instruction-shaped text aimed at an AI reader |
| `BIDI_CONTROLS` | high | Bidirectional overrides — display order can differ |
| `ZERO_WIDTH_CHARS` | medium / low | Invisible characters |
| `PRIVATE_USE_CHARS` | medium | Codepoints with no standard glyph |
| `MIXED_SCRIPT_WORDS` | medium | Cyrillic or Greek lookalikes inside Latin words |

### Malpractice
| Code | Severity | Meaning |
|---|---|---|
| `JD_MIRRORING` | high / medium | Long verbatim runs copied from the JD |
| `KEYWORD_REPETITION` | medium | A term repeats beyond what prose sustains |
| `AUTHOR_MISMATCH` | low | Document author is not the candidate |
| `TIMESTAMP_ANOMALY` | low | Modified before created |
| `TOOLING_IN_METADATA` | info | A CV service or AI tool is named in metadata |
| `DUPLICATE_CONTENT` | medium | Same fingerprint across a batch |

### Claim quality
| Code | Severity | Meaning |
|---|---|---|
| `REPEATED_LINES` | medium / low | The same bullet appears more than once |
| `TEMPLATED_LINES` | low | Several bullets open with identical wording |
| `GENERIC_LANGUAGE` | medium / low | Heavy filler against few specifics |
| `NO_QUANTIFIED_CLAIMS` | low | No figures anywhere in a long document |
| `IMPLAUSIBLE_METRIC` | medium | Percentage improvement above ~300% |
| `UNVERIFIABLE_SUPERLATIVE` | low | Absolute claims with no detail |
| `SKILL_LIST_INFLATION` | low | 35+ items in a flat skills list |
| `EXPERIENCE_CLAIM_MISMATCH` | medium | Stated years exceed the dates shown |
| `FUTURE_DATE` | medium | A date later than today |

### Across the pool
| Code | Severity | Meaning |
|---|---|---|
| `SHARED_CONTENT_DIFFERENT_NAMES` | high | One document under several identities |
| `HIDDEN_TEXT_TARGETS_OTHER_ROLE` | medium | Hidden terms fit a different posting |
| `DUPLICATE_IN_POSTING` | low | Near-identical résumés to one posting |
| `BROAD_APPLICATION` | low | One candidate across four or more postings |

### Verdicts

`clean` no findings · `review` at least one medium · `fail` at least one high ·
`error` unreadable file.

The verdict is independent of the match score. A strong candidate can fail
integrity; collapsing both into a single number is how screeners get gamed.

## 7. Scoring

1. `jd_terms()` extracts weighted requirement terms from the JD. Multi-word
   phrases from a curated list score higher than single words. A large stopword
   list removes filler like *about*, *ensure*, *culture*, *team* — words that
   inflate naive keyword coverage without meaning anything.
2. Weight rises with repetition: `1.0 + log1p(count)` for words, `2.2 + log1p(count)`
   for phrases. A requirement stated three times matters more than one mentioned
   in passing.
3. Coverage is the matched weight over total weight, against **visible text only**.
4. Bands: ≥65% strong · ≥50% good · ≥32% partial · below that weak.

Terms found only in hidden text are collected in `hidden_only` and reported, never
counted.

## 8. Python API

```python
from vetta.screen import screen_one

r = screen_one("cv.pdf", jd_text, candidate_name="Priya Raman")

r.match.score        # 0-100, visible text only
r.match.band         # "strong match" ...
r.match.matched      # JD terms evidenced
r.match.missing      # JD terms absent
r.match.hidden_only  # JD terms present only in hidden text
r.verdict            # clean | review | fail | error
r.findings           # list[Finding]
r.hidden_text        # the recovered payload
r.hidden_ratio       # share of document text that is invisible
r.identity           # {name, email, phone, label}
r.as_dict()          # JSON-ready
```

Workspace use:

```python
from vetta.store import Store
from vetta import pipeline

with Store("vetta.db") as st:
    st.add_posting("BA-001", "Business Analyst", jd_text)
    pipeline.intake(st, ["./inbox"], auto=True)
    top = pipeline.shortlist(st, "BA-001", top=10)
    pool = pipeline.cross_posting_findings(st)
```

Each `Finding` carries `code`, `severity`, `title`, `evidence`, `where`, `detail`
and `meta`, plus `rank()` for threshold comparisons.

### Extending

- **New injection patterns** — add to `INJECTION_PATTERNS` in `checks.py`. Keep
  them narrow and add a false-positive test; a pattern that fires on ordinary CV
  prose is worse than no pattern.
- **New JD phrases** — add to `PHRASES` in `match.py`.
- **New checks** — return `list[Finding]` and call it from `screen_one()`.

## 9. Scale

Measured on one laptop, single-threaded, on **unique** documents (identical files are
content-hashed and skipped, which flatters any benchmark that reuses one file):

| Operation | Cost | At 100 postings x 500 résumés |
|---|---|---|
| Screen one résumé | ~14 ms | ≈ 7 seconds |
| Route a résumé to its best-fit posting | ~19 ms at 100 postings | ≈ 10 seconds |
| Cross-pool checks | 0.13 s per 100 postings x 60 submissions | seconds, not minutes |
| Re-running an unchanged batch | skipped | only new or changed files are screened |

That is roughly **4,000 résumés a minute** per core.

Postings and résumés multiply only during automatic routing, since each résumé is
scored against every open posting. Job-description analysis is memoised, so adding a
posting costs one cheap comparison rather than a fresh analysis — measured, that took
routing from 43 ms to 19 ms per résumé at 100 postings. The cross-pool pass reads
postings once instead of querying per iteration, which took it from 2.42 s to 0.13 s.

Storage is SQLite, so a workspace is one portable file and needs no server. If you
outgrow that, the screening step is stateless and embarrassingly parallel — the work
shards cleanly by file.

### How résumés get in

**Manual, by design, for now.** An employer pastes or points at the job description
and selects the résumés received — in the desktop app, or via `intake` on the command
line. There is no inbox polling, no job-board integration and no hosted endpoint.

An **API and ATS integration are available on request** rather than shipped blind: the
useful shape depends on where your applications actually arrive. Get in touch and it
gets built for that.

## 10. Deployment notes

**Requirements** — Python 3.10+, PyMuPDF. ReportLab only for generating fixtures,
Flask only for the app.

**Tests**

```bash
python tests/test_screen.py       # core: 20 tests
python tests/test_workspace.py    # workspace and reports: 27 tests
python -m pytest -q               # or both under pytest
```

**Wiring into an existing ATS** — run `vetta screen --json` on upload and store
the verdict and findings against the application record. Use exit codes to gate,
never to auto-reject.

**If you put an LLM in the pipeline**, screening the résumé is necessary but not
sufficient. Also separate instructions from data in the prompt, never let extracted
document text occupy the system role, and keep a human on the decision. Vetta
narrows the gap; it does not close it.

## 11. Limits and responsible use

- **Not a lie detector.** It finds manipulation of the *document*, not false claims
  in it. A fabricated job history is invisible here. The `quality` checks flag
  *padding and inconsistency*, which is not the same as dishonesty.
- **Heuristics, not proof.** `AUTHOR_MISMATCH` and `TOOLING_IN_METADATA` are
  informational; shared templates and CV services are ordinary. Weigh findings
  together and read the recovered text before acting.
- **A `review` verdict is not an accusation.** Word processors emit soft hyphens.
  Some templates use white text for layout. The point is to surface the delta so a
  human decides.
- **No OCR.** A pure-image résumé with no text layer gives nothing to compare.
- **English-only injection lexicon**, pattern-based. It catches common forms, not a
  determined adversary avoiding imperative phrasing.
- **Fairness.** Filler-phrase and genericness checks can disadvantage non-native
  speakers and candidates using standard templates, and `AUTHOR_MISMATCH` can
  penalise anyone who used a CV service. Treat these as prompts for a closer read,
  never as scores that filter people out. Keep a human in the loop on every
  rejection, and be prepared to explain any decision to the candidate.
- **Personal data.** Résumés are personal data. The app deletes uploads after
  scanning and makes no network calls, but a workspace database stores extracted
  text and findings — retain it only as long as your obligations allow.

---

Licensing enquiries and permission requests: **thisisanoopshekhar89@gmail.com**
