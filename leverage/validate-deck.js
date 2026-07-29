#!/usr/bin/env node
'use strict';

// validate-deck.js
//
// Reads a .pptx back off disk, unzips it, and asserts the locked standards
// against its XML. This is the only thing in the repo permitted to say a deck
// is done. `deck build` exiting 0 is not evidence.
//
// It works on decks Deckwright did not build. That is idea 2 from IDEAS.md,
// which the Compounder vetoed as a standalone product and which lives here
// instead: point it at an inherited deck and it reports the same violations.
//
// Checks:
//   1. opens        the file is a zip containing ppt/presentation.xml and at
//                   least one slide part
//   2. slide count  matches --slides N, or the slide count of --spec FILE
//   3. fonts        every typeface in every slide is Arial, and the theme's
//                   major and minor Latin fonts are Arial
//   4. colours      every srgbClr in every slide is in theme.PALETTE
//   5. overflow     every text box is re-measured from font metrics against
//                   its own geometry. Above 1.0 fails, 0.9 to 1.0 warns
//   6. bounds       no shape sits outside the slide
//   7. writing      no em-dashes, no emojis, in the text as written to the file
//
// Exit code 0 only when there are no errors. Warnings do not fail the run, and
// are printed loudly enough to be read.

const fs = require('fs');
const path = require('path');
const JSZip = require('jszip');

const theme = require('./src/theme');
const metrics = require('./src/text/metrics');
const { checkText, LintReport } = require('./src/lint');

const EMU_PER_INCH = 914400;
// PowerPoint's default text box insets, in EMU. Used when a bodyPr omits them.
const DEFAULT_INS = { l: 91440, r: 91440, t: 45720, b: 45720 };

function unescapeXml(s) {
  return String(s)
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10)))
    .replace(/&amp;/g, '&');
}

function attr(tag, name) {
  const m = tag && tag.match(new RegExp(`${name}="([^"]*)"`));
  return m ? m[1] : null;
}

// Pull every shape out of a slide's XML with its geometry and its text runs.
// A regex reader rather than a DOM: the shapes pptxgenjs emits are flat and
// predictable, and a parser dependency is a dependency the Maintainer vetoes.
function readShapes(xml) {
  const shapes = [];
  const blocks = xml.split('<p:sp>').slice(1);
  for (const raw of blocks) {
    const block = raw.split('</p:sp>')[0];

    const off = block.match(/<a:off[^/]*\/>/);
    const ext = block.match(/<a:ext[^/]*\/>/);
    if (!off || !ext) continue;
    const x = Number(attr(off[0], 'x')) / EMU_PER_INCH;
    const y = Number(attr(off[0], 'y')) / EMU_PER_INCH;
    const w = Number(attr(ext[0], 'cx')) / EMU_PER_INCH;
    const h = Number(attr(ext[0], 'cy')) / EMU_PER_INCH;

    const xfrm = block.match(/<a:xfrm[^>]*>/);
    const rot = xfrm ? Number(attr(xfrm[0], 'rot') || 0) : 0;

    const bodyPr = block.match(/<a:bodyPr[^>]*>/);
    const ins = {
      l: bodyPr && attr(bodyPr[0], 'lIns') !== null ? Number(attr(bodyPr[0], 'lIns')) : DEFAULT_INS.l,
      r: bodyPr && attr(bodyPr[0], 'rIns') !== null ? Number(attr(bodyPr[0], 'rIns')) : DEFAULT_INS.r,
      t: bodyPr && attr(bodyPr[0], 'tIns') !== null ? Number(attr(bodyPr[0], 'tIns')) : DEFAULT_INS.t,
      b: bodyPr && attr(bodyPr[0], 'bIns') !== null ? Number(attr(bodyPr[0], 'bIns')) : DEFAULT_INS.b,
    };

    // Paragraphs, each measured separately then summed, because that is how
    // the text lays out.
    const paras = [];
    const paraBlocks = block.split('<a:p>').slice(1);
    for (const pRaw of paraBlocks) {
      const pBlock = pRaw.split('</a:p>')[0];
      let text = '';
      let size = null;
      let bold = false;
      const runBlocks = pBlock.split('<a:r>').slice(1);
      for (const rRaw of runBlocks) {
        const rBlock = rRaw.split('</a:r>')[0];
        const rPr = rBlock.match(/<a:rPr[^>]*>/);
        const sz = rPr ? attr(rPr[0], 'sz') : null;
        if (sz) size = Math.max(size || 0, Number(sz) / 100);
        if (rPr && attr(rPr[0], 'b') === '1') bold = true;
        const t = rBlock.match(/<a:t>([\s\S]*?)<\/a:t>/);
        if (t) text += unescapeXml(t[1]);
      }
      if (text !== '') paras.push({ text, size: size || 18, bold });
    }

    shapes.push({
      x, y, w, h, rot, ins, paras,
      fills: [...block.matchAll(/srgbClr val="([0-9A-Fa-f]{6})"/g)].map((m) => m[1].toUpperCase()),
      typefaces: [...block.matchAll(/typeface="([^"]*)"/g)].map((m) => m[1]),
    });
  }
  return shapes;
}

