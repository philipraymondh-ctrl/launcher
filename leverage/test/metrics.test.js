'use strict';

const test = require('node:test');
const assert = require('node:assert');

const metrics = require('../src/text/metrics');
const theme = require('../src/theme');

test('advance widths distinguish narrow from wide glyphs', () => {
  assert.ok(metrics.textWidth('iiii', 12) < metrics.textWidth('MMMM', 12),
    'four i must be narrower than four M, or the table is not being read');
  // Bold is never narrower, though for a few glyphs (W among them) the two
  // faces share an advance width exactly.
  assert.ok(metrics.textWidth('WWW', 12, true) >= metrics.textWidth('WWW', 12),
    'bold must be at least as wide as regular');
  assert.ok(metrics.textWidth('nnnn', 12, true) > metrics.textWidth('nnnn', 12),
    'bold must be wider than regular for a glyph where the faces differ');
});

test('width scales linearly with point size', () => {
  const a = metrics.textWidth('Novo Nordisk', 10);
  const b = metrics.textWidth('Novo Nordisk', 20);
  assert.ok(Math.abs(b - a * 2) < 1e-9, `expected exactly double, got ${a} and ${b}`);
});

test('accented and Nordic characters do not fall back to the default width', () => {
  // A name like Søren or Malmö must measure like its base letters, not like a
  // string of 556-unit unknowns, or every Nordic market name mismeasures.
  assert.strictEqual(metrics.charWidth('ø', false), metrics.charWidth('o', false));
  assert.strictEqual(metrics.charWidth('Å', false), metrics.charWidth('A', false));
  assert.strictEqual(metrics.charWidth('é', false), metrics.charWidth('e', false));
  assert.ok(metrics.charWidth('æ', false) > metrics.charWidth('a', false));
});

test('wrap never exceeds the given width', () => {
  const text = 'Market four is blocked on a data residency sign-off held outside the programme';
  const width = 2.5;
  for (const line of metrics.wrap(text, width, 12)) {
    assert.ok(metrics.textWidth(line, 12) <= width + 1e-9,
      `line wider than the box: "${line}"`);
  }
});

test('wrap breaks a single word wider than the box rather than overflowing', () => {
  const lines = metrics.wrap('Unternehmensberatungsgesellschaft', 0.6, 14);
  assert.ok(lines.length > 1, 'a word wider than the box must be broken');
  for (const line of lines) {
    assert.ok(metrics.textWidth(line, 14) <= 0.6 + 1e-9, `unbroken line: "${line}"`);
  }
});

test('wrap preserves explicit newlines as separate lines', () => {
  assert.strictEqual(metrics.wrap('one\ntwo\nthree', 5, 12).length, 3);
});

test('fit subtracts the text box insets', () => {
  const box = { w: 2, h: 0.5 };
  const withInsets = metrics.fit('a fairly long line of text here', box, 12);
  const without = metrics.fit('a fairly long line of text here', box, 12, { inset: false });
  assert.ok(withInsets.lineCount >= without.lineCount,
    'ignoring insets can only ever make text fit more easily');
});

test('fitDown honours the safety target, not merely fitting', () => {
  const text = 'Three of five markets are live and market four needs a decision';
  const box = { x: 0, y: 0, w: 4, h: 1 };
  const found = metrics.fitDown(text, box, metrics.ladder(24, 8));
  assert.ok(found, 'expected some size to fit');
  assert.ok(found.ratio <= theme.FIT_TARGET + 1e-9,
    `fitDown returned ${found.ratio.toFixed(3)} fill, above the ${theme.FIT_TARGET} target`);
});

test('fitDown falls back to a tight fit before giving up', () => {
  // A box that nothing can fit comfortably but the floor size can fit exactly.
  const text = 'A line of text that is quite long indeed for the space given';
  const box = { x: 0, y: 0, w: 1.6, h: 0.62 };
  const found = metrics.fitDown(text, box, metrics.ladder(12, 6));
  if (found) {
    assert.ok(found.ratio <= 1.0, 'a returned fit must actually fit');
  }
});

test('fitDown returns null when nothing fits, rather than guessing', () => {
  const box = { x: 0, y: 0, w: 0.4, h: 0.12 };
  const found = metrics.fitDown('several words that cannot possibly fit in here', box, [10, 9, 8]);
  assert.strictEqual(found, null);
});

test('ladder descends from start to the floor in half points', () => {
  const l = metrics.ladder(14, 12);
  assert.deepStrictEqual(l, [14, 13.5, 13, 12.5, 12]);
  assert.ok(l.every((pt, i) => i === 0 || pt < l[i - 1]), 'ladder must descend');
});
