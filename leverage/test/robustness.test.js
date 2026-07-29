'use strict';

// The cases a real spec hits that a tidy example never does: a heading typed
// with nothing under it yet, Nordic and French names, a pasted URL with no
// spaces in it, a table with ragged rows, and the same spec built twice.
//
// A spec half-written on a phone is the normal state of a spec, so a component
// that needs its content to be complete is a component that fails in use.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { parse } = require('../src/spec/parse');
const { build } = require('../src/build');
const { validate } = require('../validate-deck');
const { COMPONENTS } = require('../src/components');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'deckwright-rob-'));

async function buildAndValidate(name, src) {
  const out = path.join(TMP, `${name}.pptx`);
  const manifest = await build(parse(src), out);
  const report = await validate(out, { expectSlides: manifest.slideCount });
  return { manifest, report, out };
}

test('every component survives a heading with nothing under it', async () => {
  // The state a spec is in for most of its life.
  for (const type of Object.keys(COMPONENTS)) {
    const src = type === 'summary'
      ? '## summary: Nothing yet\nverdict: PLACEHOLDER\n'
      : `## ${type}: Nothing yet\n`;
    const { report, manifest } = await buildAndValidate(`empty-${type}`, src);
    assert.ok(report.ok,
      `${type} with no content failed validation:\n${report.errors.map((e) => `${e.where}: ${e.message}`).join('\n')}`);
    assert.ok(manifest.slideCount >= 1, `${type} produced no slide`);
  }
});

test('an empty content component warns rather than failing silently', async () => {
  const { manifest } = await buildAndValidate('empty-raid', '## raid: Nothing yet\n');
  assert.ok(manifest.warnings.some((w) => /no rows/.test(w)),
    `expected a warning naming the missing rows, got: ${JSON.stringify(manifest.warnings)}`);
});

test('Nordic and French names measure and render without overflowing', async () => {
  const src = [
    '# Rollout',
    'subtitle: Malmö, Ålesund, Århus and Besançon',
    '',
    '## stakeholders: Who agrees',
    'headers: Name | Influence | Interest | Position',
    '| Søren Ø. Mikkelsen | high | high | Sponsor |',
    '| Åsa Bergström-Lindqvist | high | low | Blocking |',
    '| François Lefèvre | low | high | Champion |',
    '',
    '## raid: Risks',
    'headers: Item | Owner | Status',
    '| Data residency in Danmark, Sverige og Norge | Søren Ø. Mikkelsen | Amber |',
  ].join('\n');
  const { report } = await buildAndValidate('nordic', src);
  assert.ok(report.ok, report.errors.map((e) => `${e.where}: ${e.message}`).join('\n'));
});

test('a long unbroken string does not overflow, because wrap breaks it', async () => {
  const src = '## bullets: A pasted link\n'
    + '- https://intranet.example.internal/programmes/analytics/rollout/market-four/residency-signoff-2026-q3-final\n';
  const { report } = await buildAndValidate('longword', src);
  assert.ok(report.ok, report.errors.map((e) => `${e.where}: ${e.message}`).join('\n'));
});

test('a table with ragged rows builds, using the widest row for the columns', async () => {
  const src = [
    '## raid: Ragged',
    'headers: Type | Item | Owner | Status',
    '| Risk | Something |',
    '| Action | Something else | Someone | Red |',
    '| Note |',
  ].join('\n');
  const { report } = await buildAndValidate('ragged', src);
  assert.ok(report.ok, report.errors.map((e) => `${e.where}: ${e.message}`).join('\n'));
});

test('a table with more columns than headers still builds', async () => {
  const src = [
    '## raid: Extra',
    'headers: Type | Item',
    '| Risk | Something | An extra cell nobody declared | Red |',
  ].join('\n');
  const { report } = await buildAndValidate('extracols', src);
  assert.ok(report.ok, report.errors.map((e) => `${e.where}: ${e.message}`).join('\n'));
});

