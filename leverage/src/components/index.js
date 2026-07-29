'use strict';

// The component library. Eleven slide shapes, each one taking a region from
// theme.REGIONS and subdividing it. No component contains an eyeballed
// constant for a y position, because the day a title wraps to two lines every
// such constant is wrong.
//
// Every component obeys two rules from CLAUDE.md:
//   1. It degrades rather than fails. Too much content shrinks the type to the
//      floor, then splits onto a continuation slide. It never clips.
//   2. It returns what it could not place, rather than dropping it.
//
// Tables are built from shapes and text boxes rather than with addTable, so
// that every piece of text on a slide has geometry the validator can read back
// and re-measure. A table cell whose contents overflow is exactly the failure
// this tool exists to catch.

const theme = require('../theme');
const metrics = require('../text/metrics');

const P = theme.PALETTE;

// ---------------------------------------------------------------------------
// shared helpers
// ---------------------------------------------------------------------------

function textOpts(extra = {}) {
  return {
    fontFace: theme.FONT,
    color: P.ink,
    lineSpacing: undefined,
    ...extra,
  };
}

// Place text at the largest size in `sizes` that fits `box`. Returns the size
// used. When nothing fits, places at the smallest size and records a warning
// naming the slide and the field, because a silent clip is the failure mode
// this whole tool is built against.
function fitted(slide, ctx, where, text, box, sizes, opts = {}) {
  const measureOpts = { bold: opts.bold === true };
  const found = metrics.fitDown(text, box, sizes, measureOpts);
  const pt = found ? found.pt : sizes[sizes.length - 1];
  if (!found) {
    const r = metrics.fit(text, box, pt, measureOpts);
    ctx.warn(
      `${where}: text does not fit at the minimum ${pt}pt (needs ${r.height.toFixed(2)}in in a ` +
        `${box.h.toFixed(2)}in box, ${r.lineCount} lines). Shorten it.`,
    );
  }
  slide.addText(text, textOpts({
    x: box.x,
    y: box.y,
    w: box.w,
    h: box.h,
    fontSize: pt,
    valign: opts.valign || 'top',
    align: opts.align || 'left',
    bold: opts.bold === true,
    color: opts.color || P.ink,
    ...(opts.pptx || {}),
  }));
  return pt;
}

// The standard slide frame: title, a navy rule under it, and a footer carrying
// the deck footer text and the slide number.
function frame(slide, spec, ctx, title) {
  const H = theme.REGIONS.header;
  if (title) {
    // The title gets two lines before it shrinks, and shrinking it moves
    // nothing: the body region is fixed, so a long title loses type size
    // rather than eating the content area.
    fitted(slide, ctx, `${ctx.where} title`, title, H, metrics.ladder(theme.TYPE.slideTitle, 16), {
      bold: true,
      color: P.navy,
      valign: 'bottom',
    });
  }
  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: H.x, y: H.y + H.h + 0.1, w: 1.6, h: 0.045, fill: { color: P.navy }, line: { type: 'none' },
  });
  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: H.x + 1.6, y: H.y + H.h + 0.1, w: H.w - 1.6, h: 0.045, fill: { color: P.rule }, line: { type: 'none' },
  });

  const F = theme.REGIONS.footer;
  const footerText = (spec.meta && spec.meta.footer) || '';
  if (footerText) {
    slide.addText(footerText, textOpts({
      x: F.x, y: F.y, w: F.w - 1.2, h: F.h, fontSize: theme.TYPE.micro, color: P.muted, valign: 'middle',
    }));
  }
  slide.addText(String(ctx.index), textOpts({
    x: F.x + F.w - 1.0, y: F.y, w: 1.0, h: F.h, fontSize: theme.TYPE.micro, color: P.muted,
    align: 'right', valign: 'middle',
  }));
}

// Split a list of items so the first chunk fits `box` at `pt`, returning
// [placed, remaining]. Used by every component that takes an unbounded list.
function splitToFit(items, box, pt, gap, measure) {
  const placed = [];
  let used = 0;
  for (let i = 0; i < items.length; i += 1) {
    const h = measure(items[i], pt);
    if (used + h > box.h && placed.length > 0) {
      return [placed, items.slice(i)];
    }
    placed.push(items[i]);
    used += h + gap;
  }
  return [placed, []];
}

