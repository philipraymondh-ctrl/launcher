'use strict';

// Text measurement without a font engine.
//
// pptxgenjs writes XML; nothing in this pipeline rasterises a glyph. So the
// only way to know whether text overflows its box is to measure it from font
// metrics. These are the standard advance widths for Helvetica and
// Helvetica-Bold in 1/1000 em, which Arial matches to within about 1% across
// Latin text because the two were designed to be metrically interchangeable.
//
// The 1% is why callers get a ratio rather than a boolean, and why theme.js
// carries a warning band as well as an error threshold. Read CLAUDE.md,
// "the estimator", before trusting this for anything tighter.

const theme = require('../theme');

const REGULAR = {
  ' ': 278, '!': 278, '"': 355, '#': 556, $: 556, '%': 889, '&': 667, "'": 191,
  '(': 333, ')': 333, '*': 389, '+': 584, ',': 278, '-': 333, '.': 278, '/': 278,
  0: 556, 1: 556, 2: 556, 3: 556, 4: 556, 5: 556, 6: 556, 7: 556, 8: 556, 9: 556,
  ':': 278, ';': 278, '<': 584, '=': 584, '>': 584, '?': 556, '@': 1015,
  A: 667, B: 667, C: 722, D: 722, E: 667, F: 611, G: 778, H: 722, I: 278, J: 500,
  K: 667, L: 556, M: 833, N: 722, O: 778, P: 667, Q: 778, R: 722, S: 667, T: 611,
  U: 722, V: 667, W: 944, X: 667, Y: 667, Z: 611,
  '[': 278, '\\': 278, ']': 278, '^': 469, _: 556, '`': 333,
  a: 556, b: 556, c: 500, d: 556, e: 556, f: 278, g: 556, h: 556, i: 222, j: 222,
  k: 500, l: 222, m: 833, n: 556, o: 556, p: 556, q: 556, r: 333, s: 500, t: 278,
  u: 556, v: 500, w: 722, x: 500, y: 500, z: 500,
  '{': 334, '|': 260, '}': 334, '~': 584,
};

const BOLD = {
  ' ': 278, '!': 333, '"': 474, '#': 556, $: 556, '%': 889, '&': 722, "'": 238,
  '(': 333, ')': 333, '*': 389, '+': 584, ',': 278, '-': 333, '.': 278, '/': 278,
  0: 556, 1: 556, 2: 556, 3: 556, 4: 556, 5: 556, 6: 556, 7: 556, 8: 556, 9: 556,
  ':': 333, ';': 333, '<': 584, '=': 584, '>': 584, '?': 611, '@': 975,
  A: 722, B: 722, C: 722, D: 722, E: 667, F: 611, G: 778, H: 722, I: 278, J: 556,
  K: 722, L: 611, M: 833, N: 722, O: 778, P: 667, Q: 778, R: 722, S: 667, T: 611,
  U: 722, V: 667, W: 944, X: 667, Y: 667, Z: 611,
  '[': 333, '\\': 278, ']': 333, '^': 584, _: 556, '`': 333,
  a: 556, b: 611, c: 556, d: 611, e: 556, f: 333, g: 611, h: 611, i: 278, j: 278,
  k: 556, l: 278, m: 889, n: 611, o: 611, p: 611, q: 611, r: 389, s: 556, t: 333,
  u: 611, v: 556, w: 778, x: 556, y: 556, z: 500,
  '{': 389, '|': 280, '}': 389, '~': 584,
};

// Latin-1 letters that a Danish, Swedish, Norwegian or French name will
// actually contain. Each takes its base letter's advance, which is exact for
// the diacritic cases and close for the ligatures.
const FOLD = {
  À: 'A', Á: 'A', Â: 'A', Ã: 'A', Ä: 'A', Å: 'A', Æ: 'AE',
  Ç: 'C', È: 'E', É: 'E', Ê: 'E', Ë: 'E',
  Ì: 'I', Í: 'I', Î: 'I', Ï: 'I', Ñ: 'N',
  Ò: 'O', Ó: 'O', Ô: 'O', Õ: 'O', Ö: 'O', Ø: 'O', Œ: 'OE',
  Ù: 'U', Ú: 'U', Û: 'U', Ü: 'U', Ý: 'Y',
  à: 'a', á: 'a', â: 'a', ã: 'a', ä: 'a', å: 'a', æ: 'ae',
  ç: 'c', è: 'e', é: 'e', ê: 'e', ë: 'e',
  ì: 'i', í: 'i', î: 'i', ï: 'i', ñ: 'n',
  ò: 'o', ó: 'o', ô: 'o', õ: 'o', ö: 'o', ø: 'o', œ: 'oe',
  ù: 'u', ú: 'u', û: 'u', ü: 'u', ý: 'y', ÿ: 'y', ß: 'ss',
  ' ': ' ', '’': "'", '‘': "'", '“': '"', '”': '"',
  '–': '-', '…': '...',
};

