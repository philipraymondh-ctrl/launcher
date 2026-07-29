# COUNCIL

Six reviewers, six objective functions, six vetoes. Each scored all ten ideas
against their own objective only. No blending, no averaging inside a column.

## 1. Raw matrix

Columns: OP = Operator (usable tomorrow on a phone), SE = Systems Engineer (one
mechanism, many outputs), SK = Skeptic (honest expected value), CO = Compounder
(value grows per use), SP = Stakeholder Proxy (quality received by senior
audiences), MA = Maintainer (working in six months untouched).

| Idea | OP | SE | SK | CO | SP | MA | Total |
|---|---|---|---|---|---|---|---|
| 1. Deckwright spec compiler | 4 | 5 | 4 | 5 | 5 | 4 | **27** |
| 2. Deck linter, standalone | 3 | 3 | 4 | 2 | 5 | 4 | 21 |
| 3. Fan-out renderer | 3 | 5 | 2 | 4 | 3 | 4 | 21 |
| 4. Decision ledger | 4 | 3 | 3 | 5 | 3 | 5 | 23 |
| 5. Extractor, pptx to spec | 2 | 4 | 3 | 4 | 3 | 3 | 19 |
| 6. Stakeholder commitment tracker | 3 | 2 | 1 | 3 | 3 | 2 | 14 |
| 7. Retire the deck (subtraction) | 5 | 1 | 3 | 2 | 2 | 5 | 18 |
| 8. Talk-track generator | 2 | 2 | 1 | 1 | 2 | 1 | 9 |
| 9. Deck diff | 1 | 2 | 2 | 2 | 4 | 3 | 14 |
| 10. Effort telemetry | 1 | 2 | 5 | 4 | 3 | 2 | 17 |

Two scores worth reading twice. The Skeptic gave the frontrunner a 4 and gave
idea 10 a 5: the only idea on the list that repairs the evidence base scored
highest with the reviewer who cares about honest expected value, and it was
still vetoed. And the Operator gave a 5 to the one idea that involves no code
at all. The council is not a chorus.

## 2. Objections to the frontrunner

Idea 1 led on raw score, so each reviewer named their single strongest
objection to it. Specific and falsifiable, as required.

**OPERATOR.** "The build runs in a terminal. The real loop is: type spec on
phone, find a laptop, build, look. If a laptop is in the loop then the
phone-typeable spec buys nothing that a phone-typeable email did not."
Falsifiable: check whether a build can be triggered without a desktop. E1 shows
he has already solved exactly this shape of problem once, with a browser page
that dispatches a GitHub workflow.
*Partially answered.* A `workflow_dispatch` build path is increment 7 and is
the direct answer. Until it ships, this objection stands.

**SYSTEMS ENGINEER.** "A compiler pays off only if specs get written. The
common case is not a new deck, it is last week's deck with three numbers
changed. Typing 40 lines of spec loses to editing three text boxes."
Falsifiable: time both paths on a small-delta scenario.
*Not answered.* This is the surviving objection. See SELECTION.md.

**SKEPTIC.** "770 min/month rests on A1 at 6 instances/month and a 65% format
share. Neither is measured. Halve both and the claim is 250 min/month, which is
still worth building but is not the number in IDEAS.md."
Falsifiable by one month of logging. *Answered by restating.* BASELINE.md
carries the measured single-instance ratio and names the unverified inputs
rather than compounding them into a headline multiplier.

**COMPOUNDER.** "The library compounds only while it covers the next deck's
shapes. The first deck needing a shape that does not exist sends him back to
PowerPoint, and he does not come back."
Falsifiable: count what fraction of a real deck the shipped component set can
express. *Partially answered.* Ten components chosen to span the observed
shapes in A1-A8, plus a generic multi-column fallback so an unanticipated shape
degrades to something rather than to nothing.

**STAKEHOLDER PROXY.** "Generated decks look generated. Ten slides off one
template reads as low effort to an audience that is sensitive to exactly that."
Falsifiable: show one built deck to a senior reviewer.
*Partially answered.* The component set is deliberately varied in layout, and
the validator enforces the house standard rather than a single layout. But this
one is a matter of taste and cannot be settled by a test tonight.

**MAINTAINER.** "The overflow check estimates from font metrics rather than
rendering. The day it drifts it will pass a clipped slide, and a trusted check
that is wrong is worse than no check."
Falsifiable: compare the estimator against real PowerPoint rendering on edge
cases. *Answered in design.* The estimator fails above 100% of box height and
warns from 90%, so the borderline band is reported rather than silently passed,
and the validator names its own approximation in its output. See DECISIONS D4.

## 3. Vetoes exercised

Six of the ten were removed. Each veto is recorded with its owner and reason.

- **Idea 10, Effort telemetry. Vetoed by the OPERATOR.** It is manual data
  entry, performed on the work rather than being the work, and it requires a
  logging ritual he must sustain unaided. The Skeptic's 5 could not save it.
  Recorded dissent: the Skeptic notes that vetoing the measurement idea is
  precisely how a repo keeps making unverified leverage claims, and this file
  is the evidence that the objection was heard and overruled.
- **Idea 8, Talk-track generator. Vetoed by the MAINTAINER.** Requires a model
  call at runtime: a moving dependency, a drifting prompt, and a credential.
  Lowest total on the board anyway.
- **Idea 9, Deck diff. Vetoed by the SYSTEMS ENGINEER.** An abstraction with a
  single use. It reads one artifact type for one question.
- **Idea 7, Retire the deck. Vetoed by the STAKEHOLDER PROXY.** Deleting the
  deck without owning the forum's format means the senior audience receives
  less than it expects, and the change is not his to make unilaterally. Note
  the shape of this veto: the largest leverage number on the list, 810
  min/month, was removed on standard rather than on effort. Recorded dissent:
  the Operator scored it 5 and observes that the best idea here was killed for
  being political rather than for being wrong.
- **Idea 6, Stakeholder commitment tracker. Vetoed by the SKEPTIC.** Its whole
  value is the frequency of chasing, and that frequency is ASSUMED (A14). The
  same line is 80% thinking work, so what remains after automation is a nag
  list whose upkeep cost lands at the moment of least capacity.
- **Idea 2, Deck linter as a standalone product. Vetoed by the COMPOUNDER.** A
  lint run leaves nothing behind. It is the same file, the same violations, the
  same effort next week.
  **The capability is not lost.** `validate-deck.js` accepts any `.pptx`,
  including decks Deckwright did not build, so idea 2 ships tonight as a
  feature of idea 1 rather than as a product. The Stakeholder Proxy, which
  scored it 5, asked for that to be recorded here.

## 4. Survivors, ranked

Total score, then build cost as tiebreak.

| Rank | Idea | Total | Build cost |
|---|---|---|---|
| 1 | Deckwright spec compiler | 27 | 5-7h |
| 2 | Decision ledger | 23 | 2-3h |
| 3 | Fan-out renderer | 21 | 2h, but only on top of rank 1 |
| 4 | Extractor, pptx to spec | 19 | 3-4h |

## 5. Convergence check

Required: if the council reached consensus with no substantive recorded
objection, return to Phase 1 and replace the four weakest ideas.

Not triggered. Six vetoes were exercised by five different reviewers, two
vetoes carry recorded dissent from a reviewer who scored the idea highest, one
objection to the winner is unanswered, and three more are only partially
answered. Phase 1 was not re-run.
