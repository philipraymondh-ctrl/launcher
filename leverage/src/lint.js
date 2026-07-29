'use strict';

// The writing standards, as code. A rule in a README drifts; a rule that fails
// the build does not.
//
// Two things here are mechanical and get enforced absolutely: em-dashes and
// emojis. One thing is structural and gets enforced by proxy: "verdict or
// action first" cannot be checked by reading characters, but a summary slide
// with no verdict field can be, and that is where the rule is usually broken.
//
// Nothing here tries to judge whether prose is good. It catches the four
// failures that are actually checkable.

const EM_DASH = /[—―]/;
const EN_DASH = /–/;

// Emoji and pictographic ranges. Deliberately broad: a deck for a senior
// audience contains none of this, so a false positive costs one character edit
// and a false negative ships an emoji to a steering committee.
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{1F1E6}-\u{1F1FF}\u{2190}-\u{21FF}\u{2700}-\u{27BF}]/u;

const PLACEHOLDER = 'PLACEHOLDER';

class LintReport {
  constructor() {
    this.errors = [];
    this.warnings = [];
    this.placeholders = [];
  }

  error(where, message) {
    this.errors.push({ where, message });
  }

  warn(where, message) {
    this.warnings.push({ where, message });
  }

  placeholder(where, text) {
    this.placeholders.push({ where, text });
  }

  get ok() {
    return this.errors.length === 0;
  }
}

// Check one string. `where` names the slide and field so the message points at
// something the author can find with their thumbs.
function checkText(text, where, report) {
  if (typeof text !== 'string' || text === '') return;

  if (EM_DASH.test(text)) {
    report.error(where, `em-dash found. Use a comma, a colon, or two sentences: "${clip(text)}"`);
  }
  if (EN_DASH.test(text)) {
    report.warn(where, `en-dash found. Intentional in a date range, otherwise use a comma: "${clip(text)}"`);
  }
  const emoji = text.match(EMOJI);
  if (emoji) {
    report.error(where, `emoji or pictograph "${emoji[0]}" found: "${clip(text)}"`);
  }
  if (text.includes(PLACEHOLDER)) {
    report.placeholder(where, clip(text));
  }
}

function clip(text, n = 60) {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length <= n ? flat : `${flat.slice(0, n - 3)}...`;
}

// Walk a parsed spec and check every string a reader will see, plus the
// structural rules.
function lintSpec(spec) {
  const report = new LintReport();

  for (const [key, value] of Object.entries(spec.meta || {})) {
    checkText(value, `deck.${key}`, report);
  }

  spec.slides.forEach((slide, i) => {
    const label = `slide ${i + 1} (${slide.type})`;
    checkText(slide.title, `${label} title`, report);
    for (const [key, value] of Object.entries(slide.fields || {})) {
      checkText(value, `${label} ${key}`, report);
    }
    (slide.bullets || []).forEach((b, j) => checkText(b, `${label} bullet ${j + 1}`, report));
    (slide.groups || []).forEach((g, j) => {
      checkText(g.name, `${label} group ${j + 1} heading`, report);
      (g.items || []).forEach((it, k) => checkText(it, `${label} group ${j + 1} item ${k + 1}`, report));
    });
    (slide.rows || []).forEach((row, j) => {
      row.forEach((cell, k) => checkText(cell, `${label} row ${j + 1} cell ${k + 1}`, report));
    });

    // Verdict or action first, enforced where it is checkable: an executive
    // summary whose first line is context rather than a conclusion is the
    // single most common way this rule gets broken.
    if (slide.type === 'summary' && !slide.fields.verdict) {
      report.error(
        `${label}`,
        'summary slide has no "verdict:" line. The verdict is the slide. Add one, even if it is PLACEHOLDER.',
      );
    }
  });

  return report;
}

module.exports = { lintSpec, checkText, LintReport, clip, PLACEHOLDER };
