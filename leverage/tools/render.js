#!/usr/bin/env node
'use strict';

// Render a built deck to one PNG per slide, so a human (or an agent) can look
// at it instead of reasoning about it.
//
// This exists because the XML validator cannot see everything. Three real
// defects survived a clean `validate-deck` run and were only visible once
// rendered: a rotated axis label that landed in the middle of the chart it was
// labelling, process-flow boxes tall enough to look empty, and bullet lists
// top-aligned in a region twice their height. None of the three is detectable
// from geometry alone, because every one of those shapes was inside the slide
// and inside its own box. Rendering is the only check that sees them.
//
// Requires LibreOffice with the Impress module, plus pdftoppm from
// poppler-utils. Neither is a runtime dependency of the deck build: this is a
// development tool, and it says so rather than failing mysteriously.
//
//   node tools/render.js out/steering-review.pptx [--outdir out/png] [--dpi 90]

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

function have(bin) {
  return spawnSync('which', [bin], { encoding: 'utf8' }).status === 0;
}

function main(argv) {
  const args = argv.slice(2);
  const file = args.find((a) => !a.startsWith('--'));
  if (!file) {
    process.stdout.write('usage: node tools/render.js DECK.pptx [--outdir DIR] [--dpi N]\n');
    return 2;
  }
  const outdirIdx = args.indexOf('--outdir');
  const outdir = outdirIdx !== -1 ? args[outdirIdx + 1] : path.join(path.dirname(file), 'png');
  const dpiIdx = args.indexOf('--dpi');
  const dpi = dpiIdx !== -1 ? args[dpiIdx + 1] : '90';

  const missing = ['soffice', 'pdftoppm'].filter((b) => !have(b));
  if (missing.length) {
    process.stderr.write(
      `render: missing ${missing.join(' and ')}. This is a development tool, not part of the build.\n`
      + '  Debian or Ubuntu: apt-get install libreoffice-impress poppler-utils\n',
    );
    return 3;
  }
  if (!fs.existsSync(file)) {
    process.stderr.write(`render: no such file: ${file}\n`);
    return 2;
  }

  fs.mkdirSync(outdir, { recursive: true });
  // LibreOffice writes into a user profile; point it at a scratch one so a
  // headless run does not depend on, or corrupt, anybody's real profile.
  const profile = path.join(os.tmpdir(), `deckwright-lo-${process.pid}`);

  const conv = spawnSync('soffice', [
    `-env:UserInstallation=file://${profile}`,
    '--headless', '--convert-to', 'pdf', '--outdir', outdir, path.resolve(file),
  ], { encoding: 'utf8', timeout: 300000 });
  if (conv.status !== 0) {
    process.stderr.write(`render: LibreOffice failed: ${(conv.stderr || conv.stdout || '').trim()}\n`);
    return 1;
  }
  const pdf = path.join(outdir, `${path.basename(file).replace(/\.pptx$/i, '')}.pdf`);
  if (!fs.existsSync(pdf)) {
    process.stderr.write(`render: expected ${pdf}, LibreOffice wrote nothing\n`);
    return 1;
  }

  const prefix = path.join(outdir, path.basename(file).replace(/\.pptx$/i, ''));
  const ras = spawnSync('pdftoppm', ['-r', dpi, '-png', pdf, `${prefix}-slide`], { encoding: 'utf8' });
  if (ras.status !== 0) {
    process.stderr.write(`render: pdftoppm failed: ${(ras.stderr || '').trim()}\n`);
    return 1;
  }
  const pngs = fs.readdirSync(outdir).filter((f) => f.startsWith(`${path.basename(prefix)}-slide`) && f.endsWith('.png'));
  process.stdout.write(`render: ${pngs.length} slide image(s) in ${outdir}\n`);
  for (const p of pngs.sort()) process.stdout.write(`  ${path.join(outdir, p)}\n`);
  return 0;
}

process.exit(main(process.argv));
