'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { parse, SpecError, TYPES } = require('../src/spec/parse');

test('a heading with no type becomes a bullets slide', () => {
  const spec = parse('## What we need\n- one\n- two\n');
  assert.strictEqual(spec.slides.length, 1);
  assert.strictEqual(spec.slides[0].type, 'bullets');
  assert.strictEqual(spec.slides[0].title, 'What we need');
  assert.deepStrictEqual(spec.slides[0].bullets, ['one', 'two']);
});

test('a "#" line sets the deck title and adds an implicit title slide', () => {
  const spec = parse('# Rollout\nsubtitle: Review\n\n## summary: Status\nverdict: Fine\n');
  assert.strictEqual(spec.meta.title, 'Rollout');
  assert.strictEqual(spec.slides.length, 2);
  assert.strictEqual(spec.slides[0].type, 'title');
  assert.strictEqual(spec.slides[0].implicit, true);
  assert.strictEqual(spec.slides[0].fields.subtitle, 'Review');
});

test('titleslide: no suppresses the implicit title slide', () => {
  const spec = parse('# Rollout\ntitleslide: no\n\n## summary: Status\nverdict: Fine\n');
  assert.strictEqual(spec.slides.length, 1);
  assert.strictEqual(spec.slides[0].type, 'summary');
});

test('a headers line whose value contains pipes is a field, not a table row', () => {
  // Regression. Reading it as a row cost the table its header bar and put the
  // literal text "headers: Type" on the slide, and it validated clean.
  const spec = parse([
    '## raid: Risks',
    'headers: Type | Item | Owner | Status',
    '| Risk | Something | Someone | Red |',
  ].join('\n'));
  const slide = spec.slides[0];
  assert.deepStrictEqual(slide.headers, ['Type', 'Item', 'Owner', 'Status']);
  assert.strictEqual(slide.rows.length, 1);
  assert.deepStrictEqual(slide.rows[0], ['Risk', 'Something', 'Someone', 'Red']);
});

test('a row starting with a pipe stays a row even when a cell contains a colon', () => {
  const spec = parse('## raid: Risks\n| Risk: residency | Blocked | Owner | Red |\n');
  assert.deepStrictEqual(spec.slides[0].rows[0], ['Risk: residency', 'Blocked', 'Owner', 'Red']);
  assert.strictEqual(spec.slides[0].headers, null);
});

test('a markdown table separator row is dropped', () => {
  const spec = parse('## raid: Risks\n| A | B |\n|---|---|\n| c | d |\n');
  assert.strictEqual(spec.slides[0].rows.length, 2);
});

test('a "###" group splits a leading label on the pipe', () => {
  const spec = parse('## roadmap: Phases\n### Q3 2026 | Foundations\n- contracts\n');
  const g = spec.slides[0].groups[0];
  assert.strictEqual(g.label, 'Q3 2026');
  assert.strictEqual(g.name, 'Foundations');
  assert.deepStrictEqual(g.items, ['contracts']);
});

test('a "###" group with no pipe has a name and no label', () => {
  const spec = parse('## columns: Two speeds\n### Live\n- one\n');
  assert.strictEqual(spec.slides[0].groups[0].name, 'Live');
  assert.strictEqual(spec.slides[0].groups[0].label, '');
});

test('bullets after a group belong to that group, not to the slide', () => {
  const spec = parse('## columns: T\n- loose\n### A\n- inside\n');
  assert.deepStrictEqual(spec.slides[0].bullets, ['loose']);
  assert.deepStrictEqual(spec.slides[0].groups[0].items, ['inside']);
});

test('keys are case and space insensitive', () => {
  const spec = parse('## metric: 12\nLabel: Users\n  Note : source\n');
  assert.strictEqual(spec.slides[0].fields.label, 'Users');
});

test('free text becomes the slide text field', () => {
  const spec = parse('## bullets: T\nThis is a sentence with no marker.\n- a\n');
  assert.strictEqual(spec.slides[0].fields.text, 'This is a sentence with no marker.');
});

test('an unknown slide type errors with a line number and lists the valid types', () => {
  assert.throws(() => parse('## summry: Status\n- a\n'), (e) => {
    assert.ok(e instanceof SpecError);
    assert.strictEqual(e.line, 1);
    assert.match(e.message, /unknown slide type "summry"/);
    for (const t of ['summary', 'roadmap', 'raid']) assert.ok(e.message.includes(t));
    return true;
  });
});

test('a title containing a colon is not mistaken for a type', () => {
  const spec = parse('## Decision needed: market four\n- a\n');
  assert.strictEqual(spec.slides[0].type, 'bullets');
  assert.strictEqual(spec.slides[0].title, 'Decision needed: market four');
});

test('content before any slide errors rather than being silently dropped', () => {
  assert.throws(() => parse('- an orphan bullet\n'), /line 1/);
  assert.throws(() => parse('## a: b\n'), SpecError);
});

test('an empty spec errors', () => {
  assert.throws(() => parse('\n\n'), /no slides/);
});

test('every declared type is accepted by the parser', () => {
  for (const t of TYPES) {
    const spec = parse(`## ${t}: Something\n- a\n`);
    assert.strictEqual(spec.slides[spec.slides.length - 1].type, t, `type ${t} did not parse`);
  }
});

test('windows line endings and trailing whitespace parse the same', () => {
  const spec = parse('## bullets: T   \r\n- one  \r\n');
  assert.deepStrictEqual(spec.slides[0].bullets, ['one']);
  assert.strictEqual(spec.slides[0].title, 'T');
});