// Height of the *text box* holding one bullet, not of the text itself. The
// difference is the vertical inset PowerPoint applies inside every text box,
// and forgetting it makes a one-line bullet overflow its own box by 0.1in.
// validate-deck.js re-measures the placed geometry, so it catches this rather
// than letting it ship.
function bulletHeight(text, pt, innerW) {
  const lines = metrics.lineCount(text, innerW, pt);
  const textH = metrics.linesHeight(lines, pt);
  // Slack, not decoration. A box sized to exactly its own text sits at 100%
  // fill, which is both one rounding error from clipping and visibly cramped.
  const slack = Math.max(0.05, textH * 0.14);
  return textH + slack + theme.INSET.y * 2;
}

// Render a bullet list into `box`, shrinking then splitting. Returns the items
// that did not fit.
function bulletList(slide, ctx, where, items, box, opts = {}) {
  if (items.length === 0) return [];
  const marker = 0.19;          // navy square plus its gap
  const gap = opts.gap ?? 0.11;
  const startPt = opts.startPt ?? theme.TYPE.body;
  const textW = box.w - marker - theme.INSET.x * 2;

  let pt = startPt;
  let placed = items;
  let remaining = [];
  for (const candidate of metrics.ladder(startPt, opts.minPt ?? theme.MIN_BODY_PT)) {
    const total = items.reduce((acc, it) => acc + bulletHeight(it, candidate, textW) + gap, 0) - gap;
    if (total <= box.h) {
      pt = candidate;
      placed = items;
      remaining = [];
      break;
    }
    pt = candidate;
  }
  // At the floor size, split rather than clip.
  const totalAtFloor = placed.reduce((acc, it) => acc + bulletHeight(it, pt, textW) + gap, 0) - gap;
  if (totalAtFloor > box.h) {
    [placed, remaining] = splitToFit(items, box, pt, gap, (it, p) => bulletHeight(it, p, textW) + gap);
  }

  // Vertical distribution. A four-bullet list in a 5in region top-aligned
  // leaves half the slide blank and reads as a draft. Extra space goes into the
  // gaps rather than into a block floating at the top, up to a cap, because
  // beyond that the list stops reading as a list.
  let gapUsed = gap;
  if (placed.length > 1) {
    const contentH = placed.reduce((acc, it) => acc + bulletHeight(it, pt, textW), 0);
    const slack = box.h * 0.94 - contentH - gap * (placed.length - 1);
    if (slack > 0) {
      gapUsed = Math.min(gap * 2.6, gap + slack / (placed.length - 1));
    }
  }

  let y = box.y;
  placed.forEach((item, i) => {
    const h = bulletHeight(item, pt, textW);
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x: box.x, y: y + pt / 72 * 0.42, w: 0.075, h: 0.075,
      fill: { color: opts.markerColor || P.navy }, line: { type: 'none' },
    });
    slide.addText(item, textOpts({
      x: box.x + marker, y, w: box.w - marker, h,
      fontSize: pt, color: opts.color || P.ink, valign: 'top',
    }));
    // Per-bullet overflow is impossible by construction here (the height is
    // derived from the wrap), so the only thing worth reporting is a bullet
    // long enough to be a paragraph.
    if (metrics.lineCount(item, textW, pt) > 4) {
      ctx.warn(`${where}: bullet ${i + 1} wraps to ${metrics.lineCount(item, textW, pt)} lines. A bullet that long is a paragraph.`);
    }
    y += h + gapUsed;
  });
  return remaining;
}

function statusColour(value) {
  const v = String(value || '').trim().toLowerCase();
  if (/^(red|r|high|blocked|off track|slipping)$/.test(v)) return P.red;
  if (/^(amber|a|orange|medium|at risk|watch)$/.test(v)) return P.amber;
  if (/^(green|g|low|on track|done|complete|live)$/.test(v)) return P.green;
  return null;
}

function placeholderOr(value, ctx, where) {
  if (value === undefined || value === null || String(value).trim() === '') {
    ctx.warn(`${where}: not supplied, rendered as PLACEHOLDER.`);
    return 'PLACEHOLDER';
  }
  return String(value);
}

// ---------------------------------------------------------------------------
// components
// ---------------------------------------------------------------------------

