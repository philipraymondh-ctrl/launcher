'use strict';

// The CLI is the interface a human touches, so it gets tested as a process:
// real argv, real exit codes, real stdout. Every other test in this suite calls
// the library directly and would not notice a CLI that always exits 0.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const DECK = path.join(ROOT, 'bin', 'deck.js');
const VALIDATE = path.join(ROOT, 'validate-deck.js');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'deckwright-cli-'));

function run(script, args, opts = {}) {
  return spawnSync(process.execPath, [script, ...args], {
    encoding: 'utf8', cwd: opts.cwd || ROOT, timeout: 120000,
  });
}

const GOOD_SPEC = [
  '# A programme',
  'subtitle: A review',
  '',
  '## summary: Status',
  'verdict: One sentence that is the verdict.',
  '- a point',
  '- another point',
  '',
  '## metric: 42',
  'label: Things counted',
].join('\n');

test('build exits 0 and writes a deck, a manifest, and a validation report', () => {
  const spec = path.join(TMP, 'good.md');
  fs.writeFileSync(spec, GOOD_SPEC);
  const out = path.join(TMP, 'good.pptx');
  const r = run(DECK, ['build', spec, '-o', out]);
  assert.strictEqual(r.status, 0, `expected exit 0, got ${r.status}:\n${r.stdout}${r.stderr}`);
  assert.ok(fs.existsSync(out), 'no deck written');
  assert.ok(fs.existsSync(path.join(TMP, 'good.manifest.json')), 'no manifest written');
  assert.match(r.stdout, /VALID/);
  assert.match(r.stdout, /3 slide\(s\)/, 'implicit title slide should bring this to 3');
});

test('build creates the output directory when it does not exist', () => {
  const spec = path.join(TMP, 'mkdir.md');
  fs.writeFileSync(spec, GOOD_SPEC);
  const out = path.join(TMP, 'nested', 'deeper', 'deck.pptx');
  const r = run(DECK, ['build', spec, '-o', out]);
  assert.strictEqual(r.status, 0, r.stdout + r.stderr);
  assert.ok(fs.existsSync(out));
});

test('build on a missing spec exits 2 and says which file', () => {
  const r = run(DECK, ['build', path.join(TMP, 'nope.md')]);
  assert.strictEqual(r.status, 2);
  assert.match(r.stderr, /no such spec file/);
});

test('build on a spec with a bad slide type exits 2 with a line number', () => {
  const spec = path.join(TMP, 'badtype.md');
  fs.writeFileSync(spec, '## summry: Status\n- a\n');
  const r = run(DECK, ['build', spec, '-o', path.join(TMP, 'badtype.pptx')]);
  assert.strictEqual(r.status, 2);
  assert.match(r.stderr, /line 1/);
  assert.match(r.stderr, /unknown slide type/);
});

test('build on a spec that breaks the writing standards exits 1 and writes nothing', () => {
  const spec = path.join(TMP, 'emdash.md');
  fs.writeFileSync(spec, '## bullets: Status\n- Three live — one blocked\n');
  const out = path.join(TMP, 'emdash.pptx');
  const r = run(DECK, ['build', spec, '-o', out]);
  assert.strictEqual(r.status, 1);
  assert.match(r.stderr, /em-dash/);
  assert.ok(!fs.existsSync(out), 'a rejected spec must leave no deck behind');
});

test('build reports the placeholder count so a deck cannot be sent with them in', () => {
  const spec = path.join(TMP, 'ph.md');
  fs.writeFileSync(spec, '## metric: PLACEHOLDER\nlabel: PLACEHOLDER users\n');
  const r = run(DECK, ['build', spec, '-o', path.join(TMP, 'ph.pptx')]);
  assert.strictEqual(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /2 PLACEHOLDER\(s\)/);
  assert.match(r.stdout, /Nothing here was invented/);
});

