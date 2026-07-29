'use strict';

// The spec parser. This is the contract with the phone, so it is deliberately
// forgiving about how something is typed and deliberately strict about what it
// will invent, which is nothing.
//
// Format, all of which fits in a notes app:
//
//   # Data Foundations Programme          <- deck title (or "title:")
//   subtitle: Steering review
//   footer: Internal
//
//   ## summary: Where we stand
//   verdict: Three markets are live. Market four needs a decision today.
//   - Live in DK, SE, NO
//   - Market four blocked on residency sign-off
//
//   ## roadmap: Delivery phases
//   ### Q3 2026 | Foundations
//   - Data contracts signed
//
//   ## raid: Risks and decisions
//   headers: Type | Item | Owner | Status
//   | Risk | Residency sign-off outstanding | PLACEHOLDER | Red |
//
// Rules:
//   "## type: Title"   starts a slide. Type is case-insensitive.
//   "## Title"         starts a bullets slide (the common case, fewest thumbs).
//   "### Name"         starts a group inside the current slide.
//   "- item"           a bullet, belonging to the current group if there is one.
//   "| a | b |"        a table row.
//   "key: value"       a field on the current slide, or deck meta before the
//                      first slide.
//   anything else      appended to the slide's free text.

const TYPES = new Set([
  'title',
  'divider',
  'summary',
  'bullets',
  'columns',
  'flow',
  'roadmap',
  'capabilities',
  'raid',
  'stakeholders',
  'metric',
]);

// Deck-level keys. Anything else before the first slide is still kept, so a
// future component can read it, but these are the ones the title slide uses.
const META_KEYS = new Set(['title', 'subtitle', 'presenter', 'date', 'footer', 'titleslide']);

class SpecError extends Error {
  constructor(line, message) {
    super(line ? `line ${line}: ${message}` : message);
    this.line = line;
    this.name = 'SpecError';
  }
}

function normaliseKey(raw) {
  return raw.trim().toLowerCase().replace(/\s+/g, '');
}

function parseTableRow(line) {
  // "| a | b |" and "a | b" both work. Empty leading and trailing cells from
  // the pipe delimiters are dropped, interior empty cells are kept.
  let body = line.trim();
  if (body.startsWith('|')) body = body.slice(1);
  if (body.endsWith('|')) body = body.slice(0, -1);
  return body.split('|').map((c) => c.trim());
}

function isSeparatorRow(cells) {
  // A markdown table separator, "|---|---|". Someone typing a table in a
  // markdown editor gets one for free and should not see it on a slide.
  return cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c));
}

function newSlide(type, title, line) {
  return { type, title: title || '', fields: {}, bullets: [], groups: [], rows: [], headers: null, line };
}