// A full-bleed navy opener. No frame: a title slide has no page furniture.
function title(slide, spec, ctx) {
  const R = theme.REGIONS.full;
  slide.background = { color: P.navy };

  const pad = 0.9;
  const box = { x: pad, y: 2.1, w: R.w - pad * 2, h: 2.0 };
  const deckTitle = placeholderOr(ctx.slide.title || spec.meta.title, ctx, `${ctx.where} title`);
  fitted(slide, ctx, `${ctx.where} title`, deckTitle, box, metrics.ladder(theme.TYPE.deckTitle, 22, 1), {
    bold: true, color: P.white, valign: 'bottom',
  });

  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: pad, y: 4.28, w: 1.8, h: 0.05, fill: { color: P.white }, line: { type: 'none' },
  });

  const sub = ctx.slide.fields.subtitle || spec.meta.subtitle;
  if (sub) {
    fitted(slide, ctx, `${ctx.where} subtitle`, sub, { x: pad, y: 4.55, w: R.w - pad * 2 - 2.0, h: 0.9 },
      metrics.ladder(theme.TYPE.deckSubtitle, 12), { color: P.white });
  }

  const meta = [ctx.slide.fields.presenter, ctx.slide.fields.date].filter((v) => v && String(v).trim() !== '');
  if (meta.length) {
    slide.addText(meta.join('   |   '), textOpts({
      x: pad, y: 6.4, w: R.w - pad * 2, h: 0.4, fontSize: theme.TYPE.small, color: P.rule, valign: 'middle',
    }));
  }
}

// A section marker. One line, large, on a navy band, so the deck has visible
// joints when someone scrolls it on a phone.
function divider(slide, spec, ctx) {
  const R = theme.REGIONS.full;
  const bandH = 2.5;
  const bandY = (R.h - bandH) / 2;
  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: 0, y: bandY, w: R.w, h: bandH, fill: { color: P.navy }, line: { type: 'none' },
  });
  const box = { x: 0.9, y: bandY + 0.5, w: R.w - 1.8, h: 1.0 };
  fitted(slide, ctx, `${ctx.where} title`, placeholderOr(ctx.slide.title, ctx, `${ctx.where} title`), box,
    metrics.ladder(32, 18, 1), { bold: true, color: P.white, valign: 'middle' });

  const note = ctx.slide.fields.note || ctx.slide.fields.text;
  if (note) {
    fitted(slide, ctx, `${ctx.where} note`, note, { x: 0.9, y: bandY + 1.6, w: R.w - 1.8, h: 0.6 },
      metrics.ladder(theme.TYPE.body, 10), { color: P.rule });
  }
}

// The executive summary. The verdict occupies a panel at the top and is the
// only thing on the slide set in navy at lead size, because the verdict is the
// slide. Supporting points sit below it.
function summary(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title || 'Executive summary');
  const B = theme.REGIONS.body;

  const verdict = placeholderOr(ctx.slide.fields.verdict, ctx, `${ctx.where} verdict`);
  const verdictBox = { x: B.x + 0.28, y: B.y + 0.22, w: B.w - 0.56, h: 1.0 };
  const used = metrics.fit(verdict, verdictBox, theme.TYPE.verdict, { bold: true });
  const panelH = Math.max(1.0, Math.min(2.2, used.height + 0.5));

  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: B.x, y: B.y, w: B.w, h: panelH, fill: { color: P.panel }, line: { type: 'none' },
  });
  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: B.x, y: B.y, w: 0.07, h: panelH, fill: { color: P.navy }, line: { type: 'none' },
  });
  fitted(slide, ctx, `${ctx.where} verdict`, verdict,
    { x: B.x + 0.28, y: B.y + 0.18, w: B.w - 0.56, h: panelH - 0.36 },
    metrics.ladder(theme.TYPE.verdict, 12), { bold: true, color: P.navy, valign: 'middle' });

  const listY = B.y + panelH + 0.32;
  const listBox = { x: B.x, y: listY, w: B.w, h: B.y + B.h - listY };
  const remaining = bulletList(slide, ctx, ctx.where, ctx.slide.bullets, listBox, { startPt: theme.TYPE.lead });
  return remaining.length ? { overflow: { ...ctx.slide, type: 'bullets', bullets: remaining } } : null;
}

