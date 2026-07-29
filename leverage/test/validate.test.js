'use strict';

// The most important file in the suite. A validator that cannot fail is not a
// validator, so this builds decks that are deliberately wrong in each of the
// ways the standards forbid and asserts that the named check catches each one.
//
// If any of these tests ever passes a bad deck, everything else in this repo
// that claims a deck is correct becomes worthless.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const PptxGenJS = require('pptxgenjs');

const { validate } = require('../validate-deck');
const { build } = require('../src/build');
const { parse } = require('../src/spec/parse');
const theme = require('../src/theme');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'deckwright-test-'));

function tmpFile(name) {
  return path.join(TMP, name);
}

function checkNamed(report, name) {
  const c = report.checks.find((x) => x.name === name);
  assert.ok(c, `no check named "${name}" ran. Checks: ${report.checks.map((x) => x.name).join(', ')}`);
  return c;
}

// A deck that is wrong in exactly one way, so a failure is attributable.
async function brokenDeck(name, mutate) {
  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.theme = { headFontFace: theme.FONT, bodyFontFace: theme.FONT };
  const slide = pptx.addSlide();
  slide.addText('A correct line', {
    x: 1, y: 1, w: 6, h: 0.6, fontFace: theme.FONT, fontSize: 14, color: theme.PALETTE.ink,
  });
  mutate(slide, pptx);
  const file = tmpFile(`${name}.pptx`);
  await pptx.writeFile({ fileName: file });
  return file;
}

test('a deck built from a spec validates clean', async () => {
  const spec = parse(fs.readFileSync(path.join(__dirname, '..', 'specs', 'steering-review.md'), 'utf8'));
  const out = tmpFile('good.pptx');
  const manifest = await build(spec, out);
  const report = await validate(out, { expectSlides: manifest.slideCount });
  assert.ok(report.ok, `expected VALID, got errors:\n${report.errors.map((e) => `${e.where}: ${e.message}`).join('\n')}`);
  for (const name of ['opens', 'slide count', 'theme fonts', 'fonts', 'colours', 'bounds', 'overflow', 'writing']) {
    assert.ok(checkNamed(report, name).ok, `${name} did not pass on a good deck`);
  }
});

test('a missing file fails the opens check rather than throwing', async () => {
  const report = await validate(tmpFile('does-not-exist.pptx'));
  assert.ok(!report.ok);
  assert.ok(!checkNamed(report, 'opens').ok);
});

test('a file that is not a zip fails the opens check', async () => {
  const file = tmpFile('not-a-deck.pptx');
  fs.writeFileSync(file, 'this is not an Office package');
  const report = await validate(file);
  assert.ok(!report.ok);
  assert.ok(!checkNamed(report, 'opens').ok);
});

test('an empty file fails the opens check', async () => {
  const file = tmpFile('empty.pptx');
  fs.writeFileSync(file, '');
  const report = await validate(file);
  assert.ok(!report.ok);
});

test('the wrong slide count fails', async () => {
  const file = await brokenDeck('count', () => {});
  const report = await validate(file, { expectSlides: 7 });
  assert.ok(!checkNamed(report, 'slide count').ok);
  assert.match(checkNamed(report, 'slide count').detail, /found 1, expected 7/);
});

test('a non-Arial run font fails the fonts check', async () => {
  const file = await brokenDeck('font', (slide) => {
    slide.addText('Calibri crept in', {
      x: 1, y: 3, w: 5, h: 0.5, fontFace: 'Calibri', fontSize: 14, color: theme.PALETTE.ink,
    });
  });
  const report = await validate(file);
  assert.ok(!report.ok);
  const c = checkNamed(report, 'fonts');
  assert.ok(!c.ok);
  assert.match(c.detail, /Calibri/);
});

test('a default theme font fails even when every run is Arial', async () => {
  // This is the leak that matters: every visible run is Arial, but anyone who
  // types into the deck later gets Calibri.
  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.theme = { headFontFace: 'Calibri Light', bodyFontFace: 'Calibri' };
  const slide = pptx.addSlide();
  slide.addText('All runs are Arial', {
    x: 1, y: 1, w: 6, h: 0.6, fontFace: theme.FONT, fontSize: 14, color: theme.PALETTE.ink,
  });
  const file = tmpFile('theme-font.pptx');
  await pptx.writeFile({ fileName: file });
  const report = await validate(file);
  assert.ok(!report.ok, 'a Calibri theme must fail');
  assert.ok(!checkNamed(report, 'theme fonts').ok);
});

test('a colour outside the palette fails the colours check', async () => {
  const file = await brokenDeck('colour', (slide, pptx) => {
    slide.addShape(pptx.ShapeType.rect, {
      x: 1, y: 3, w: 2, h: 1, fill: { color: 'FF0000' }, line: { type: 'none' },
    });
  });
  const report = await validate(file);
  assert.ok(!report.ok);
  const c = checkNamed(report, 'colours');
  assert.ok(!c.ok);
  assert.match(c.detail, /FF0000/i);
});