// Axis-aligned bounding box of a shape after its own rotation. Office stores
// rotation in 60000ths of a degree, applied about the shape's centre.
function boundingBox(sp) {
  if (!sp.rot) return { x: sp.x, y: sp.y, w: sp.w, h: sp.h };
  const rad = ((sp.rot / 60000) * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const w = sp.w * cos + sp.h * sin;
  const h = sp.w * sin + sp.h * cos;
  const cx = sp.x + sp.w / 2;
  const cy = sp.y + sp.h / 2;
  return { x: cx - w / 2, y: cy - h / 2, w, h };
}

function slideParts(zip) {
  return Object.keys(zip.files)
    .filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))
    .sort((a, b) => {
      const na = Number(a.match(/slide(\d+)\.xml/)[1]);
      const nb = Number(b.match(/slide(\d+)\.xml/)[1]);
      return na - nb;
    });
}

async function validate(file, opts = {}) {
  const report = new LintReport();
  report.checks = [];
  report.stats = { slides: 0, textShapes: 0, maxFill: 0, maxFillWhere: null };

  const pass = (name, detail) => report.checks.push({ name, ok: true, detail });
  const fail = (name, detail) => {
    report.checks.push({ name, ok: false, detail });
    report.error(name, detail);
  };

  // 1. opens
  if (!fs.existsSync(file)) {
    fail('opens', `${file} does not exist`);
    return report;
  }
  const buf = fs.readFileSync(file);
  if (buf.length === 0) {
    fail('opens', `${file} is empty`);
    return report;
  }
  let zip;
  try {
    zip = await JSZip.loadAsync(buf);
  } catch (e) {
    fail('opens', `not a readable Office Open XML package: ${e.message}`);
    return report;
  }
  if (!zip.file('ppt/presentation.xml')) {
    fail('opens', 'no ppt/presentation.xml, so PowerPoint will not open this');
    return report;
  }
  const parts = slideParts(zip);
  if (parts.length === 0) {
    fail('opens', 'package contains no slides');
    return report;
  }
  pass('opens', `${(buf.length / 1024).toFixed(0)}KB package, ${parts.length} slide part(s)`);
  report.stats.slides = parts.length;

  // 2. slide count
  if (opts.expectSlides !== undefined && opts.expectSlides !== null) {
    if (parts.length === opts.expectSlides) {
      pass('slide count', `${parts.length}, as expected`);
    } else {
      fail('slide count', `found ${parts.length}, expected ${opts.expectSlides}`);
    }
  } else {
    report.warn('slide count', 'not checked. Pass --slides N or --spec FILE to check it.');
  }

  // 3, 4, 5, 6, 7 per slide
  const badFonts = new Map();
  const badColours = new Map();
  const overflows = [];
  const nearOverflows = [];
  const outOfBounds = [];

  for (let i = 0; i < parts.length; i += 1) {
    const xml = await zip.file(parts[i]).async('string');
    const shapes = readShapes(xml);
    const slideNo = i + 1;

    for (const tf of [...xml.matchAll(/typeface="([^"]*)"/g)].map((m) => m[1])) {
      if (tf !== theme.FONT && !tf.startsWith('+')) {
        badFonts.set(`${tf} on slide ${slideNo}`, true);
      }
    }
    for (const hex of [...xml.matchAll(/srgbClr val="([0-9A-Fa-f]{6})"/g)].map((m) => m[1].toUpperCase())) {
      if (!theme.ALLOWED_HEX.has(hex)) {
        badColours.set(`#${hex} on slide ${slideNo}`, true);
      }
    }

    shapes.forEach((sp, j) => {
      const label = `slide ${slideNo} shape ${j + 1}`;

      const tol = 0.02;
      // A rotated shape is stored unrotated and spun about its own centre, so
      // its stored box says nothing about where it lands. Rotate the box and
      // check that. Skipping rotated shapes instead let a rotated axis label
      // sit half off the slide and still pass, which LibreOffice then rendered
      // through the middle of the chart it was labelling.
      const box = boundingBox(sp);
      if (box.x < -tol || box.y < -tol
          || box.x + box.w > theme.SLIDE.w + tol || box.y + box.h > theme.SLIDE.h + tol) {
        outOfBounds.push(
          `${label}${sp.rot ? ' (rotated)' : ''} occupies (${box.x.toFixed(2)}, ${box.y.toFixed(2)}) to `
          + `(${(box.x + box.w).toFixed(2)}, ${(box.y + box.h).toFixed(2)})in on a `
          + `${theme.SLIDE.w}x${theme.SLIDE.h}in slide`,
        );
      }

      if (sp.paras.length === 0) return;
      report.stats.textShapes += 1;

      const innerW = sp.w - (sp.ins.l + sp.ins.r) / EMU_PER_INCH;
      const innerH = sp.h - (sp.ins.t + sp.ins.b) / EMU_PER_INCH;
      // Rotated text: the stored width is the along-text dimension already, so
      // the measurement holds without adjustment.
      let needed = 0;
      for (const p of sp.paras) {
        const lines = metrics.lineCount(p.text, Math.max(0.05, innerW), p.size, p.bold);
        needed += metrics.linesHeight(lines, p.size);
      }
      const fill = needed / Math.max(0.05, innerH);
      const first = sp.paras[0].text;
      if (fill > report.stats.maxFill) {
        report.stats.maxFill = fill;
        report.stats.maxFillWhere = `${label}: "${first.slice(0, 40)}"`;
      }
      const detail = `${label}: "${first.slice(0, 48)}" needs ${needed.toFixed(2)}in in ${innerH.toFixed(2)}in `
        + `(${(fill * 100).toFixed(0)}% of the box at ${sp.paras[0].size}pt)`;
      if (fill > theme.OVERFLOW_ERROR + theme.FILL_EPSILON) overflows.push(detail);
      else if (fill >= theme.OVERFLOW_WARN) nearOverflows.push(detail);

      for (const p of sp.paras) {
        checkText(p.text, `slide ${slideNo}`, report);
      }
    });
  }

  // theme fonts
  const themePart = Object.keys(zip.files).find((n) => /^ppt\/theme\/theme\d+\.xml$/.test(n));
  if (themePart) {
    const txml = await zip.file(themePart).async('string');
    const major = txml.match(/<a:majorFont>[\s\S]*?<a:latin typeface="([^"]*)"/);
    const minor = txml.match(/<a:minorFont>[\s\S]*?<a:latin typeface="([^"]*)"/);
    const bad = [];
    if (major && major[1] !== theme.FONT) bad.push(`major font is ${major[1] || '(empty)'}`);
    if (minor && minor[1] !== theme.FONT) bad.push(`minor font is ${minor[1] || '(empty)'}`);
    if (bad.length) {
      fail('theme fonts', `${bad.join(', ')}, expected ${theme.FONT}. Anyone typing into this deck gets the wrong font.`);
    } else {
      pass('theme fonts', `major and minor Latin fonts are ${theme.FONT}`);
    }
  } else {
    report.warn('theme fonts', 'no theme part found in the package');
  }

  if (badFonts.size) fail('fonts', `non-${theme.FONT} typefaces: ${[...badFonts.keys()].join('; ')}`);
  else pass('fonts', `every typeface in every slide is ${theme.FONT}`);

  if (badColours.size) {
    fail('colours', `${badColours.size} colour(s) outside the palette: ${[...badColours.keys()].slice(0, 8).join('; ')}`
      + (badColours.size > 8 ? ` and ${badColours.size - 8} more` : ''));
  } else {
    pass('colours', `every colour is in the palette (navy #${theme.NAVY} plus ${theme.ALLOWED_HEX.size - 1} others)`);
  }

  if (outOfBounds.length) fail('bounds', `${outOfBounds.length} shape(s) outside the slide: ${outOfBounds.slice(0, 5).join('; ')}`);
  else pass('bounds', 'every shape sits inside the slide');

  if (overflows.length) {
    fail('overflow', `${overflows.length} text box(es) overflow:\n      ${overflows.slice(0, 10).join('\n      ')}`);
  } else {
    pass('overflow', `${report.stats.textShapes} text boxes measured, fullest at `
      + `${(report.stats.maxFill * 100).toFixed(0)}% (estimated from Arial metrics, +/-1%)`);
  }
  for (const n of nearOverflows) {
    report.warn('overflow', `close to the edge, ${n}`);
  }

  const writingErrors = report.errors.filter((e) => /em-dash|emoji/.test(e.message));
  if (writingErrors.length === 0) pass('writing', 'no em-dashes, no emojis');

  return report;
}