// A plain list slide. The workhorse, and the default when a spec heading gives
// no type.
function bullets(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  let y = B.y;
  let h = B.h;
  const lead = ctx.slide.fields.lead || ctx.slide.fields.text;
  if (lead) {
    const leadBox = { x: B.x, y, w: B.w, h: 0.8 };
    const r = metrics.fit(lead, leadBox, theme.TYPE.lead);
    const leadH = Math.min(1.2, r.height + 0.1);
    fitted(slide, ctx, `${ctx.where} lead`, lead, { x: B.x, y, w: B.w, h: leadH },
      metrics.ladder(theme.TYPE.lead, 11), { color: P.muted });
    y += leadH + 0.28;
    h = B.y + B.h - y;
  }
  const remaining = bulletList(slide, ctx, ctx.where, ctx.slide.bullets, { x: B.x, y, w: B.w, h });
  return remaining.length ? { overflow: { ...ctx.slide, bullets: remaining, fields: {} } } : null;
}

// Two to four columns, one per "###" group. Each column carries a navy heading
// bar so the comparison reads left to right without a legend.
function columns(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  const groups = ctx.slide.groups.length
    ? ctx.slide.groups
    : [{ name: '', items: ctx.slide.bullets }];
  const n = Math.min(4, groups.length);
  if (groups.length > 4) {
    ctx.warn(`${ctx.where}: ${groups.length} columns given, 4 is the maximum that stays readable. The rest moved to a continuation slide.`);
  }
  const gutter = 0.34;
  const colW = (B.w - gutter * (n - 1)) / n;
  const headH = 0.46;

  let overflowGroups = groups.slice(n).map((g) => ({ ...g }));
  for (let i = 0; i < n; i += 1) {
    const g = groups[i];
    const x = B.x + i * (colW + gutter);
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x, y: B.y, w: colW, h: headH, fill: { color: P.navy }, line: { type: 'none' },
    });
    if (g.name) {
      fitted(slide, ctx, `${ctx.where} column ${i + 1} heading`, g.name,
        { x: x + 0.12, y: B.y, w: colW - 0.24, h: headH },
        metrics.ladder(theme.TYPE.body, 9), { bold: true, color: P.white, valign: 'middle' });
    }
    const listBox = { x, y: B.y + headH + 0.22, w: colW, h: B.h - headH - 0.22 };
    const rest = bulletList(slide, ctx, `${ctx.where} column ${i + 1}`, g.items || [], listBox, { gap: 0.1 });
    if (rest.length) overflowGroups.push({ ...g, items: rest });
  }
  return overflowGroups.length
    ? { overflow: { ...ctx.slide, groups: overflowGroups, bullets: [] } }
    : null;
}

// A process flow. Steps run left to right, wrapping to a second row above six,
// with a navy chevron between them so the direction is unambiguous.
function flow(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  const steps = ctx.slide.bullets.length ? ctx.slide.bullets : ctx.slide.groups.map((g) => g.name);
  if (steps.length === 0) {
    ctx.warn(`${ctx.where}: flow slide has no steps.`);
    return null;
  }
  const placed = steps.slice(0, 12);
  const overflow = steps.slice(12);
  const perRow = placed.length <= 6 ? placed.length : Math.ceil(placed.length / 2);
  const rows = Math.ceil(placed.length / perRow);
  const arrowW = 0.42;
  const boxW = (B.w - arrowW * (perRow - 1)) / perRow;
  const rowGap = 0.5;
  // Height from the content, not from the region. Filling the region gives tall
  // boxes holding two lines of text at the top, which renders as empty boxes.
  const textW = boxW - 0.24 - theme.INSET.x * 2;
  const deepest = placed.reduce(
    (max, s) => Math.max(max, metrics.lineCount(s, textW, theme.TYPE.body, true)), 1,
  );
  const contentH = metrics.linesHeight(deepest, theme.TYPE.body) + 0.62;
  const boxH = Math.min(Math.max(0.95, contentH), (B.h - rowGap * (rows - 1)) / rows);
  const startY = B.y + (B.h - (boxH * rows + rowGap * (rows - 1))) / 2;

  placed.forEach((step, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const x = B.x + col * (boxW + arrowW);
    const y = startY + row * (boxH + rowGap);
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x, y, w: boxW, h: boxH, fill: { color: P.panel }, line: { color: P.rule, width: 1 },
    });
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x, y, w: boxW, h: 0.055, fill: { color: P.navy }, line: { type: 'none' },
    });
    slide.addText(String(i + 1), textOpts({
      x: x + 0.1, y: y + 0.1, w: 0.5, h: 0.3, fontSize: theme.TYPE.micro, color: P.muted, bold: true,
    }));
    fitted(slide, ctx, `${ctx.where} step ${i + 1}`, step,
      { x: x + 0.12, y: y + 0.4, w: boxW - 0.24, h: boxH - 0.5 },
      metrics.ladder(theme.TYPE.body, 9), { bold: true, color: P.navy, valign: 'middle' });

    const isRowEnd = col === perRow - 1 || i === placed.length - 1;
    if (!isRowEnd) {
      // An actual arrow. A thin navy bar between boxes reads as a divider, which
      // is the opposite of what a process flow needs to say.
      slide.addShape(ctx.pptx.ShapeType.rightArrow, {
        x: x + boxW + 0.1, y: y + boxH / 2 - 0.11, w: arrowW - 0.2, h: 0.22,
        fill: { color: P.navy }, line: { type: 'none' },
      });
    }
  });
  return overflow.length ? { overflow: { ...ctx.slide, bullets: overflow } } : null;
}

