# HANDOVER

## 1. Verdict

A markdown file that fits on a phone screen now compiles to a finished
`.pptx`: Arial, navy #001965, 13.33 x 7.5, eleven slide shapes, in a quarter of
a second.

A validator reads the built file back off disk and proves it, on any deck,
including ones this tool did not build, and it is proven able to fail.

Four example specs of different shapes build and validate clean, 80 tests pass,
and a GitHub workflow does the same thing without a desktop.

## 2. What the council chose, and the objection it never resolved

Ten candidates, six adversarial reviewers, six vetoes. The winner was the spec
compiler, because it is one mechanism under eight of the recurring outputs in
EVIDENCE.md while everything else served at most two.

The unresolved objection is the Systems Engineer's: **a compiler only pays off
if the specs get written, and the common case is not a new deck.** It is last
week's deck with three numbers changed, and for that case editing three text
boxes plausibly beats typing a spec.

I measured that case rather than arguing with it (BASELINE scenario 2). Editing
the spec is not slower. But the tool's advantage there comes almost entirely
from the consistency re-check being free rather than from speed, which is a
weaker claim than the headline. The objection survives in that narrower form and
it is the thing to watch over the next month.

Two vetoes carry recorded dissent, both worth knowing:

- The largest leverage number anyone produced, 810 min/month, was **stop making
  the weekly deck** and send one page instead. The Stakeholder Proxy vetoed it
  because the format is not yours to change unilaterally. The Operator scored it
  5 and noted it was killed for being political, not for being wrong.
- The Skeptic's top-scoring idea was **measuring where the hours actually go**,
  because twelve of the fourteen effort figures in EVIDENCE.md are assumptions.
  The Operator vetoed it as manual data entry. That means every leverage number
  in this repo still rests on estimates, and BASELINE.md says so in its own
  words rather than hiding it.

## 3. Commands to try first

```bash
cd leverage
npm install
npm test
npm run examples
```

Then look at one:

```bash
node bin/deck.js build specs/steering-review.md
open out/steering-review.pptx
```

Point it at a deck you did not write:

```bash
node validate-deck.js /path/to/someone-elses-deck.pptx
```

Start your own, on a phone or otherwise:

```bash
node bin/deck.js new -o specs/my-deck.md
node bin/deck.js build specs/my-deck.md
```

See every slide type and what it draws:

```bash
node bin/deck.js components
```

**From a phone, no terminal:** edit or add a file under `leverage/specs/` in
GitHub's web editor and push. The `deck` workflow builds and validates every
spec and attaches the decks to the run. Or run the workflow manually and paste a
spec into the `spec` input.

## 4. Measured leverage

Measured, wall clock, median of three cold runs:

| | time |
|---|---|
| Build and validate an 11-slide deck | **0.24 s** |
| Validate a deck | **0.11 s** |
| Build and validate all four example specs | **0.44 s** |

Against the estimated manual figures in EVIDENCE.md:

- **9.5x on the format work it replaces** (88 min of layout for a deck of this
  size, down to about 9 min of typing)
- **2.4x on the whole task**, thinking included, because thinking is 35% of the
  deck and the tool does not touch it
- **The whole-task ceiling is 2.9x** and that is arithmetic, not modesty. If
  formatting fell to zero the number would be 135/47. Anyone claiming 10x on the
  whole task is automating the judgment, and the deck's verdict would then be
  nobody's.

The one claim that survives even if every manual estimate is wrong: **127 text
boxes were measured for overflow on one deck in 0.11 s.** Nobody checks 127
boxes by hand, so that check is not a task done faster, it is a task that was
not being done.

## 5. Decisions waiting for you

Not questions. Each has a default already in the repo, in BLOCKED.md with the
consequences either way.

1. **Palette.** Nine supporting colours are my choice; navy is yours. Replacing
   `src/theme.js` re-points the validator at the new set everywhere, including on
   inherited decks. Default: leave it.
2. **Overflow estimate.** Never checked against real PowerPoint, only against
   LibreOffice with a metrically identical font. Open one deck once and it is
   settled. Default: accept the 1% band the thresholds are built to absorb.
3. **Workflow trigger.** The deck workflow runs on every push touching
   `leverage/**`, in a repo that already runs an hourly scraper. Narrow it to
   `main` if that is too many minutes. Default: leave it.
4. **En-dashes** warn rather than fail, so `Q3–Q4` survives. One line to change.
5. **Next build: fan-out or the extractor.** Default is fan-out, for the reason
   in section 6.

## 6. What I would do next

Fan-out, idea 3 in IDEAS.md, ranked third by the council and worth about two
hours on top of what exists. It is the only route BASELINE.md identifies to a
whole-task ratio above 3x, because it is the only one that changes the
denominator rather than the numerator: one status file, N market packs, the
thinking written once and the formatting multiplied. Everything else on the list
makes a single deck cheaper, and a single deck is already 0.24 seconds. I would
not build the extractor first, and I would not build anything at all until the
week-one question is answered by use rather than by argument, which is whether a
spec gets written on a Tuesday when there are twenty minutes left. That is the
Systems Engineer's objection and no amount of further building resolves it. If
the answer turns out to be no, the honest next move is not more features, it is
idea 7: stop making the deck.
