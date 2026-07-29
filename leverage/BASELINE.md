# BASELINE

The point of this file is that the leverage claim can be checked rather than
asserted. Every number below is either **measured** (I ran it and timed it) or
**estimated** (with the basis stated). Nothing is a multiplier with no
derivation.

Machine timings are wall clock on this container, Node 22, cold process start
each time, median of three runs.

---

## Scenario 1. Build a deck of about eleven slides from a standing structure

**The task it replaces:** EVIDENCE A1, the steering or governance deck. Also
A3, A4 and A8, which are the same operation on individual slides.

**Manual time, estimated: 135 min.** Basis: EVIDENCE A1 midpoint (90 to 180
min). ASSUMED, not measured. A1 also estimates the split as roughly 65% format
work and 35% thinking, which gives about **88 min of format work** and 47 min of
thinking per deck.

**Measured time using the tool:**

| Step | Time | How it was obtained |
|---|---|---|
| `deck build specs/steering-review.md`, including validation | **0.24 s** | measured, median of 3: 224, 218, 269 ms |
| `validate-deck.js` alone on the built deck | **0.11 s** | measured, median of 3: 102, 108, 110 ms |
| All four example specs, built and validated | **0.44 s** | measured |
| Writing the spec | **9 min, estimated** | 563 words of already-decided content, transcribed at 60 wpm. Basis: the spec is plain text with no layout decisions in it. This is transcription, not composition |

Thinking time does not change. It is 47 min in both columns, because the tool
does not decide what the verdict is.

```
manual        47 thinking + 88 format          = 135 min
with tool     47 thinking +  9 typing + 0.004  =  56 min
```

**Ratio, honestly stated:**

- On the format work it actually replaces: **88 min to 9 min, 9.5x**
- On the whole task, thinking included: **135 min to 56 min, 2.4x**

The second number is the one to quote to anyone senior. The first is the one
that says whether the tool is any good.

## Scenario 2. Change three numbers in last week's deck

This is the scenario the council could not resolve. The Systems Engineer's
objection in COUNCIL.md is that this case, not the new deck, is the common one,
and that editing three text boxes in PowerPoint beats typing a spec.

**Manual time, estimated: 4 to 10 min.** Basis: EVIDENCE A10. Four minutes if he
changes the three numbers and sends it. Ten if he also re-checks the deck for
the consistency drift that editing introduces, which is A10's 15 to 25 min
scaled down to a three-edit change.

**Measured time using the tool: 0.17 s of machine time** (measured: edit one
line with sed, rebuild, validate) **plus about 1 min of typing.**

**Ratio: 4x if the manual path includes the re-check, 3x if it does not.**

The objection survives in a narrower form than it was made. Editing the spec is
not slower than editing the deck. But the tool's advantage in this case comes
almost entirely from the re-check being free, and the re-check is the step
most likely to be skipped when manual. So the tool wins this case on quality
more than on speed, which is a weaker claim than the headline.

## Scenario 3. Check a deck against the standards

**The task it replaces:** EVIDENCE A10, and A9 when the deck is inherited.

**Manual time, estimated: 15 to 25 min** for a 15-slide deck, and that is the
optimistic figure for a human who actually checks every text box for clipping,
every run for a non-Arial font, and every fill for an off-palette colour.

**Measured time using the tool: 0.11 s**, and it reports the slide, the shape and
the number.

**Ratio: not worth stating as a multiple.** The honest claim is different in
kind: a human does not perform this task exhaustively, ever. On the
steering-review deck the validator measured **127 text boxes**. Nobody checks
127 boxes by hand. So this is not the same task done faster, it is a task that
was previously not being done. That is a bigger deal than the ratio would
suggest and it is why the linter capability was absorbed rather than dropped
when the Compounder vetoed it as a product (COUNCIL.md section 3).

---

## What would have to change to reach 10x

**On format work, 10x is already reached (9.5x, scenario 1).**

**On the whole task, 10x is not reachable by this tool, and the arithmetic says
why.** Thinking is 47 min of the 135 and the tool must not touch it: the brief
forbids automating judgment and EVIDENCE says the same. Even if formatting fell
to zero, the ceiling is 135/47, which is **2.9x**. The tool is already at 2.4x,
so 83% of the achievable whole-task gain is realised, and the remaining 17% is
worth about 9 minutes a deck.

Anyone claiming 10x on the whole task would have to be automating the thinking,
and the output would be a deck whose verdict nobody chose.

Three things would move the honest number, in order of size:

1. **Reuse rather than authoring.** Scenario 1 assumes a spec written from
   scratch. Once a deck's spec exists, next month's version is an edit, and the
   9 minutes of typing drops to 1 or 2. That takes the second deck of a series
   from 2.4x to roughly 2.7x, close to the ceiling.
2. **Fan-out** (idea 3, ranked third in COUNCIL.md). Per-market packs multiply
   the format saving by the number of markets while the thinking is written
   once. Four markets from one source is the only route on this list to a
   whole-task ratio above 3x, because it changes the denominator rather than
   the numerator.
3. **Removing the deck** (idea 7, vetoed). 810 min/month, by not doing the work.
   Still the largest number anyone has produced for this problem, and still not
   a build.

## What is not measured, and would need a month to be

Every manual figure above is ASSUMED, from stated role context, and inherits
that uncertainty. The frequencies in EVIDENCE.md are unverified too, which is
what the Skeptic objected to and what idea 10 would have fixed before the
Operator vetoed it. The claim that survives without any of those numbers being
right is narrow and worth stating on its own:

**A deck of this shape now takes 0.24 s of machine time to produce and 0.11 s to
prove correct against the house standards, from a text file that fits on a phone
screen. Whatever the manual figure turns out to be, it is not 0.24 s.**
