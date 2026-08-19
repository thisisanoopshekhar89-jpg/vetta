/* Vetta - in-browser document scanner.
 * Proprietary. Copyright (c) 2026 Anoop Shekhar. All rights reserved.
 * Public to read, not to use. Written permission required:
 * thisisanoopshekhar89@gmail.com
 *
 * Reads a PDF or DOCX in the browser and separates what renders legibly from what
 * only a parser sees. No library and no upload: FlateDecode streams and ZIP entries
 * are inflated with the platform's own DecompressionStream.
 *
 * This is deliberately a subset of the Python engine. It reads the text operators
 * and graphics state - which is all hidden-text detection actually needs - not a
 * full layout engine. Fonts with custom or CID encodings may decode imperfectly;
 * the desktop app uses a real PDF engine and does not have that limit.
 */
(function (root) {
  'use strict';

  const MIN_PT = 4.0;          // below this, nobody is reading it
  const NEAR_WHITE = 0.94;
  const MIN_CONTRAST = 0.22;

  async function inflate(bytes, raw) {
    const fmt = raw ? 'deflate-raw' : 'deflate';
    const ds = new DecompressionStream(fmt);
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  const latin1 = bytes => {
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return s;
  };

  function indexOfSeq(hay, needle, from) {
    outer: for (let i = from; i <= hay.length - needle.length; i++) {
      for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
      return i;
    }
    return -1;
  }
  const bytesOf = s => Uint8Array.from(s, c => c.charCodeAt(0));

  /* ---------------- PDF ---------------- */

  /* ASCII85, as ReportLab emits it: optional <~ lead-in, ~> terminator, z shortcut. */
  function a85(bytes) {
    const out = [];
    let tuple = [], i = 0;
    let s = latin1(bytes);
    if (s.startsWith('<~')) s = s.slice(2);
    const end = s.indexOf('~>');
    if (end >= 0) s = s.slice(0, end);
    for (i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === ' ' || c === '\n' || c === '\r' || c === '\t' || c === '\0') continue;
      if (c === 'z' && tuple.length === 0) { out.push(0, 0, 0, 0); continue; }
      const v = c.charCodeAt(0) - 33;
      if (v < 0 || v > 84) continue;
      tuple.push(v);
      if (tuple.length === 5) {
        let n = 0;
        for (const t of tuple) n = n * 85 + t;
        out.push((n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255);
        tuple = [];
      }
    }
    if (tuple.length > 1) {                 // partial group: pad with 'u'
      const n0 = tuple.length;
      while (tuple.length < 5) tuple.push(84);
      let n = 0;
      for (const t of tuple) n = n * 85 + t;
      const b = [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
      for (let k = 0; k < n0 - 1; k++) out.push(b[k]);
    }
    return Uint8Array.from(out);
  }

  function ahx(bytes) {
    const h = latin1(bytes).replace(/>[\s\S]*$/, '').replace(/[^0-9a-fA-F]/g, '');
    const out = new Uint8Array(h.length >> 1);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(i * 2, 2), 16);
    return out;
  }

  /* Decode a stream through its whole /Filter chain, in order. */
  async function decodeStream(raw, dict) {
    const fm = dict.match(/\/Filter\s*(\[[^\]]*\]|\/[A-Za-z0-9]+)/);
    let filters = [];
    if (fm) filters = (fm[1].match(/\/[A-Za-z0-9]+/g) || []).map(f => f.slice(1));
    let data = raw;
    for (const f of filters) {
      if (f === 'ASCII85Decode' || f === 'A85') data = a85(data);
      else if (f === 'ASCIIHexDecode' || f === 'AHx') data = ahx(data);
      else if (f === 'FlateDecode' || f === 'Fl') {
        try { data = await inflate(data, false); }
        catch (e) { data = await inflate(data, true); }
      } else if (f === 'DCTDecode' || f === 'JPXDecode' || f === 'CCITTFaxDecode' ||
                 f === 'JBIG2Decode' || f === 'LZWDecode' || f === 'RunLengthDecode') {
        return null;                        // image or unsupported codec - not text
      }
    }
    return data;
  }

  async function pdfStreams(bytes) {
    const S = bytesOf('stream'), E = bytesOf('endstream');
    const out = [];
    let i = 0;
    while (true) {
      let s = indexOfSeq(bytes, S, i);
      if (s < 0) break;
      // skip the "stream" inside a preceding "endstream"
      if (s >= 3 && latin1(bytes.subarray(s - 3, s)) === 'end') { i = s + 6; continue; }
      // bound the dictionary at this object's own "obj" marker - a fixed lookback
      // swallows the previous object's keys (/ProcSet /ImageB looks like an image)
      const win = latin1(bytes.subarray(Math.max(0, s - 3000), s));
      const objAt = win.lastIndexOf(' obj');
      const dict = objAt >= 0 ? win.slice(objAt + 4) : win;
      let a = s + 6;
      while (a < bytes.length && (bytes[a] === 13 || bytes[a] === 10 || bytes[a] === 32)) a++;
      // trust /Length when it is there; binary Flate data can contain "endstream"
      const lm = dict.match(/\/Length\s+(\d+)(?![\s\d]*R)/);
      let b;
      if (lm) b = Math.min(bytes.length, a + parseInt(lm[1], 10));
      else {
        b = indexOfSeq(bytes, E, a);
        if (b < 0) break;
        while (b > a && (bytes[b - 1] === 13 || bytes[b - 1] === 10)) b--;
      }
      if (/\/FontFile|\/Subtype\s*\/Image/.test(dict)) { i = b + 6; continue; }
      try {
        const dec = await decodeStream(bytes.subarray(a, b), dict);
        if (dec) out.push(latin1(dec));
      } catch (err) { /* undecodable stream - skip it */ }
      i = b + 6;
    }
    return out;
  }

  function mediaBox(bytes) {
    const head = latin1(bytes.subarray(0, Math.min(bytes.length, 400000)));
    const m = head.match(/\/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)/);
    return m ? {w: parseFloat(m[3]), h: parseFloat(m[4])} : {w: 595, h: 842};
  }

  // PDF string literal -> text
  function pdfLiteral(s) {
    let out = '';
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (c === '\\') {
        const n = s[++i];
        if (n === 'n') out += '\n';
        else if (n === 'r') out += '';
        else if (n === 't') out += '\t';
        else if (n >= '0' && n <= '7') {
          let oct = n;
          while (oct.length < 3 && s[i + 1] >= '0' && s[i + 1] <= '7') oct += s[++i];
          out += String.fromCharCode(parseInt(oct, 8));
        } else out += n === undefined ? '' : n;
      } else out += c;
    }
    return out;
  }
  const pdfHex = s => {
    const h = s.replace(/[^0-9a-fA-F]/g, '');
    let out = '';
    for (let i = 0; i + 1 < h.length; i += 2) {
      const code = parseInt(h.substr(i, 2), 16);
      if (code >= 32 || code === 10) out += String.fromCharCode(code);
    }
    return out;
  };

  const lum = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];

  /* Walk one content stream, tracking the graphics state that decides legibility.
   * Backgrounds are resolved per position, not per page: a dark band behind one
   * paragraph must not make the rest of the document look hidden. */
  function scanContent(content, page) {
    const spans = [];
    const tok = content.match(
      /\((?:\.|[^\()])*\)|<[0-9a-fA-F\s]*>|\[[^\]]*\]|\/[^\s\/\[\]<>()]+|[-+]?[\d.]+|[A-Za-z'"*]+/g) || [];

    let fill = [0, 0, 0], size = 12, mode = 0;
    let tx = 0, ty = page.h / 2;            // text position, from Tm / Td / TD only
    let ox = 0, oy = 0;                     // current translate, from cm
    const fills = [];                       // {x0,y0,x1,y1,rgb} in draw order
    const pending = [];
    const nums = [];

    const bgAt = (x, y) => {
      for (let i = fills.length - 1; i >= 0; i--) {
        const r = fills[i];
        if (x >= r.x0 - 1 && x <= r.x1 + 1 && y >= r.y0 - 1 && y <= r.y1 + 1) return r.rgb;
      }
      return [1, 1, 1];                     // paper
    };

    const push = txt => {
      if (!txt || !txt.trim()) return;
      const reasons = [];
      if (mode === 3) reasons.push('invisible render mode (Tr 3)');
      if (size > 0 && size < MIN_PT) reasons.push('font size ' + size.toFixed(1) + 'pt');
      const bg = bgAt(tx, ty);
      const contrast = Math.abs(lum(fill) - lum(bg));
      if (contrast < MIN_CONTRAST) {
        if (fill.every(c => c >= NEAR_WHITE) && bg.every(c => c >= NEAR_WHITE))
          reasons.push('near-white text on near-white background');
        else if (lum(bg) < 0.25) reasons.push('dark text on a dark background');
        else reasons.push('contrast ' + contrast.toFixed(2) + ' below the legibility threshold');
      }
      if (ty < -2 || ty > page.h + 2 || tx < -2 || tx > page.w + 2)
        reasons.push('drawn outside the page box');
      spans.push({text: txt, hidden: reasons.length > 0, reasons: reasons,
                  size: size, mode: mode, fill: fill.slice()});
    };

    for (let i = 0; i < tok.length; i++) {
      const t = tok[i];
      if (/^[-+]?[\d.]+$/.test(t)) { nums.push(parseFloat(t)); continue; }

      if (t[0] === '(') { push(pdfLiteral(t.slice(1, -1))); nums.length = 0; continue; }
      if (t[0] === '<' && t !== '<<') { push(pdfHex(t)); nums.length = 0; continue; }
      if (t[0] === '[') {                       // TJ array
        const parts = t.match(/\((?:\.|[^\()])*\)|<[0-9a-fA-F\s]*>/g) || [];
        push(parts.map(p => p[0] === '(' ? pdfLiteral(p.slice(1, -1)) : pdfHex(p)).join(''));
        nums.length = 0;
        continue;
      }
      if (t[0] === '/') { nums.length = 0; continue; }

      switch (t) {
        case 'g': case 'G':
          if (nums.length >= 1) { const v = nums[nums.length - 1]; fill = [v, v, v]; }
          break;
        case 'rg': case 'RG': if (nums.length >= 3) fill = nums.slice(-3); break;
        case 'k': case 'K': if (nums.length >= 4) {          // CMYK -> RGB
          const c = nums[nums.length - 4], m = nums[nums.length - 3],
                yy = nums[nums.length - 2], kk = nums[nums.length - 1];
          fill = [(1 - c) * (1 - kk), (1 - m) * (1 - kk), (1 - yy) * (1 - kk)];
        } break;
        case 'sc': case 'scn':
          if (nums.length >= 3) fill = nums.slice(-3);
          else if (nums.length === 1) fill = [nums[0], nums[0], nums[0]];
          break;
        case 'Tf': if (nums.length >= 1) size = Math.abs(nums[nums.length - 1]); break;
        case 'Tr': if (nums.length >= 1) mode = nums[nums.length - 1]; break;
        case 'Td': case 'TD':
          if (nums.length >= 2) { tx += nums[nums.length - 2]; ty += nums[nums.length - 1]; }
          break;
        case 'T*': break;
        case 'Tm':
          if (nums.length >= 6) { tx = nums[nums.length - 2] + ox; ty = nums[nums.length - 1] + oy; }
          break;
        case 'cm':
          if (nums.length >= 6) { ox = nums[nums.length - 2]; oy = nums[nums.length - 1]; }
          break;
        case 're':
          if (nums.length >= 4) {
            const x = nums[nums.length - 4] + ox, y = nums[nums.length - 3] + oy;
            const w = nums[nums.length - 2], h = nums[nums.length - 1];
            pending.push({x0: Math.min(x, x + w), y0: Math.min(y, y + h),
                          x1: Math.max(x, x + w), y1: Math.max(y, y + h)});
          }
          break;
        case 'f': case 'F': case 'f*': case 'b': case 'b*': case 'B': case 'B*':
          for (const r of pending) { r.rgb = fill.slice(); fills.push(r); }
          pending.length = 0;
          break;
        case 'n': case 'S': case 's': pending.length = 0; break;   // clip or stroke, not a fill
        case 'BT': tx = 0; ty = page.h / 2; break;
        default: break;
      }
      nums.length = 0;
    }
    return spans;
  }

  async function scanPDF(bytes) {
    const page = mediaBox(bytes);
    const streams = await pdfStreams(bytes);
    let spans = [];
    for (const s of streams) {
      if (!/BT|Tj|TJ/.test(s)) continue;         // not a content stream
      spans = spans.concat(scanContent(s, page));
    }
    return finish(spans, 'pdf');
  }

  /* ---------------- DOCX ---------------- */

  async function docxPart(bytes, wanted) {
    const SIG = bytesOf('PK\x03\x04');
    let i = 0;
    while (true) {
      const p = indexOfSeq(bytes, SIG, i);
      if (p < 0) return null;
      const dv = new DataView(bytes.buffer, bytes.byteOffset);
      const method = dv.getUint16(p + 8, true);
      const compSize = dv.getUint32(p + 18, true);
      const nameLen = dv.getUint16(p + 26, true);
      const extraLen = dv.getUint16(p + 28, true);
      const name = latin1(bytes.subarray(p + 30, p + 30 + nameLen));
      const dataAt = p + 30 + nameLen + extraLen;
      if (name === wanted && compSize > 0) {
        const raw = bytes.subarray(dataAt, dataAt + compSize);
        if (method === 0) return latin1(raw);
        try { return new TextDecoder().decode(await inflate(raw, true)); }
        catch (e) { return null; }
      }
      i = dataAt + (compSize || 1);
    }
  }

  async function scanDOCX(bytes) {
    const xml = await docxPart(bytes, 'word/document.xml');
    if (!xml) return finish([], 'docx');
    const spans = [];
    const runs = xml.match(/<w:r(?:\s[^>]*)?>[\s\S]*?<\/w:r>/g) || [];
    for (const run of runs) {
      const text = (run.match(/<w:t(?:\s[^>]*)?>([\s\S]*?)<\/w:t>/g) || [])
        .map(t => t.replace(/<[^>]+>/g, '')).join('');
      if (!text.trim()) continue;
      const reasons = [];
      if (/<w:vanish\s*\/?>/.test(run)) reasons.push('marked hidden (w:vanish)');
      const col = run.match(/<w:color\s+w:val="([0-9A-Fa-f]{6})"/);
      if (col) {
        const v = col[1].toUpperCase();
        const ch = [0, 2, 4].map(k => parseInt(v.substr(k, 2), 16) / 255);
        if (ch.every(c => c >= NEAR_WHITE)) reasons.push('near-white font colour #' + v);
      }
      const sz = run.match(/<w:sz\s+w:val="(\d+)"/);
      if (sz && parseInt(sz[1], 10) / 2 < MIN_PT)
        reasons.push('font size ' + (parseInt(sz[1], 10) / 2).toFixed(1) + 'pt');
      if (/<w:webHidden\s*\/?>/.test(run)) reasons.push('hidden in web view');
      spans.push({text: text, hidden: reasons.length > 0, reasons: reasons});
    }
    const dels = (xml.match(/<w:delText(?:\s[^>]*)?>([\s\S]*?)<\/w:delText>/g) || [])
      .map(t => t.replace(/<[^>]+>/g, '')).join(' ');
    if (dels.trim())
      spans.push({text: dels, hidden: true,
                  reasons: ['deleted text retained as a tracked change']});
    return finish(spans, 'docx');
  }

  function finish(spans, kind) {
    const vis = [], hid = [], why = new Set();
    for (const s of spans) {
      (s.hidden ? hid : vis).push(s.text);
      if (s.hidden) s.reasons.forEach(r => why.add(r));
    }
    const visible = vis.join(' ').replace(/\s+/g, ' ').trim();
    const hidden = hid.join(' ').replace(/\s+/g, ' ').trim();
    const total = (visible + hidden).replace(/\s/g, '').length || 1;
    return {
      kind: kind,
      visible: visible,
      hidden: hidden,
      hiddenRatio: hidden.replace(/\s/g, '').length / total,
      reasons: [...why],
      spans: spans.length
    };
  }

  async function scan(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const name = (file.name || '').toLowerCase();
    if (name.endsWith('.docx') || name.endsWith('.docm')) return scanDOCX(bytes);
    if (name.endsWith('.pdf')) return scanPDF(bytes);
    throw new Error('Unsupported file type. Use PDF or DOCX.');
  }

  root.VettaDocScan = {scan: scan, scanPDF: scanPDF, scanDOCX: scanDOCX};
})(typeof window !== 'undefined' ? window : globalThis);

if (typeof module !== 'undefined') module.exports = globalThis.VettaDocScan;
