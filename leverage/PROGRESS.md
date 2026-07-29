# PROGRESS

One line per completed step. Newest at the bottom.

- Setup: `leverage/` created inside the launcher repo on the designated
  branch, npm initialised, pptxgenjs installed and confirmed working.
- Phase 0: EVIDENCE.md written. Direct evidence is thin and says so at the
  top; four OBSERVED lines from this repo, fourteen ASSUMED lines from stated
  role context.
- Phases 1 to 3: IDEAS.md (10 candidates, 6 categories, 1 speculative),
  COUNCIL.md (6x10 matrix, 6 vetoes, 2 recorded dissents), SELECTION.md
  (winner: a spec compiler for slides), feasibility gate passed on all four.
- Increment 1: theme, Arial metrics estimator, spec parser, writing lint, 11
  components, build, validate-deck.js, CLI. `deck build specs/steering-review.md`
  produces an 11-slide deck that validates clean. The first run found four real
  defects; all fixed.
- Increment 2: rendered the deck through LibreOffice and looked at it. Three
  layout defects were invisible to the XML validator: a rotated axis label
  landing in the middle of its own chart, process-flow boxes sized from the
  region rather than the content, and bullet lists top-aligned in a region twice
  their height. All three fixed. The first also exposed a validator gap, which
  now rotates a shape's box before checking bounds. `tools/render.js` makes the
  check repeatable.
- Increment 3: 80 tests (`npm test`), all passing. The important file is
  test/validate.test.js, which builds decks that are deliberately wrong in each
  forbidden way and asserts the named check catches each one, so the validator
  is known to be able to fail. Three test failures were real findings: a wrong
  assertion about bold advance widths, an overstated claim in a code comment
  about what the rotation-aware bounds check can catch (corrected), and a
  roadmap that dropped content when phases were dense. Roadmap now packs phases
  by estimated height rather than five at a time, and exceeding the continuation
  cap fails the build instead of warning over a deck that lost slides.
