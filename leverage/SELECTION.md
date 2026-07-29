# SELECTION

## Winner

**Deckwright: a spec compiler for slides.** A phone-typeable markdown spec
compiles to a standards-clean `.pptx`, and `validate-deck.js` proves the
standards held on any deck, including ones it did not build.

## The three reasons it won

1. **It is one mechanism under eight recurring outputs.** A1, A2, A3, A4, A8,
   A9, A12 and the check in A10 are not eight problems, they are one operation
   performed eight ways: short structured content in, formatted slides out,
   standards verified. Nothing else on the list served more than two lines.
2. **The standard goes up, not just the volume.** The Stakeholder Proxy holds
   the veto that kills most productivity tools, and this was the only
   generation idea it scored 5. A validator that fails a clipped slide or a
   leaked theme colour raises the floor on what a senior audience receives.
   Faster and worse would have been vetoed.
3. **It compounds twice.** Each spec is a reusable, diffable asset, and each
   component added serves every future deck. A linter run compounds not at all,
   which is why the same capability ships inside this rather than beside it.

## Runner-up

**The decision ledger (idea 4, total 23).**

The exact condition under which it would have won: if slide standards were
already solved. If there were a corporate template that reliably produced
compliant slides, then A1-A4 and A9-A10 collapse to near-zero automatable
effort, and the largest remaining automatable cost is E3, the only OBSERVED
evidence line in this repo about repeated written effort. The ledger also beat
the winner outright on two columns, the Compounder and the Maintainer, because
plain text has no dependency to rot.

Deckwright won on breadth, not on being better at its own job.

## The objection that was not resolved

The Systems Engineer's: **a compiler pays off only if the specs get written,
and the common case is not a new deck.** It is last week's deck with three
numbers changed. For that case, typing forty lines of spec plausibly loses to
editing three text boxes in PowerPoint, and no amount of build quality fixes
that.

Two things were done about it and neither closes it. Specs are files, so week
two is editing a text file rather than typing a fresh one, and `deck new`
scaffolds a spec so the first draft is not a blank page. But the week-one cost
is real and it lands before any of the benefit does. This is the failure mode
IDEAS.md predicted for this idea, the council could not talk it away, and it is
the thing to watch for over the next month.

## Prediction

If this was the right pick, then by tomorrow morning:

- `deck build` turns a spec that fits on a phone screen into a `.pptx` that
  opens, and `validate-deck.js` passes it on slide count, Arial, navy #001965,
  and no overflow, in under five seconds.
- `validate-deck.js` run against a deck built deliberately wrong fails and
  names the slide, the component and the violation. A validator that cannot
  fail is not a validator, so this matters more than the build working.
- BASELINE.md carries a ratio measured by running the tool and timing it, and
  that ratio is stated honestly even if it is 3x rather than 10x.

If instead the morning shows a working generator with a validator that passes
everything handed to it, the pick was wrong in the way the Maintainer warned
about, and the honest move is to trust nothing it says about overflow.