// A phased roadmap. One band per "###" group, its label on the left as the
// period and its items on the right, with a navy axis tying the phases
// together.
function roadmap(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  const phases = ctx.slide.groups.length ? ctx.slide.groups : [];
  if (phases.length === 0) {
    ctx.warn(`${ctx.where}: roadmap has no phases. Add "### Q3 2026 | Phase name" groups.`);
    return null;
  }
  const placed = phases.slice(0, 5);
  const overflow = phases.slice(5);
  const axisX = B.x + 1.85;
  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: axisX, y: B.y + 0.1, w: 0.035, h: B.h - 0.2, fill: { color: P.rule }, line: { type: 'none' },
  });

  const bandH = (B.h - 0.2) / placed.length;
  placed.forEach((phase, i) => {
    const y = B.y + 0.1 + i * bandH;
    slide.addShape(ctx.pptx.ShapeType.ellipse, {
      x: axisX - 0.075, y: y + 0.18, w: 0.185, h: 0.185, fill: { color: P.navy }, line: { type: 'none' },
    });
    const label = phase.label || `Phase ${i + 1}`;
    fitted(slide, ctx, `${ctx.where} phase ${i + 1} label`, label,
      { x: B.x, y: y + 0.1, w: 1.62, h: 0.5 },
      metrics.ladder(theme.TYPE.body, 9), { bold: true, color: P.navy, align: 'right' });
    if (phase.name) {
      fitted(slide, ctx, `${ctx.where} phase ${i + 1} name`, phase.name,
        { x: axisX + 0.28, y: y + 0.08, w: B.w - (axisX - B.x) - 0.28, h: 0.42 },
        metrics.ladder(theme.TYPE.lead, 10), { bold: true, color: P.ink });
    }
    const itemsBox = {
      x: axisX + 0.28,
      y: y + 0.56,
      w: B.w - (axisX - B.x) - 0.28,
      h: bandH - 0.66,
    };
    const rest = bulletList(slide, ctx, `${ctx.where} phase ${i + 1}`, phase.items || [], itemsBox,
      { gap: 0.07, startPt: theme.TYPE.body, markerColor: P.muted });
    if (rest.length) overflow.push({ ...phase, items: rest, label: `${phase.label} cont.` });
  });
  return overflow.length ? { overflow: { ...ctx.slide, groups: overflow } } : null;
}

