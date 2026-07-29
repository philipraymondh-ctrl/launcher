'use strict';

// Every component, twice: once with ordinary content, once with far more
// content than fits. The second case is the one that matters. The rule is that
// a component degrades rather than fails, so an overfilled slide must produce a
// deck that still validates and must not lose any content.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const JSZip = require('jszip');

const { build } = require('../src/build');
const { parse } = require('../src/spec/parse');
const { validate } = require('../validate-deck');
const { COMPONENTS } = require('../src/components');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'deckwright-comp-'));

// Every visible string in the built deck, so a test can assert nothing was lost.
async function deckText(file) {
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  const names = Object.keys(zip.files).filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n));
  let all = '';
  for (const n of names) {
    const xml = await zip.file(n).async('string');
    all += [...xml.matchAll(/<a:t>([\s\S]*?)<\/a:t>/g)].map((m) => m[1]).join('\n');
    all += '\n';
  }
  return all
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&apos;/g, "'");
}

async function buildSpec(name, src) {
  const spec = parse(src);
  const out = path.join(TMP, `${name}.pptx`);
  const manifest = await build(spec, out);
  const report = await validate(out, { expectSlides: manifest.slideCount });
  return { manifest, report, out };
}

// One minimal spec per component, plus one overfilled spec per component.
const CASES = {
  title: {
    ok: '# A programme\nsubtitle: A review\n\n## title: A programme\nsubtitle: A review\n',
    over: `# ${'Very long programme name that goes on '.repeat(6)}\nsubtitle: ${'and a subtitle too '.repeat(12)}\n\n## bullets: x\n- a\n`,
  },
  divider: {
    ok: '## divider: Delivery\n',
    over: `## divider: ${'A section name that will not fit on one line '.repeat(5)}\n`,
  },
  summary: {
    ok: '## summary: Status\nverdict: One sentence.\n- a\n- b\n',
    over: `## summary: Status\nverdict: ${'A verdict that runs on and on and refuses to stop. '.repeat(8)}\n`
      + Array.from({ length: 18 }, (_, i) => `- Supporting point number ${i + 1} which is itself quite long`).join('\n'),
  },
  bullets: {
    ok: '## bullets: Asks\n- one\n- two\n',
    over: `## bullets: Asks\n${Array.from({ length: 40 }, (_, i) => `- Point ${i + 1} of forty, each long enough to wrap on a wide slide when set at body size`).join('\n')}\n`,
  },
  columns: {
    ok: '## columns: Speeds\n### Live\n- a\n### Next\n- b\n',
    over: `## columns: Speeds\n${Array.from({ length: 7 }, (_, c) => `### Column ${c + 1}\n${Array.from({ length: 14 }, (_, i) => `- Item ${i + 1} in column ${c + 1}, long enough to wrap`).join('\n')}`).join('\n')}\n`,
  },
  flow: {
    ok: '## flow: Path\n- Request\n- Review\n- Provision\n',
    over: `## flow: Path\n${Array.from({ length: 20 }, (_, i) => `- Step ${i + 1} with a fairly descriptive name`).join('\n')}\n`,
  },
  roadmap: {
    ok: '## roadmap: Phases\n### Q3 | Foundations\n- contracts\n### Q4 | Scale\n- markets\n',
    over: `## roadmap: Phases\n${Array.from({ length: 9 }, (_, p) => `### Q${(p % 4) + 1} 202${p} | Phase ${p + 1}\n${Array.from({ length: 8 }, (_, i) => `- Milestone ${i + 1} of phase ${p + 1}, described at some length`).join('\n')}`).join('\n')}\n`,
  },
  capabilities: {
    ok: '## capabilities: Map\n### Ingest\n- Contracts\n- Quality\n### Consume\n- Self serve\n',
    over: `## capabilities: Map\n${Array.from({ length: 8 }, (_, l) => `### Layer ${l + 1}\n${Array.from({ length: 9 }, (_, i) => `- Capability ${i + 1} on layer ${l + 1}`).join('\n')}`).join('\n')}\n`,
  },
  raid: {
    ok: '## raid: Risks\nheaders: Type | Item | Owner | Status\n| Risk | Something | Someone | Red |\n',
    over: `## raid: Risks\nheaders: Type | Item | Owner | Status\n${Array.from({ length: 30 }, (_, i) => `| Risk | Item number ${i + 1}, described in enough words that the description column has to wrap at least once | Owner ${i + 1} | ${['Red', 'Amber', 'Green'][i % 3]} |`).join('\n')}\n`,
  },
  stakeholders: {
    ok: '## stakeholders: Map\n| A person | high | high | Sponsor |\n| Another | low | low | Monitor |\n',
    over: `## stakeholders: Map\n${Array.from({ length: 28 }, (_, i) => `| Stakeholder number ${i + 1} with a long title | ${i % 2 ? 'high' : 'low'} | ${i % 3 ? 'high' : 'low'} | A note about their position that is not short |`).join('\n')}\n`,
  },
  metric: {
    ok: '## metric: 1,100\nlabel: Active users\nnote: source\n',
    over: '## metric: 1,100,000,000,000,000\nlabel: A label that is really a sentence and keeps going for quite a while indeed\nnote: A note that also refuses to be brief and carries on well past the point of usefulness\n',
  },
};

