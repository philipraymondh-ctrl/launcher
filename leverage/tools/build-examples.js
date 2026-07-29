#!/usr/bin/env node
'use strict';

// Build and validate every spec in specs/. This is the acceptance test for the
// library as a whole: several specs of different shapes must build and validate
// clean, not just the one that was used while writing the components.
//
//   npm run examples
//
// Exits non-zero if any spec fails to build or fails validation, so it works as
// a pre-commit check.

const fs = require('fs');
const path = require('path');

const { parse } = require('../src/spec/parse');
const { build } = require('../src/build');
const { validate } = require('../validate-deck');

const ROOT = path.join(__dirname, '..');
const SPECS = path.join(ROOT, 'specs');
const OUT = path.join(ROOT, 'out');

async function main() {
  const files = fs.readdirSync(SPECS).filter((f) => f.endsWith('.md')).sort();
  if (files.length === 0) {
    process.stderr.write('build-examples: no specs found\n');
    return 2;
  }
  fs.mkdirSync(OUT, { recursive: true });

  let failed = 0;
  const rows = [];
  for (const f of files) {
    const name = f.replace(/\.md$/, '');
    const out = path.join(OUT, `${name}.pptx`);
    const started = Date.now();
    try {
      const spec = parse(fs.readFileSync(path.join(SPECS, f), 'utf8'));
      const manifest = await build(spec, out);
      const report = await validate(out, { expectSlides: manifest.slideCount });
      const ms = Date.now() - started;
      const shapes = new Set(manifest.slides.map((s) => s.type));
      rows.push({
        name,
        slides: manifest.slideCount,
        shapes: shapes.size,
        placeholders: manifest.placeholders.length,
        warnings: manifest.warnings.length,
        warningList: manifest.warnings,
        ms,
        ok: report.ok,
        errors: report.errors,
      });
      if (!report.ok) failed += 1;
    } catch (e) {
      rows.push({ name, ok: false, errors: [{ where: 'build', message: e.message }], ms: Date.now() - started });
      failed += 1;
    }
  }

  const w = Math.max(...rows.map((r) => r.name.length));
  process.stdout.write(`build-examples: ${rows.length} spec(s)\n`);
  for (const r of rows) {
    if (r.ok) {
      process.stdout.write(
        `  VALID    ${r.name.padEnd(w)}  ${String(r.slides).padStart(2)} slides, `
        + `${r.shapes} shape types, ${r.placeholders} placeholders, ${r.ms}ms\n`,
      );
      for (const wn of r.warningList || []) process.stdout.write(`    WARN ${wn}\n`);
    } else {
      process.stdout.write(`  INVALID  ${r.name.padEnd(w)}\n`);
      for (const e of r.errors) process.stdout.write(`    ${e.where}: ${e.message}\n`);
    }
  }
  const total = rows.reduce((a, r) => a + r.ms, 0);
  process.stdout.write(`  ${rows.length - failed}/${rows.length} valid, ${total}ms total\n`);
  return failed === 0 ? 0 : 1;
}

main().then((c) => process.exit(c)).catch((e) => {
  process.stderr.write(`build-examples: ${e.stack}\n`);
  process.exit(2);
});
