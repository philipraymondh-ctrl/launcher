'use strict';

// The locked standards, in one place. validate-deck.js asserts against these
// exact values, so a change here is a change to what passes validation.

const NAVY = '001965';

const PALETTE = {
  navy: NAVY,
  white: 'FFFFFF',
  ink: '1A1A1A',
  muted: '5A5A5A',
  rule: 'D4D6DD',
  panel: 'F2F3F7',
  panelDeep: 'E4E7EF',
  red: 'B3132B',
  amber: 'C77700',
  green: '14713D',
};

// Every hex the deck is allowed to contain. An srgbClr outside this set in any
// slide is a validation error, which is how a leaked default theme colour or a
// hand-typed brand-adjacent blue gets caught.
const ALLOWED_HEX = new Set(Object.values(PALETTE));

const FONT = 'Arial';

const SLIDE = { w: 13.333, h: 7.5 };

// Named regions. Components subdivide these; they never invent constants.
const REGIONS = {
  // Full bleed, used by title and divider slides.
  full: { x: 0, y: 0, w: SLIDE.w, h: SLIDE.h },
  // The content area of a standard slide: below the header rule, above the
  // footer. Every content component is handed this and must stay inside it.
  body: { x: 0.62, y: 1.62, w: 12.09, h: 5.16 },
  header: { x: 0.62, y: 0.46, w: 12.09, h: 0.9 },
  footer: { x: 0.62, y: 6.94, w: 12.09, h: 0.32 },
};

// Type scale, in points. Named by role rather than by size so a component
// cannot quietly pick 13.5.
const TYPE = {
  deckTitle: 40,
  deckSubtitle: 18,
  slideTitle: 26,
  verdict: 17,
  lead: 15,
  body: 13,
  small: 11,
  micro: 9.5,
  metric: 86,
};

// Smallest font a component may shrink to before it must split content onto a
// continuation slide instead. Below this a senior audience cannot read it on a
// screen at the back of a room.
const MIN_BODY_PT = 10;

// pptxgenjs applies these internal insets to text boxes by default (inches).
// The estimator must subtract them or it will think text fits when it does not.
const INSET = { x: 0.1, y: 0.05 };

// Line spacing multiple that PowerPoint applies for single-spaced text.
const LINE_SPACING = 1.2;

// Fill ratio thresholds. See CLAUDE.md, "the estimator".
//
// FIT_TARGET is what a component aims for when it picks a font size: fill no
// more than 85% of the box. OVERFLOW_WARN sits above it deliberately. If
// components maximised fill instead, every box would land in the warning band
// and the warning would carry no information, which is how a useful check
// becomes an ignored one.
const FIT_TARGET = 0.85;
const OVERFLOW_ERROR = 1.0;
const OVERFLOW_WARN = 0.92;
// Float slack. A box sized to hold exactly its own text must not fail on the
// last bit of a double.
const FILL_EPSILON = 0.005;

module.exports = {
  NAVY,
  PALETTE,
  ALLOWED_HEX,
  FONT,
  SLIDE,
  REGIONS,
  TYPE,
  MIN_BODY_PT,
  INSET,
  LINE_SPACING,
  FIT_TARGET,
  OVERFLOW_ERROR,
  OVERFLOW_WARN,
  FILL_EPSILON,
};