test('validate exits 0 on a good deck and 1 on a broken one', () => {
  const spec = path.join(TMP, 'v.md');
  fs.writeFileSync(spec, GOOD_SPEC);
  const out = path.join(TMP, 'v.pptx');
  assert.strictEqual(run(DECK, ['build', spec, '-o', out]).status, 0);

  const ok = run(VALIDATE, [out]);
  assert.strictEqual(ok.status, 0, ok.stdout);
  assert.match(ok.stdout, /VALID/);

  const junk = path.join(TMP, 'junk.pptx');
  fs.writeFileSync(junk, 'not a deck');
  const bad = run(VALIDATE, [junk]);
  assert.strictEqual(bad.status, 1);
  assert.match(bad.stdout, /INVALID/);
});

test('validate --slides catches the wrong count', () => {
  const out = path.join(TMP, 'v.pptx');
  const r = run(VALIDATE, [out, '--slides', '9']);
  assert.strictEqual(r.status, 1);
  assert.match(r.stdout, /found 3, expected 9/);
});

test('validate --spec derives the expected count by building the spec', () => {
  const spec = path.join(TMP, 'v.md');
  const out = path.join(TMP, 'v.pptx');
  const r = run(VALIDATE, [out, '--spec', spec]);
  assert.strictEqual(r.status, 0, r.stdout);
  assert.match(r.stdout, /slide count\s+3, as expected/);
});

test('validate --json emits parseable JSON with the checks in it', () => {
  const out = path.join(TMP, 'v.pptx');
  const r = run(VALIDATE, [out, '--json']);
  assert.strictEqual(r.status, 0);
  const parsed = JSON.parse(r.stdout);
  assert.strictEqual(parsed.ok, true);
  assert.ok(parsed.checks.some((c) => c.name === 'overflow'));
  assert.ok(parsed.stats.textShapes > 0);
});

test('deck validate delegates to validate-deck.js and preserves its exit code', () => {
  const junk = path.join(TMP, 'junk.pptx');
  const r = run(DECK, ['validate', junk]);
  assert.strictEqual(r.status, 1);
});

test('lint exits 1 on a bad spec and 0 on a good one, without building', () => {
  const bad = path.join(TMP, 'lintbad.md');
  fs.writeFileSync(bad, '## summary: Status\n- no verdict here\n');
  const r = run(DECK, ['lint', bad]);
  assert.strictEqual(r.status, 1);
  assert.match(r.stdout, /verdict/);
  assert.ok(!fs.existsSync(path.join(TMP, 'lintbad.pptx')));

  const good = path.join(TMP, 'lintgood.md');
  fs.writeFileSync(good, GOOD_SPEC);
  const ok = run(DECK, ['lint', good]);
  assert.strictEqual(ok.status, 0);
  assert.match(ok.stdout, /writing standards met/);
});

test('new writes a starter spec that itself builds and validates', () => {
  const spec = path.join(TMP, 'starter.md');
  const r = run(DECK, ['new', '-o', spec]);
  assert.strictEqual(r.status, 0);
  assert.ok(fs.existsSync(spec));
  // A starter that does not compile is a trap, not a starter.
  const built = run(DECK, ['build', spec, '-o', path.join(TMP, 'starter.pptx')]);
  assert.strictEqual(built.status, 0, built.stdout + built.stderr);
  assert.match(built.stdout, /VALID/);
});

test('new with no -o prints the starter to stdout', () => {
  const r = run(DECK, ['new']);
  assert.strictEqual(r.status, 0);
  assert.match(r.stdout, /^# PLACEHOLDER/);
});

test('components lists every slide type the parser accepts', () => {
  const r = run(DECK, ['components']);
  assert.strictEqual(r.status, 0);
  const { TYPES } = require('../src/spec/parse');
  for (const t of TYPES) {
    assert.match(r.stdout, new RegExp(`\\b${t}\\b`), `components did not mention "${t}"`);
  }
});

test('no arguments prints usage and exits 0, an unknown command exits 2', () => {
  const none = run(DECK, []);
  assert.strictEqual(none.status, 0);
  assert.match(none.stdout, /a spec compiler for slides/);

  const bogus = run(DECK, ['frobnicate']);
  assert.strictEqual(bogus.status, 2);
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