const DEFAULT_ADVANCE = 556;

// Advance width of one character in 1/1000 em.
function charWidth(ch, bold) {
  const table = bold ? BOLD : REGULAR;
  if (Object.prototype.hasOwnProperty.call(table, ch)) return table[ch];
  const folded = FOLD[ch];
  if (folded !== undefined) {
    let sum = 0;
    for (const c of folded) sum += table[c] !== undefined ? table[c] : DEFAULT_ADVANCE;
    return sum;
  }
  return DEFAULT_ADVANCE;
}

// Width of a string in inches at a given point size.
function textWidth(str, pt, bold = false) {
  let em = 0;
  for (const ch of String(str)) em += charWidth(ch, bold);
  return (em / 1000) * (pt / 72);
}

// Greedy word wrap, the same algorithm PowerPoint uses for a plain text box.
// Returns the wrapped lines. A single word longer than the line is broken
// mid-word rather than allowed to run out of the box, which is also what
// PowerPoint does.
function wrap(str, widthIn, pt, bold = false) {
  const out = [];
  const paragraphs = String(str).split('\n');
  for (const para of paragraphs) {
    const words = para.split(/\s+/).filter((w) => w.length > 0);
    if (words.length === 0) {
      out.push('');
      continue;
    }
    let line = '';
    for (const word of words) {
      const candidate = line === '' ? word : `${line} ${word}`;
      if (textWidth(candidate, pt, bold) <= widthIn || line === '') {
        if (textWidth(candidate, pt, bold) > widthIn && line === '') {
          // One unbreakable word wider than the box. Break it by character.
          let chunk = '';
          for (const ch of word) {
            if (textWidth(chunk + ch, pt, bold) > widthIn && chunk !== '') {
              out.push(chunk);
              chunk = ch;
            } else {
              chunk += ch;
            }
          }
          line = chunk;
        } else {
          line = candidate;
        }
      } else {
        out.push(line);
        line = word;
      }
    }
    out.push(line);
  }
  return out;
}

function lineCount(str, widthIn, pt, bold = false) {
  return wrap(str, widthIn, pt, bold).length;
}

// Height in inches that `lines` lines of `pt` text occupy.
function linesHeight(lines, pt, lineSpacing = theme.LINE_SPACING) {
  return (lines * pt * lineSpacing) / 72;
}

// The core question: does this text fit this box?
//
// `box` is { w, h } in inches, as passed to pptxgenjs. Insets are subtracted
// unless the caller says the shape has none. Returns the fill ratio, so
// callers can distinguish "just fits" from "fits easily" and shrink
// accordingly, plus the wrapped lines so a caller can report where it broke.
function fit(str, box, pt, opts = {}) {
  const bold = opts.bold === true;
  const lineSpacing = opts.lineSpacing || theme.LINE_SPACING;
  const insetX = opts.inset === false ? 0 : (opts.insetX ?? theme.INSET.x);
  const insetY = opts.inset === false ? 0 : (opts.insetY ?? theme.INSET.y);
  const innerW = Math.max(0.01, box.w - insetX * 2);
  const innerH = Math.max(0.01, box.h - insetY * 2);
  const lines = wrap(str, innerW, pt, bold);
  const height = linesHeight(lines.length, pt, lineSpacing);
  return {
    lines,
    lineCount: lines.length,
    height,
    innerW,
    innerH,
    ratio: height / innerH,
    fits: height <= innerH,
  };
}

// Largest point size from `sizes` (descending) at which `str` fits `box` with
// the safety margin left over. Returns null when even the smallest overflows,
// so the caller has to decide what to do about it rather than being handed an
// unreadable font size.
//
// The margin defaults to theme.FIT_TARGET rather than to 1.0 on purpose: a box
// packed to exactly its own height is one metric rounding away from clipping,
// and it also looks packed.
function fitDown(str, box, sizes, opts = {}) {
  const target = opts.maxFill ?? theme.FIT_TARGET;
  for (const pt of sizes) {
    const r = fit(str, box, pt, opts);
    if (r.ratio <= target) return { pt, ...r };
  }
  // Nothing hit the target. Fall back to anything that merely fits, so a tight
  // box degrades to tight rather than to overflowing.
  for (const pt of sizes) {
    const r = fit(str, box, pt, opts);
    if (r.ratio <= 1.0) return { pt, ...r, tight: true };
  }
  return null;
}

// Descending candidate sizes from `start` down to `floor`, in half points.
function ladder(start, floor = theme.MIN_BODY_PT, step = 0.5) {
  const out = [];
  for (let pt = start; pt >= floor - 1e-9; pt -= step) out.push(Math.round(pt * 2) / 2);
  return out;
}

module.exports = {
  charWidth,
  textWidth,
  wrap,
  lineCount,
  linesHeight,
  fit,
  fitDown,
  ladder,
  REGULAR,
  BOLD,
};