// A capability map. Each "###" group is a layer, drawn as a row, and its items
// are capability boxes inside that row. Layers stack, so the architecture reads
// top to bottom.
function capabilities(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  const layers = ctx.slide.groups.length ? ctx.slide.groups : [];
  if (layers.length === 0) {
    ctx.warn(`${ctx.where}: capability map has no layers. Add "### Layer name" groups.`);
    return null;
  }
  const placed = layers.slice(0, 5);
  const overflow = layers.slice(5);
  const gapY = 0.2;
  const rowH = (B.h - gapY * (placed.length - 1)) / placed.length;
  const labelW = 1.9;

  placed.forEach((layer, i) => {
    const y = B.y + i * (rowH + gapY);
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x: B.x, y, w: labelW, h: rowH, fill: { color: P.navy }, line: { type: 'none' },
    });
    fitted(slide, ctx, `${ctx.where} layer ${i + 1}`, layer.name || 'PLACEHOLDER',
      { x: B.x + 0.14, y, w: labelW - 0.28, h: rowH },
      metrics.ladder(theme.TYPE.body, 9), { bold: true, color: P.white, valign: 'middle' });

    const items = (layer.items || []).slice(0, 6);
    if ((layer.items || []).length > 6) {
      overflow.push({ ...layer, name: `${layer.name} cont.`, items: layer.items.slice(6) });
    }
    if (items.length === 0) return;
    const zoneX = B.x + labelW + 0.16;
    const zoneW = B.w - labelW - 0.16;
    const gapX = 0.14;
    const boxW = (zoneW - gapX * (items.length - 1)) / items.length;
    items.forEach((item, j) => {
      const x = zoneX + j * (boxW + gapX);
      slide.addShape(ctx.pptx.ShapeType.rect, {
        x, y, w: boxW, h: rowH, fill: { color: P.panel }, line: { color: P.panelDeep, width: 1 },
      });
      fitted(slide, ctx, `${ctx.where} layer ${i + 1} item ${j + 1}`, item,
        { x: x + 0.08, y: y + 0.06, w: boxW - 0.16, h: rowH - 0.12 },
        metrics.ladder(theme.TYPE.small, 8), { color: P.ink, valign: 'middle', align: 'center' });
    });
  });
  return overflow.length ? { overflow: { ...ctx.slide, groups: overflow } } : null;
}

// A RAID or status table, built from shapes so every cell has measurable
// geometry. The last column is read as a status and coloured, which is the one
// piece of formatting that gets done by hand every single week.
function raid(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  const rows = ctx.slide.rows;
  if (rows.length === 0) {
    ctx.warn(`${ctx.where}: table has no rows. Add "| Risk | Item | Owner | Status |" lines.`);
    return null;
  }
  const headers = ctx.slide.headers;
  const colCount = Math.max(headers ? headers.length : 0, ...rows.map((r) => r.length));

  const pt = theme.TYPE.small;

  // Column widths. In a RAID table one column is the description and the rest
  // are short, so proportional-to-content alone squeezes "Amber" into a column
  // narrower than the word and the status wraps. Each column therefore gets a
  // floor wide enough for its longest single word plus its status marker, and
  // only the slack above those floors is shared by content weight.
  const longestWord = (s) => String(s || '')
    .split(/\s+/)
    .reduce((max, w) => Math.max(max, metrics.textWidth(w, pt, true)), 0);

  const floors = [];
  const weights = [];
  for (let c = 0; c < colCount; c += 1) {
    let word = headers && headers[c] ? longestWord(headers[c]) : 0;
    let total = headers && headers[c] ? metrics.textWidth(headers[c], pt, true) : 0;
    for (const r of rows) {
      if (r[c]) {
        word = Math.max(word, longestWord(r[c]));
        total = Math.max(total, metrics.textWidth(r[c], pt));
      }
    }
    // 0.42in covers the cell padding plus the status marker and its gap.
    floors.push(Math.min(2.6, word + 0.42));
    weights.push(Math.max(0.01, total));
  }
  const floorSum = floors.reduce((a, b) => a + b, 0);
  let colW;
  if (floorSum >= B.w) {
    // Even the floors do not fit. Share proportionally and let fitted() shrink.
    const scale = B.w / floorSum;
    colW = floors.map((f) => f * scale);
    ctx.warn(`${ctx.where}: ${colCount} columns need more width than the slide has. Shorten the headers or drop a column.`);
  } else {
    const slack = B.w - floorSum;
    const weightSum = weights.reduce((a, b) => a + b, 0);
    colW = floors.map((f, c) => f + (weights[c] / weightSum) * slack);
  }
  const headH = 0.42;
  let y = B.y;

  if (headers) {
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x: B.x, y, w: B.w, h: headH, fill: { color: P.navy }, line: { type: 'none' },
    });
    let x = B.x;
    headers.forEach((h, c) => {
      fitted(slide, ctx, `${ctx.where} header ${c + 1}`, h,
        { x: x + 0.06, y, w: colW[c] - 0.12, h: headH },
        metrics.ladder(pt, 8), { bold: true, color: P.white, valign: 'middle' });
      x += colW[c];
    });
    y += headH;
  }

  const cellH = (cells) => {
    let max = 0.36;
    cells.forEach((cell, c) => {
      const lines = metrics.lineCount(cell || '', colW[c] - 0.12 - theme.INSET.x * 2, pt);
      max = Math.max(max, metrics.linesHeight(lines, pt) + theme.INSET.y * 2 + 0.16);
    });
    return max;
  };

  const available = B.y + B.h - y;
  const [placed, overflow] = splitToFit(rows, { h: available }, pt, 0, (r) => cellH(r));

  placed.forEach((row, i) => {
    const h = cellH(row);
    if (i % 2 === 1) {
      slide.addShape(ctx.pptx.ShapeType.rect, {
        x: B.x, y, w: B.w, h, fill: { color: P.panel }, line: { type: 'none' },
      });
    }
    let x = B.x;
    row.forEach((cell, c) => {
      const status = c === row.length - 1 ? statusColour(cell) : null;
      if (status) {
        slide.addShape(ctx.pptx.ShapeType.rect, {
          x: x + 0.06, y: y + h / 2 - 0.055, w: 0.11, h: 0.11, fill: { color: status }, line: { type: 'none' },
        });
      }
      fitted(slide, ctx, `${ctx.where} row ${i + 1} cell ${c + 1}`, cell || '',
        { x: x + (status ? 0.24 : 0.06), y: y + 0.03, w: colW[c] - (status ? 0.3 : 0.12), h: h - 0.06 },
        metrics.ladder(pt, 8), { color: status || P.ink, bold: Boolean(status), valign: 'middle' });
      x += colW[c];
    });
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x: B.x, y: y + h, w: B.w, h: 0.012, fill: { color: P.rule }, line: { type: 'none' },
    });
    y += h;
  });

  return overflow.length ? { overflow: { ...ctx.slide, rows: overflow } } : null;
}

