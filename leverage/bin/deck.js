#!/usr/bin/env node
'use strict';

// deck: the CLI.
//
//   deck build SPEC.md [-o OUT.pptx]   compile a spec, then validate it
//   deck validate OUT.pptx [--spec S]  validate any deck, including foreign ones
//   deck lint SPEC.md                  check the writing standards only
//   deck new [NAME]                    print a starter spec
//   deck components                    list the slide types a spec can use
//
// `build` runs the validator itself and exits non-zero if it fails. There is no
// path through this CLI that reports success on an unvalidated deck.

const fs = require('fs');
const path = require('path');

const { parse, SpecError, TYPES } = require('../src/spec/parse');
const { build } = require('../src/build');
const { lintSpec } = require('../src/lint');
const { validate, printReport } = require('../validate-deck');

const STARTER = `# PLACEHOLDER programme name
subtitle: Steering review, PLACEHOLDER date
presenter: Philip Raymond
footer: Internal

## summary: Where we stand
verdict: PLACEHOLDER one sentence. The verdict, not the context.
- PLACEHOLDER what is true now
- PLACEHOLDER what is blocked, and on whom
- Ask: PLACEHOLDER the decision you need in the room

## metric: PLACEHOLDER
label: PLACEHOLDER what the number counts
note: PLACEHOLDER source of the figure

## divider: Delivery

## roadmap: Phases
### Q3 PLACEHOLDER | Foundations
- PLACEHOLDER milestone
### Q4 PLACEHOLDER | Scale
- PLACEHOLDER milestone

## raid: Risks and decisions
headers: Type | Item | Owner | Status
| Risk | PLACEHOLDER | PLACEHOLDER | Amber |
| Decision | PLACEHOLDER | PLACEHOLDER | Red |
`;

function usage() {
  return [
    'deck: a spec compiler for slides',
    '',
    '  deck build SPEC.md [-o OUT.pptx]   compile, then validate. Non-zero if invalid.',
    '  deck validate DECK.pptx [--spec S] validate any .pptx, including one this did not build',
    '  deck lint SPEC.md                  writing standards only, no build',
    '  deck new [-o SPEC.md]              print a starter spec',
    '  deck components                    list slide types',
    '',
    `slide types: ${[...TYPES].sort().join(', ')}`,
  ].join('\n');
}

function readSpec(file) {
  if (!fs.existsSync(file)) {
    process.stderr.write(`deck: no such spec file: ${file}\n`);
    process.exit(2);
  }
  try {
    return parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    if (e instanceof SpecError) {
      process.stderr.write(`deck: ${file}: ${e.message}\n`);
      process.exit(2);
    }
    throw e;
  }
}

function flag(args, name) {
  const i = args.indexOf(name);
  return i === -1 ? null : args[i + 1];
}

async function cmdBuild(args) {
  const specFile = args.find((a) => !a.startsWith('-') && a !== flag(args, '-o') && a !== flag(args, '--out'));
  if (!specFile) {
    process.stderr.write(`${usage()}\n`);
    return 2;
  }
  const out = flag(args, '-o') || flag(args, '--out')
    || path.join('out', `${path.basename(specFile).replace(/\.(md|txt|yaml|yml)$/i, '')}.pptx`);
  fs.mkdirSync(path.dirname(path.resolve(out)), { recursive: true });

  const spec = readSpec(specFile);
  const started = Date.now();
  let manifest;
  try {
    manifest = await build(spec, out);
  } catch (e) {
    process.stderr.write(`deck: ${e.message}\n`);
    return 1;
  }
  const buildMs = Date.now() - started;

  const manifestPath = `${out.replace(/\.pptx$/i, '')}.manifest.json`;
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  process.stdout.write(`deck build: ${out}\n`);
  process.stdout.write(`  ${manifest.slideCount} slide(s) in ${buildMs}ms\n`);
  for (const s of manifest.slides) {
    process.stdout.write(`    ${String(s.index).padStart(2)}  ${s.type.padEnd(13)} ${s.title}${s.continuation ? '' : ''}\n`);
  }
  if (manifest.placeholders.length) {
    process.stdout.write(`  ${manifest.placeholders.length} PLACEHOLDER(s). Nothing here was invented; replace them before sending.\n`);
  }
  for (const w of manifest.lintWarnings) process.stdout.write(`  WARN  ${w.where}: ${w.message}\n`);
  for (const w of manifest.warnings) process.stdout.write(`  WARN  ${w}\n`);

  const report = await validate(out, { expectSlides: manifest.slideCount });
  process.stdout.write(`\n${printReport(out, report)}\n`);
  return report.ok ? 0 : 1;
}