test('every component in the library has a test case', () => {
  const missing = Object.keys(COMPONENTS).filter((c) => !CASES[c]);
  assert.deepStrictEqual(missing, [],
    'a component without a test case is a component that breaks silently. Add it to CASES.');
});

for (const [name, cases] of Object.entries(CASES)) {
  test(`${name}: ordinary content builds and validates`, async () => {
    const { report, manifest } = await buildSpec(`${name}-ok`, cases.ok);
    assert.ok(report.ok, `${name} failed validation:\n${report.errors.map((e) => `${e.where}: ${e.message}`).join('\n')}`);
    assert.ok(manifest.slideCount >= 1);
  });

  test(`${name}: overfilled content still validates and loses nothing`, async () => {
    const { report, manifest, out } = await buildSpec(`${name}-over`, cases.over);
    assert.ok(report.ok,
      `${name} overfilled failed validation:\n${report.errors.map((e) => `${e.where}: ${e.message}`).join('\n')}`);

    // Nothing may be dropped. Sample the content the spec asked for and assert
    // it is somewhere in the deck, on a continuation slide if need be.
    const text = await deckText(out);
    const wanted = [...cases.over.matchAll(/^[-|]\s*([^|\n]{12,60})/gm)].map((m) => m[1].trim());
    const sample = [wanted[0], wanted[Math.floor(wanted.length / 2)], wanted[wanted.length - 1]]
      .filter(Boolean);
    for (const s of sample) {
      const needle = s.slice(0, 28);
      assert.ok(text.includes(needle),
        `${name}: "${needle}" was in the spec but is not in the built deck (${manifest.slideCount} slides)`);
    }
  });
}

test('a bullets slide with more content than one slide holds continues onto another', async () => {
  const { manifest } = await buildSpec('continuation', CASES.bullets.over);
  assert.ok(manifest.slideCount > 1, 'forty long bullets must not claim to fit one slide');
  assert.ok(manifest.slides.some((s) => s.continuation), 'the extra slides must be marked as continuations');
  assert.ok(manifest.slides.slice(1).every((s) => /\(continued\)$/.test(s.title)),
    'a continuation slide must say so in its title');
});

test('a continuation title does not accumulate the word continued', async () => {
  const { manifest } = await buildSpec('continuation-title', CASES.raid.over);
  for (const s of manifest.slides) {
    const count = (s.title.match(/\(continued\)/g) || []).length;
    assert.ok(count <= 1, `title accumulated markers: "${s.title}"`);
  }
});

test('a spec that fails the writing standards does not produce a file at all', async () => {
  const spec = parse('## bullets: T\n- Live — mostly\n');
  const out = path.join(TMP, 'never-written.pptx');
  await assert.rejects(() => build(spec, out), /writing standards/);
  assert.ok(!fs.existsSync(out), 'a rejected spec must not leave a deck on disk');
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
