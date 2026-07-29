# Deckwright

A spec compiler for slides. A markdown spec that fits on a phone screen
compiles to a standards-clean `.pptx`, and the validator proves the standards
held on any deck, including ones this tool did not build.

## The one rule that shapes everything else

**Nothing is done until a machine says so.** `deck build` exiting 0 is not
evidence. `validate-deck.js` reads the produced file back off disk, unzips it
and asserts against its XML. Every claim in this repo about a deck being
correct traces to that command, never to reasoning about the builder.

## Locked standards, encoded not documented

These live in `src/theme.js` and are enforced by `validate-deck.js`. Changing
one means changing both, and the test suite will say so.

- pptxgenjs, Node, `LAYOUT_WIDE` 13.33 x 7.5 in
- Arial everywhere, including the presentation theme's major and minor fonts.
  A single non-Arial `<a:latin>` in any slide fails validation
- Navy `#001965` as the primary. Every colour in the output must be in
  `theme.PALETTE`. An unlisted hex fails validation, which is what catches a
  leaked default theme colour
- No text may overflow its placeholder. See "the estimator" below
- All generated writing: no em-dashes, no emojis, skimmable, verdict or action
  first. `src/lint.js` enforces the first two at build time and names the slide
  and field
- Never fabricate facts, names, dates or figures. A field the author did not
  supply renders the literal token `PLACEHOLDER` and raises a build warning.
  The generator has no fallback that invents a plausible value

## The estimator, and what it cannot do

There is no font engine in this pipeline, so overflow is *estimated*:
`src/text/metrics.js` holds per-character advance widths for Arial regular and
bold, wraps greedily at the box's inner width, and sums line heights.

It is an approximation, it says so in its output, and the thresholds are set so
the approximation fails loudly rather than quietly:

- fill ratio above 1.0 is an **error**
- fill ratio from 0.9 to 1.0 is a **warning**, because that is the band where a
  1% metric error changes the answer

Do not tighten this into a silent pass. A trusted check that is wrong is worse
than no check, which is the Maintainer's objection in COUNCIL.md and the whole
reason the warning band exists.

## Layout invariant

Components never place a shape by eyeballed constant. They take a region from
`theme.REGIONS` and subdivide it. A component that hardcodes `y: 2.37` is a
component that breaks when the title grows to two lines.

## Rules

- No component may render a shape outside its allotted region. The validator
  checks bounds against the slide, and the tests check components against
  their region
- Every component must degrade rather than fail. Too many bullets shrinks the
  font to the floor, then splits to a continuation slide. It never clips and it
  never silently drops content
- A component added to `src/components/index.js` must be added to
  `test/components.test.js` in the same commit, with a case that overfills it
- The spec parser is the contract with the phone. It accepts what someone would
  plausibly type with their thumbs: any heading level, loose key casing,
  trailing whitespace, missing optional fields. It errors with a line number
  and never guesses at content
- `validate-deck.js` must work on a `.pptx` it did not build. That is idea 2
  from IDEAS.md, vetoed as a standalone product and absorbed here, so do not
  make it depend on a sidecar manifest to function
- No network calls anywhere, at build or validate time. No model calls. The
  Maintainer vetoed a moving dependency once already
- Tests are `node:test`, run with `npm test`, and must pass before any commit