async function cmdValidate(args) {
  const file = args.find((a) => !a.startsWith('-') && a !== flag(args, '--spec') && a !== flag(args, '--slides'));
  if (!file) {
    process.stderr.write(`${usage()}\n`);
    return 2;
  }
  // Delegate to validate-deck.js so there is exactly one implementation of the
  // acceptance test, and it is the one a human runs directly.
  const { spawnSync } = require('child_process');
  const r = spawnSync(process.execPath, [path.join(__dirname, '..', 'validate-deck.js'), ...args], { stdio: 'inherit' });
  return r.status === null ? 2 : r.status;
}

function cmdLint(args) {
  const specFile = args.find((a) => !a.startsWith('-'));
  if (!specFile) {
    process.stderr.write(`${usage()}\n`);
    return 2;
  }
  const spec = readSpec(specFile);
  const report = lintSpec(spec);
  for (const e of report.errors) process.stdout.write(`  FAIL  ${e.where}: ${e.message}\n`);
  for (const w of report.warnings) process.stdout.write(`  WARN  ${w.where}: ${w.message}\n`);
  for (const p of report.placeholders) process.stdout.write(`  NOTE  ${p.where}: PLACEHOLDER\n`);
  process.stdout.write(report.ok
    ? `  OK    ${spec.slides.length} slide(s), writing standards met\n`
    : `  INVALID, ${report.errors.length} error(s)\n`);
  return report.ok ? 0 : 1;
}

function cmdNew(args) {
  const out = flag(args, '-o') || flag(args, '--out');
  if (out) {
    fs.writeFileSync(out, STARTER);
    process.stdout.write(`deck new: wrote ${out}\n`);
  } else {
    process.stdout.write(STARTER);
  }
  return 0;
}

function cmdComponents() {
  const rows = [
    ['title', 'full-bleed navy opener, from the deck title and subtitle'],
    ['divider', 'section marker, one line on a navy band'],
    ['summary', 'executive summary: a verdict panel, then supporting points'],
    ['bullets', 'a list. The default when a "##" heading gives no type'],
    ['columns', 'two to four columns, one per "###" group'],
    ['flow', 'process steps left to right, wrapping above six'],
    ['roadmap', 'phased plan, one band per "### period | phase" group'],
    ['capabilities', 'capability map, one row per "###" layer'],
    ['raid', 'table from "| a | b |" rows, last column read as a status'],
    ['stakeholders', 'influence and interest grid from "| name | high | low | note |"'],
    ['metric', 'one number at 86pt, a label, and a source note'],
  ];
  for (const [name, desc] of rows) process.stdout.write(`  ${name.padEnd(14)} ${desc}\n`);
  return 0;
}

async function main(argv) {
  const [cmd, ...args] = argv.slice(2);
  switch (cmd) {
    case 'build': return cmdBuild(args);
    case 'validate': return cmdValidate(args);
    case 'lint': return cmdLint(args);
    case 'new': return cmdNew(args);
    case 'components': return cmdComponents();
    default:
      process.stdout.write(`${usage()}\n`);
      return cmd ? 2 : 0;
  }
}

main(process.argv).then((code) => process.exit(code)).catch((e) => {
  process.stderr.write(`deck: ${e.stack}\n`);
  process.exit(2);
});
