'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { lintSpec } = require('../src/lint');
const { parse } = require('../src/spec/parse');

function lint(src) {
  return lintSpec(parse(src));
}

test('an em-dash anywhere in a spec is an error that names the field', () => {
  const r = lint('## bullets: T\n- Three live — one blocked\n');
  assert.ok(!r.ok);
  assert.match(r.errors[0].where, /slide 1 \(bullets\) bullet 1/);
  assert.match(r.errors[0].message, /em-dash/);
});

test('an em-dash in a slide title is caught too', () => {
  const r = lint('## bullets: Status — July\n- a\n');
  assert.ok(!r.ok);
  assert.match(r.errors[0].where, /title/);
});

test('an em-dash in deck meta is caught', () => {
  const r = lint('# Rollout — phase two\nsubtitle: x\n\n## bullets: T\n- a\n');
  assert.ok(!r.ok);
  assert.ok(r.errors.some((e) => /deck\./.test(e.where)));
});

test('an en-dash warns rather than failing, because a date range is legitimate', () => {
  const r = lint('## bullets: T\n- Q3–Q4 delivery\n');
  assert.ok(r.ok, 'an en-dash must not fail the build');
  assert.ok(r.warnings.some((w) => /en-dash/.test(w.message)));
});

test('an emoji is an error', () => {
  const r = lint('## bullets: T\n- Status \u{1F7E2} green\n');
  assert.ok(!r.ok);
  assert.match(r.errors[0].message, /emoji|pictograph/);
});

test('a summary slide with no verdict fails, because the verdict is the slide', () => {
  const r = lint('## summary: Where we stand\n- one\n- two\n');
  assert.ok(!r.ok);
  assert.match(r.errors[0].message, /verdict/);
});

test('a summary slide with a verdict passes', () => {
  const r = lint('## summary: Where we stand\nverdict: Market four needs a decision.\n- one\n');
  assert.ok(r.ok, JSON.stringify(r.errors));
});

test('placeholders are recorded but do not fail the build', () => {
  const r = lint('## metric: PLACEHOLDER\nlabel: PLACEHOLDER users\n');
  assert.ok(r.ok);
  assert.strictEqual(r.placeholders.length, 2);
});

test('table cells and group items are linted, not just bullets', () => {
  const cell = lint('## raid: R\n| Risk | slipping — badly | Owner | Red |\n');
  assert.ok(!cell.ok);
  assert.match(cell.errors[0].where, /row 1 cell 2/);

  const group = lint('## columns: C\n### Live — mostly\n- a\n');
  assert.ok(!group.ok);
  assert.match(group.errors[0].where, /group 1 heading/);
});

test('ordinary punctuation is left alone', () => {
  const r = lint([
    '## bullets: T',
    '- Ask: approve the exception, or accept the slip',
    '- Cost is 1,100 EUR per market (excluding licences)',
    "- The sponsor's view is that it can wait",
    '- Sign-off is pending',
  ].join('\n'));
  assert.ok(r.ok, JSON.stringify(r.errors));
  assert.strictEqual(r.warnings.length, 0);
});