function parse(source) {
  const lines = String(source).split(/\r?\n/);
  const meta = {};
  const slides = [];
  let slide = null;
  let group = null;
  const textParts = new Map();

  const pushText = (target, value) => {
    const key = target === null ? '__meta__' : target;
    const prev = textParts.get(key) || [];
    prev.push(value);
    textParts.set(key, prev);
  };

  lines.forEach((raw, idx) => {
    const lineNo = idx + 1;
    const line = raw.trim();
    if (line === '') return;
    if (line.startsWith('<!--')) return;

    // Deck title shorthand: a single-hash heading anywhere before slide one.
    const h1 = line.match(/^#\s+(.*)$/);
    if (h1 && !line.startsWith('##')) {
      if (slide) throw new SpecError(lineNo, 'a "#" deck title must come before the first "##" slide');
      meta.title = h1[1].trim();
      return;
    }

    const h3 = line.match(/^###\s+(.*)$/);
    if (h3 && !line.startsWith('####')) {
      if (!slide) throw new SpecError(lineNo, '"###" group appears before any "##" slide');
      // "### Q3 2026 | Foundations" splits into a label and a name, which is
      // what the roadmap component wants and what the others ignore.
      const parts = h3[1].split('|').map((p) => p.trim());
      group = { name: parts.length > 1 ? parts.slice(1).join(' ') : parts[0], label: parts.length > 1 ? parts[0] : '', items: [] };
      slide.groups.push(group);
      return;
    }

    const h2 = line.match(/^##\s+(.*)$/);
    if (h2) {
      const rest = h2[1].trim();
      const colon = rest.indexOf(':');
      let type = 'bullets';
      let title = rest;
      if (colon > 0) {
        const candidate = normaliseKey(rest.slice(0, colon));
        if (TYPES.has(candidate)) {
          type = candidate;
          title = rest.slice(colon + 1).trim();
        }
      }
      if (colon > 0 && !TYPES.has(normaliseKey(rest.slice(0, colon)))) {
        // A colon that is not a known type is part of the title, not a typo to
        // guess at. But if it looks like a type attempt, say so rather than
        // silently making a bullets slide.
        const candidate = normaliseKey(rest.slice(0, colon));
        if (!candidate.includes(' ') && candidate.length <= 14 && !/\s/.test(rest.slice(0, colon).trim())) {
          throw new SpecError(
            lineNo,
            `unknown slide type "${candidate}". Valid types: ${[...TYPES].sort().join(', ')}. ` +
              'If that was meant as part of the title, rephrase so the first word is not followed by a colon.',
          );
        }
      }
      slide = newSlide(type, title, lineNo);
      slides.push(slide);
      group = null;
      return;
    }

    if (line.startsWith('- ') || line === '-') {
      const item = line.slice(1).trim();
      if (!slide) throw new SpecError(lineNo, 'bullet appears before any "##" slide');
      if (group) group.items.push(item);
      else slide.bullets.push(item);
      return;
    }

    // A leading pipe always means a table row, even when a cell contains a
    // colon. Without the pipe, a "key: value" line wins over the table branch,
    // because "headers: Type | Item | Owner" is a field whose *value* contains
    // pipes and reading it as a row silently costs the table its header bar.
    const looksLikeField = /^[A-Za-z][A-Za-z0-9 _-]{0,24}:\s/.test(line);
    if (line.includes('|') && (line.startsWith('|') || !looksLikeField)) {
      const cells = parseTableRow(line);
      if (!slide) throw new SpecError(lineNo, 'table row appears before any "##" slide');
      if (!isSeparatorRow(cells)) slide.rows.push(cells);
      return;
    }

    const kv = line.match(/^([A-Za-z][A-Za-z0-9 _-]{0,24}):\s*(.*)$/);
    if (kv) {
      const key = normaliseKey(kv[1]);
      const value = kv[2].trim();
      if (!slide) {
        if (!META_KEYS.has(key) && key !== '') {
          // Kept, not rejected: an unknown deck-level key is more likely a note
          // to self than an error, and refusing to build over it would be the
          // kind of brittleness that gets a tool abandoned.
          meta[key] = value;
        } else {
          meta[key] = value;
        }
        return;
      }
      if (key === 'headers') {
        slide.headers = value.split('|').map((c) => c.trim());
        return;
      }
      slide.fields[key] = value;
      return;
    }

    // Free text. Belongs to the slide, or to the deck subtitle before slide one.
    pushText(slide ? slides.length - 1 : null, line);
  });

  // Attach accumulated free text.
  for (const [key, parts] of textParts) {
    const joined = parts.join(' ');
    if (key === '__meta__') {
      meta.text = meta.text ? `${meta.text} ${joined}` : joined;
    } else {
      const target = slides[key];
      target.fields.text = target.fields.text ? `${target.fields.text} ${joined}` : joined;
    }
  }

  if (slides.length === 0) {
    throw new SpecError(null, 'spec contains no slides. Add at least one "## type: Title" line.');
  }

  // An implicit title slide, because typing one out is four lines a phone does
  // not need to see. Suppress with "titleslide: no".
  const wantsTitle = !/^(no|false|off)$/i.test(String(meta.titleslide || '').trim());
  if (wantsTitle && slides[0].type !== 'title' && (meta.title || meta.subtitle)) {
    slides.unshift({
      ...newSlide('title', meta.title || 'PLACEHOLDER', 0),
      fields: {
        subtitle: meta.subtitle || '',
        presenter: meta.presenter || '',
        date: meta.date || '',
      },
      implicit: true,
    });
  }

  return { meta, slides };
}

module.exports = { parse, SpecError, TYPES, parseTableRow };