// A stakeholder map on influence and interest. Rows are
// "Name | influence | interest | note", where influence and interest are high,
// medium or low. Placement is derived, so moving someone is a one-word edit
// rather than dragging a box.
function stakeholders(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.title);
  const B = theme.REGIONS.body;
  const rows = ctx.slide.rows.length
    ? ctx.slide.rows
    : ctx.slide.bullets.map((b) => b.split('|').map((c) => c.trim()));
  if (rows.length === 0) {
    ctx.warn(`${ctx.where}: stakeholder map has no entries. Add "| Name | high | low | note |" lines.`);
    return null;
  }

  const axis = 0.46;
  const gridX = B.x + axis;
  const gridY = B.y;
  const gridW = B.w - axis;
  const gridH = B.h - axis;
  const quadW = gridW / 2;
  const quadH = gridH / 2;

  const quadFills = [P.panel, P.panelDeep, P.white, P.panel];
  const quadNames = ['Keep satisfied', 'Manage closely', 'Monitor', 'Keep informed'];
  for (let q = 0; q < 4; q += 1) {
    const col = q % 2;
    const row = Math.floor(q / 2);
    const x = gridX + col * quadW;
    const y = gridY + row * quadH;
    slide.addShape(ctx.pptx.ShapeType.rect, {
      x, y, w: quadW, h: quadH, fill: { color: quadFills[q] }, line: { color: P.rule, width: 1 },
    });
    slide.addText(quadNames[q], textOpts({
      x: x + 0.1, y: y + 0.06, w: quadW - 0.2, h: 0.3,
      fontSize: theme.TYPE.micro, color: P.muted, bold: true,
    }));
  }
  // A rotated shape spins about its own centre, so its x and y are not where it
  // appears. Place these by the centre the label should end up at, or the box
  // lands somewhere else entirely: the first version of this put "Influence"
  // through the middle of the grid it was labelling, and passed validation
  // because the bounds check ignored rotation. Both are fixed.
  const axisLabel = (text, cx, cy, rotate) => {
    const w = 1.5;
    const h = 0.3;
    slide.addText(text, textOpts({
      x: cx - w / 2, y: cy - h / 2, w, h,
      fontSize: theme.TYPE.micro, color: P.navy, bold: true, align: 'center', valign: 'middle',
      ...(rotate ? { rotate } : {}),
    }));
  };
  axisLabel('Influence', B.x + axis / 2, gridY + gridH / 2, 270);
  axisLabel('Interest', gridX + gridW / 2, gridY + gridH + 0.2, 0);

  const level = (v) => {
    const s = String(v || '').trim().toLowerCase();
    if (/^(h|high|hi)$/.test(s)) return 'high';
    if (/^(l|low|lo)$/.test(s)) return 'low';
    return 'med';
  };
  const buckets = [[], [], [], []];
  const overflow = [];
  rows.forEach((row, i) => {
    const name = (row[0] || '').trim();
    if (name === '') return;
    const inf = level(row[1]);
    const int = level(row[2]);
    const note = (row[3] || '').trim();
    // high influence is the top row, high interest is the right column
    const col = int === 'low' ? 0 : 1;
    const rowIdx = inf === 'low' ? 1 : 0;
    const q = rowIdx * 2 + col;
    if (buckets[q].length >= 6) {
      overflow.push(row);
      ctx.warn(`${ctx.where}: quadrant "${quadNames[q]}" already holds 6 names, "${name}" moved to a continuation slide.`);
      return;
    }
    buckets[q].push({ name, note, key: `${i}` });
  });

  buckets.forEach((people, q) => {
    const col = q % 2;
    const row = Math.floor(q / 2);
    const x = gridX + col * quadW + 0.12;
    let y = gridY + row * quadH + 0.42;
    const w = quadW - 0.24;
    people.forEach((p, i) => {
      const label = p.note ? `${p.name}, ${p.note}` : p.name;
      const lines = metrics.lineCount(label, w - 0.16 - theme.INSET.x * 2, theme.TYPE.small);
      const h = metrics.linesHeight(lines, theme.TYPE.small) + theme.INSET.y * 2 + 0.14;
      if (y + h > gridY + (row + 1) * quadH - 0.06) {
        overflow.push([p.name, row === 0 ? 'high' : 'low', col === 1 ? 'high' : 'low', p.note]);
        ctx.warn(`${ctx.where}: "${p.name}" did not fit quadrant "${quadNames[q]}", moved to a continuation slide.`);
        return;
      }
      slide.addShape(ctx.pptx.ShapeType.rect, {
        x, y, w, h, fill: { color: P.white }, line: { color: P.navy, width: 0.75 },
      });
      fitted(slide, ctx, `${ctx.where} ${quadNames[q]} entry ${i + 1}`, label,
        { x: x + 0.08, y: y + 0.03, w: w - 0.16, h: h - 0.06 },
        metrics.ladder(theme.TYPE.small, 8), { color: P.navy, valign: 'middle' });
      y += h + 0.08;
    });
  });

  return overflow.length ? { overflow: { ...ctx.slide, rows: overflow, bullets: [] } } : null;
}