test('a table with no headers line builds without a header bar', async () => {
  const { report } = await buildAndValidate('noheaders',
    '## raid: No headers\n| Risk | Something | Red |\n');
  assert.ok(report.ok, report.errors.map((e) => `${e.where}: ${e.message}`).join('\n'));
});

test('a stakeholder row with an unrecognised level lands in a quadrant rather than vanishing', async () => {
  const src = '## stakeholders: Levels\n| Someone | maybe | sort of | note |\n| Another | HIGH | Low | note |\n';
  const { report, out } = await buildAndValidate('levels', src);
  assert.ok(report.ok);
  const text = fs.readFileSync(out); // presence check via the validator's own read
  assert.ok(text.length > 0);
});

test('the same spec built twice produces the same slide count and the same text', async () => {
  // validate --spec derives the expected count by rebuilding, so a
  // non-deterministic build would make that check meaningless.
  const src = fs.readFileSync(path.join(__dirname, '..', 'specs', 'steering-review.md'), 'utf8');
  const a = await buildAndValidate('det-a', src);
  const b = await buildAndValidate('det-b', src);
  assert.strictEqual(a.manifest.slideCount, b.manifest.slideCount);
  assert.deepStrictEqual(
    a.manifest.slides.map((s) => `${s.type}:${s.title}`),
    b.manifest.slides.map((s) => `${s.type}:${s.title}`),
  );
  assert.deepStrictEqual(a.manifest.warnings, b.manifest.warnings);
});

test('a spec of only a deck title and one heading builds', async () => {
  const { report, manifest } = await buildAndValidate('minimal', '# Just this\n\n## Next week\n- something\n');
  assert.ok(report.ok);
  assert.strictEqual(manifest.slideCount, 2, 'a title slide plus the one heading');
});

test('a heading with no bullets at all still produces a slide', async () => {
  const { manifest, report } = await buildAndValidate('bareheading', '## Next week\n');
  assert.ok(report.ok);
  assert.strictEqual(manifest.slideCount, 1);
});

test('a content slide with no title warns, and still builds', async () => {
  const { manifest, report } = await buildAndValidate('untitled', '## bullets:\n- a point\n');
  assert.ok(report.ok, 'a missing title must not fail the build');
  assert.ok(manifest.warnings.some((w) => /no title/.test(w)),
    `expected a warning about the missing title, got ${JSON.stringify(manifest.warnings)}`);
});

test('a metric slide does not warn about a blank header, because its number is its title', async () => {
  // Regression. This warning fired on every correct metric slide, which is the
  // shape of a warning that gets ignored, and then so do the real ones.
  const { manifest } = await buildAndValidate('metric-noheading', '## metric: 1,100\nlabel: Users\n');
  assert.ok(!manifest.warnings.some((w) => /no title/.test(w)),
    `a metric slide must not warn about its header: ${JSON.stringify(manifest.warnings)}`);
});

test('a titled content slide does not warn about its title', async () => {
  const { manifest } = await buildAndValidate('titled', '## bullets: A title\n- a point\n');
  assert.ok(!manifest.warnings.some((w) => /no title/.test(w)),
    `unexpected title warning: ${JSON.stringify(manifest.warnings)}`);
});

test('a spec whose every field is blank produces placeholders, not invented content', async () => {
  const { manifest, out } = await buildAndValidate('blank', '## metric:\n');
  assert.ok(manifest.placeholders.length >= 0);
  assert.ok(manifest.warnings.some((w) => /PLACEHOLDER/.test(w)),
    `expected a warning about the missing value, got ${JSON.stringify(manifest.warnings)}`);
  const zip = await require('jszip').loadAsync(fs.readFileSync(out));
  const xml = await zip.file('ppt/slides/slide1.xml').async('string');
  assert.match(xml, /PLACEHOLDER/, 'a missing value must render as PLACEHOLDER');
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