function printReport(file, report) {
  const out = [];
  out.push(`validate-deck: ${path.basename(file)}`);
  for (const c of report.checks) {
    out.push(`  ${c.ok ? 'PASS' : 'FAIL'}  ${c.name.padEnd(12)} ${c.detail}`);
  }
  if (report.warnings.length) {
    out.push('');
    for (const w of report.warnings) out.push(`  WARN  ${w.where.padEnd(12)} ${w.message}`);
  }
  const otherErrors = report.errors.filter((e) => !report.checks.some((c) => !c.ok && c.name === e.where));
  if (otherErrors.length) {
    out.push('');
    for (const e of otherErrors) out.push(`  FAIL  ${e.where.padEnd(12)} ${e.message}`);
  }
  if (report.placeholders.length) {
    out.push('');
    out.push(`  NOTE  ${String('placeholders').padEnd(12)} ${report.placeholders.length} in the deck. Replace before sending.`);
  }
  out.push('');
  out.push(report.ok ? '  VALID' : `  INVALID, ${report.errors.length} error(s)`);
  return out.join('\n');
}

async function main(argv) {
  const args = argv.slice(2);
  const file = args.find((a) => !a.startsWith('--'));
  if (!file) {
    process.stdout.write('usage: validate-deck.js FILE.pptx [--slides N] [--spec SPEC.md] [--json]\n');
    return 2;
  }
  const opts = {};
  const slidesIdx = args.indexOf('--slides');
  if (slidesIdx !== -1) opts.expectSlides = Number(args[slidesIdx + 1]);
  const specIdx = args.indexOf('--spec');
  if (specIdx !== -1) {
    // The expected count cannot be read off the spec: a slide whose content
    // overflows legitimately becomes two. So the expectation is derived by
    // running the real build into a temporary file and counting what it
    // produced. That makes this check catch a dropped slide, and it also
    // catches a non-deterministic build.
    const os = require('os');
    const { parse } = require('./src/spec/parse');
    const { build } = require('./src/build');
    const spec = parse(fs.readFileSync(args[specIdx + 1], 'utf8'));
    const tmp = path.join(os.tmpdir(), `deckwright-expect-${process.pid}.pptx`);
    const m = await build(spec, tmp);
    try { fs.unlinkSync(tmp); } catch { /* a temp file that will not delete is not a validation failure */ }
    opts.expectSlides = m.slideCount;
  }
  // A manifest written alongside the deck by `deck build` gives an exact count
  // for free. Absent for a foreign deck, which is why it is optional.
  if (opts.expectSlides === undefined) {
    const manifestPath = `${file.replace(/\.pptx$/i, '')}.manifest.json`;
    if (fs.existsSync(manifestPath)) {
      try {
        opts.expectSlides = JSON.parse(fs.readFileSync(manifestPath, 'utf8')).slideCount;
      } catch { /* a corrupt manifest is not an expectation */ }
    }
  }

  const report = await validate(file, opts);
  if (args.includes('--json')) {
    process.stdout.write(`${JSON.stringify({
      file, ok: report.ok, checks: report.checks, errors: report.errors,
      warnings: report.warnings, stats: report.stats,
    }, null, 2)}\n`);
  } else {
    process.stdout.write(`${printReport(file, report)}\n`);
  }
  return report.ok ? 0 : 1;
}

if (require.main === module) {
  main(process.argv).then((code) => process.exit(code)).catch((e) => {
    process.stderr.write(`validate-deck: ${e.stack}\n`);
    process.exit(2);
  });
}

module.exports = { validate, printReport, readShapes, boundingBox };