// One number, one claim. The slide that exists because a figure buried in a
// table does not land and the same figure at 86pt does.
function metric(slide, spec, ctx) {
  frame(slide, spec, ctx, ctx.slide.fields.heading || '');
  const B = theme.REGIONS.body;
  const value = placeholderOr(ctx.slide.title, ctx, `${ctx.where} value`);
  const label = ctx.slide.fields.label || ctx.slide.fields.text || '';

  const numBox = { x: B.x, y: B.y + 0.5, w: B.w, h: 2.3 };
  fitted(slide, ctx, `${ctx.where} value`, value, numBox, metrics.ladder(theme.TYPE.metric, 30, 2),
    { bold: true, color: P.navy, align: 'center', valign: 'middle' });

  slide.addShape(ctx.pptx.ShapeType.rect, {
    x: B.x + B.w / 2 - 0.9, y: B.y + 2.95, w: 1.8, h: 0.05, fill: { color: P.navy }, line: { type: 'none' },
  });

  if (label) {
    fitted(slide, ctx, `${ctx.where} label`, label,
      { x: B.x + 1.6, y: B.y + 3.2, w: B.w - 3.2, h: 0.9 },
      metrics.ladder(theme.TYPE.lead, 11), { color: P.ink, align: 'center' });
  }
  const note = ctx.slide.fields.note;
  if (note) {
    fitted(slide, ctx, `${ctx.where} note`, note,
      { x: B.x + 1.6, y: B.y + 4.15, w: B.w - 3.2, h: 0.6 },
      metrics.ladder(theme.TYPE.small, 9), { color: P.muted, align: 'center' });
  }
}

const COMPONENTS = {
  title,
  divider,
  summary,
  bullets,
  columns,
  flow,
  roadmap,
  capabilities,
  raid,
  stakeholders,
  metric,
};

module.exports = { COMPONENTS, frame, bulletList, fitted, statusColour, splitToFit };
