/* Vetta - self-test for the in-browser scanner.
 * Proprietary. Copyright (c) 2026 Anoop Shekhar. All rights reserved.
 *
 * The browser parser is a second implementation of hidden-text detection, so it can
 * drift from the Python engine. This pins it to the same fixtures.
 *   node docs/selftest.mjs        (from the repository root)
 */
import fs from 'fs';

const src = fs.readFileSync(new URL('./docscan.js', import.meta.url), 'utf8');
(0, eval)(src.split("if (typeof module !== 'undefined')")[0]);
const S = globalThis.VettaDocScan;

const bytes = p => new Uint8Array(fs.readFileSync(p));

/* Expected ratios come from the Python engine (vetta.extract.extract). The browser
   parser normalises whitespace differently, so ratios are compared with tolerance. */
const CASES = [
  {file: 'samples/clean_resume.pdf',        ratio: 0.000, wants: []},
  {file: 'samples/padded_resume.pdf',       ratio: 0.000, wants: []},
  {file: 'samples/poisoned_resume.pdf',     ratio: 0.336,
   wants: ['near-white', 'render mode', 'font size', 'outside the page']},
  {file: 'samples/dark_on_dark_resume.pdf', ratio: 0.218,
   wants: ['dark text on a dark background']},
  {file: 'samples/poisoned_resume.docx',    ratio: 0.464,
   wants: ['w:vanish', 'near-white font colour', 'font size']},
];

let failed = 0;
for (const c of CASES) {
  const b = bytes(c.file);
  const r = c.file.endsWith('.docx') ? await S.scanDOCX(b) : await S.scanPDF(b);
  const errs = [];

  if (!r.spans) errs.push('no text runs read at all');
  if (Math.abs(r.hiddenRatio - c.ratio) > 0.03)
    errs.push('hidden ratio ' + r.hiddenRatio.toFixed(3) + ', expected ~' + c.ratio.toFixed(3));

  const blob = r.reasons.join(' | ');
  for (const w of c.wants)
    if (!blob.includes(w)) errs.push('missing reason containing "' + w + '"');
  if (!c.wants.length && r.hidden) errs.push('flagged hidden text in a clean document');
  if (c.wants.length && !/ignore previous instructions/i.test(r.hidden))
    errs.push('did not recover the injected payload');
  // the visible reading must survive: hiding one block must not blank the document
  if (c.wants.length && !/[A-Za-z]{4,}/.test(r.visible))
    errs.push('no visible text left - the background test is over-firing');

  console.log((errs.length ? 'FAIL  ' : 'ok    ') + c.file +
              '  hidden=' + (r.hiddenRatio * 100).toFixed(1) + '%  runs=' + r.spans);
  errs.forEach(e => console.log('        - ' + e));
  failed += errs.length ? 1 : 0;
}

console.log(failed ? '\n' + failed + ' case(s) failed' : '\nall ' + CASES.length + ' cases passed');
process.exit(failed ? 1 : 0);