test('text that overflows its box fails the overflow check', async () => {
  const file = await brokenDeck('overflow', (slide) => {
    slide.addText(
      'This is a great deal of text for a very small box indeed, and it will not fit however '
      + 'generously the estimator rounds, which is the entire point of measuring it.',
      { x: 1, y: 3, w: 1.5, h: 0.4, fontFace: theme.FONT, fontSize: 18, color: theme.PALETTE.ink },
    );
  });
  const report = await validate(file);
  assert.ok(!report.ok);
  const c = checkNamed(report, 'overflow');
  assert.ok(!c.ok);
  assert.match(c.detail, /overflow/);
});

test('a shape off the slide fails the bounds check', async () => {
  const file = await brokenDeck('bounds', (slide, pptx) => {
    slide.addShape(pptx.ShapeType.rect, {
      x: 12.5, y: 1, w: 3, h: 1, fill: { color: theme.PALETTE.navy }, line: { type: 'none' },
    });
  });
  const report = await validate(file);
  assert.ok(!report.ok);
  assert.ok(!checkNamed(report, 'bounds').ok);
});

test('a rotated shape off the slide fails the bounds check', async () => {
  // A rotated shape's stored box is not where it appears, so the check has to
  // rotate the box first. This one is 8in wide and turned on its side, so it
  // runs off a 7.5in-tall slide even though its stored box fits fine.
  const file = await brokenDeck('rot-bounds', (slide) => {
    slide.addText('An axis label far too long for the slide it is on', {
      x: 1, y: 3.5, w: 8, h: 0.3, rotate: 270,
      fontFace: theme.FONT, fontSize: 10, color: theme.PALETTE.navy,
    });
  });
  const report = await validate(file);
  assert.ok(!report.ok, 'a rotated shape hanging off the slide must fail');
  const c = checkNamed(report, 'bounds');
  assert.ok(!c.ok);
  assert.match(c.detail, /rotated/);
});

test('a rotated shape that lands on the slide passes the bounds check', async () => {
  const file = await brokenDeck('rot-ok', (slide) => {
    slide.addText('Influence', {
      x: 0.2, y: 3, w: 1.5, h: 0.3, rotate: 270,
      fontFace: theme.FONT, fontSize: 10, color: theme.PALETTE.navy,
    });
  });
  const report = await validate(file);
  assert.ok(checkNamed(report, 'bounds').ok,
    `a correctly placed rotated label must not be flagged: ${checkNamed(report, 'bounds').detail}`);
});

test('an em-dash in the written file fails', async () => {
  const file = await brokenDeck('emdash', (slide) => {
    slide.addText('Three markets are live — market four is blocked', {
      x: 1, y: 3, w: 8, h: 0.5, fontFace: theme.FONT, fontSize: 14, color: theme.PALETTE.ink,
    });
  });
  const report = await validate(file);
  assert.ok(!report.ok, 'an em-dash must fail validation');
  assert.ok(report.errors.some((e) => /em-dash/.test(e.message)));
});

test('an emoji in the written file fails', async () => {
  const file = await brokenDeck('emoji', (slide) => {
    slide.addText('Status \u{1F600} green', {
      x: 1, y: 3, w: 8, h: 0.5, fontFace: theme.FONT, fontSize: 14, color: theme.PALETTE.ink,
    });
  });
  const report = await validate(file);
  assert.ok(!report.ok, 'an emoji must fail validation');
  assert.ok(report.errors.some((e) => /emoji/.test(e.message)));
});

test('validation works on a deck it did not build', async () => {
  // Idea 2 from IDEAS.md, absorbed here: point it at a foreign deck. No
  // manifest, no spec, and it still reports on fonts, colours and overflow.
  const pptx = new PptxGenJS();
  const slide = pptx.addSlide();
  slide.addText('An inherited slide with default everything', { x: 1, y: 1, w: 6, h: 1 });
  const file = tmpFile('foreign.pptx');
  await pptx.writeFile({ fileName: file });
  const report = await validate(file);
  assert.ok(report.checks.length >= 6, 'a foreign deck must still be checked');
  const counted = report.warnings.find((w) => w.where === 'slide count');
  assert.ok(counted, 'with no spec and no manifest, slide count should warn rather than fail');
});

test('a deck with no expectation warns about the unchecked slide count', async () => {
  const file = await brokenDeck('nocount', () => {});
  const report = await validate(file);
  assert.ok(report.warnings.some((w) => w.where === 'slide count'));
  assert.ok(report.ok, 'an unchecked count is a warning, not an error');
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
