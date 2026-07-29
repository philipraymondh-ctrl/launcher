'use strict';

// spec -> .pptx
//
// The build is deliberately dumb: it lints, then walks the slide list handing
// each entry to its component, and any content a component could not place
// comes back as a continuation slide and goes on the end of the queue. Nothing
// is dropped and nothing is clipped.
//
// The build does not decide whether the deck is correct. validate-deck.js does
// that, by reading the written file back off disk.

const path = require('path');
const PptxGenJS = require('pptxgenjs');

const theme = require('./theme');
const { COMPONENTS } = require('./components');
const { lintSpec } = require('./lint');

// A continuation of a continuation of a continuation means a spec that should
// have been split by its author. Cap it, and say so.
const MAX_CONTINUATIONS = 8;

function makeContext(pptx, index, type, warnings) {
  return {
    pptx,
    index,
    where: `slide ${index} (${type})`,
    warn(message) {
      warnings.push(message);
    },
  };
}

// Build a deck from a parsed spec. Returns a manifest describing what was
// written, which is what the CLI prints and what BASELINE.md times.
async function build(spec, outPath) {
  const lint = lintSpec(spec);
  if (!lint.ok) {
    const err = new Error(
      `spec failed the writing standards:\n${lint.errors.map((e) => `  ${e.where}: ${e.message}`).join('\n')}`,
    );
    err.lint = lint;
    throw err;
  }

  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_WIDE';
  // The theme fonts matter as much as the run fonts: a deck whose theme says
  // Calibri hands Calibri to anyone who types into it later, and
  // validate-deck.js fails on exactly that.
  pptx.theme = { headFontFace: theme.FONT, bodyFontFace: theme.FONT };
  pptx.author = 'Deckwright';
  pptx.title = spec.meta.title || 'Untitled deck';

  const warnings = [];
  const manifest = { slides: [], warnings, placeholders: lint.placeholders, lintWarnings: lint.warnings };

  const queue = spec.slides.map((s) => ({ ...s, continuation: 0 }));
  let index = 0;

  while (queue.length > 0) {
    const slideSpec = queue.shift();
    const component = COMPONENTS[slideSpec.type];
    if (!component) {
      throw new Error(`no component for slide type "${slideSpec.type}"`);
    }
    index += 1;
    const slide = pptx.addSlide();
    const ctx = makeContext(pptx, index, slideSpec.type, warnings);
    ctx.slide = slideSpec;

    const result = component(slide, spec, ctx) || {};
    manifest.slides.push({
      index,
      type: slideSpec.type,
      title: slideSpec.title || '',
      continuation: slideSpec.continuation > 0,
    });

    if (result.overflow) {
      const depth = slideSpec.continuation + 1;
      if (depth > MAX_CONTINUATIONS) {
        warnings.push(
          `${ctx.where}: content still did not fit after ${MAX_CONTINUATIONS} continuation slides. ` +
            'The remainder was not placed. Split this slide in the spec.',
        );
      } else {
        const title = slideSpec.title
          ? `${String(slideSpec.title).replace(/ \(continued\)$/, '')} (continued)`
          : '';
        queue.unshift({ ...result.overflow, title, continuation: depth });
      }
    }
  }

  manifest.slideCount = index;
  manifest.out = path.resolve(outPath);
  await pptx.writeFile({ fileName: manifest.out });
  return manifest;
}

module.exports = { build, MAX_CONTINUATIONS };
