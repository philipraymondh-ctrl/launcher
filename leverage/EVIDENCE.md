# EVIDENCE

## Direct evidence is thin. Read this first.

The working directory contains exactly one project: a personal wine-producer
scraper (`/home/user/launcher`). There are no work artifacts here. No decks, no
status packs, no templates, no meeting notes, no spreadsheets. Nothing from
Novo Nordisk, and nothing should be invented to fill the gap.

So this file splits into two halves:

- **OBSERVED** lines are inferred from real code and real documents in this
  repo. They are about how Philip works, not about what he produces at work.
- **ASSUMED** lines come from the stated role context in the mission brief
  (Transformation Manager, Analytics Data & Insights: multi-market rollouts,
  capability architecture, stakeholder alignment without reporting lines,
  heavy senior-stakeholder deck production, reviews output on a phone).
  Frequencies and effort figures on ASSUMED lines are estimates, not measured.
  Every downstream leverage claim built on them inherits that uncertainty, and
  BASELINE.md states it plainly rather than hiding it in a multiplier.

---

## OBSERVED, from this repo

**E1. Output is reviewed and operated from a phone.**
`dashboard.py` (35KB) exists to generate `wine.html`, a status page whose
buttons call the GitHub REST API directly from the browser so that a run can be
triggered and watched without a desktop, and with no link out to the repo.
That is a deliberate, expensive design choice serving one constraint.
Frequency: every interaction with the project. Effort: n/a.
Nature: **format/interface work**, and already automated here.
Implication for tonight: an artifact whose output cannot be judged on a phone
is an artifact that will not be judged.

**E2. Nothing is "done" until a machine says so.**
`CLAUDE.md` rule: "Every scraping change must pass the fixture tests in
`tests/` before commit." `probe.py --apply` may only set `verified: true`
because fetch, parse and flag happen in one run against a real response;
never by hand. `tests/test_dashboard.py` runs the real JavaScript through the
real Python parser rather than reimplementing the format.
Frequency: every change. Nature: **format work, automatable, and automated.**
Implication: a generator without a validator would be rejected on sight.

**E3. Rationale is re-documented repeatedly and at length.**
22KB of `CLAUDE.md`, 15KB of `PLAN.md`, plus `decisions/standing-decisions.md`
and `decisions/open-questions.md`. The pattern is consistent: every decision
carries the failure it prevents ("three estates were reported wrongly before
this existed", "one live dry run put exactly that in the digest").
Frequency: continuous. Effort per instance: 15-45 min.
Nature: **mostly thinking work.** The judgment is not automatable. The
retrieval of "what did we decide and why" is.

**E4. Silent failure is treated as the primary enemy.**
`ShopResult.products_parsed` exists so a shop that parses to zero produces a
`DRIFT` line instead of a quiet empty run. Digest notes name shops that
returned nothing and producers found nowhere. Near-miss alias detection exists
to make a typo visible.
Frequency: designed into every run. Nature: **format work, automated.**
Implication: a tool that fails quietly is worse than no tool. Loud failure
with a named cause is the standard to hold.

---

## ASSUMED, from stated role context

Effort figures are per instance, for one person, excluding review cycles.
"Format" means it could be produced by a machine from a shorter input.
"Thinking" means the content itself requires judgment and must not be
automated.

| # | Recurring output | Frequency | Effort | Format | Thinking |
|---|---|---|---|---|---|
| A1 | Steering / governance deck for senior stakeholders | weekly to biweekly | 90-180 min | ~65% | ~35% |
| A2 | Per-market rollout status pack, one per market | monthly x N markets | 40-60 min each | ~80% | ~20% |
| A3 | Capability architecture map slide (boxes, layers, owners) | monthly, redrawn on change | 45-90 min | ~85% | ~15% |
| A4 | Phased roadmap / timeline slide | biweekly | 30-45 min | ~85% | ~15% |
| A5 | RAID or risk-and-decision log slide from a running list | weekly | 20-30 min | ~90% | ~10% |
| A6 | Stakeholder map / influence-interest grid | monthly, or per new initiative | 40-60 min | ~80% | ~20% |
| A7 | Executive summary slide, the "so what" | once per deck | 25-40 min | ~40% | ~60% |
| A8 | Single-metric callout slide (one number, one claim) | per deck, often several | 10-15 min | ~90% | ~10% |
| A9 | Reformatting inherited or borrowed slides to house standards | weekly | 25-40 min | ~100% | 0% |
| A10 | Consistency policing across a finished deck: fonts, navy, overflow, stray theme colours | per deck, every revision | 15-25 min | ~100% | 0% |
| A11 | Pre-read note or email summarising a deck for people who will not open it | per deck | 15-25 min | ~50% | ~50% |
| A12 | Re-cutting one deck for a different audience, same content, different depth | 2-4 per month | 45-75 min | ~75% | ~25% |
| A13 | Meeting notes into owned actions | weekly | 20 min | ~30% | ~70% |
| A14 | Chasing status inputs from people who do not report to him | weekly | 30-60 min | ~20% | ~80% |

## What the table says

The mass of automatable effort is concentrated and it is all the same shape:
**A1 through A4, A8, A9, A10 and A12 are the same operation** — turning a
short structured statement of content into correctly formatted, standards-
compliant slides, then proving the standards held.

Rough monthly format-work total across those lines, using midpoints and
conservative instance counts (A2 at 4 markets, A12 at 3):

```
A1  6 x 135 x 0.65 =  527 min
A2  4 x  50 x 0.80 =  160 min
A3  1 x  67 x 0.85 =   57 min
A4  2 x  37 x 0.85 =   63 min
A8  8 x  12 x 0.90 =   86 min
A9  4 x  32 x 1.00 =  128 min
A10 8 x  20 x 1.00 =  160 min
A12 3 x  60 x 0.75 =  135 min
                     -------
                     1316 min/month of format work  (~22 hours)
```

Treat 1316 as an order of magnitude, not a number: it rests on ASSUMED
frequencies. The defensible claim is narrower and does not need the total to
be right: **the largest single category of automatable effort in this role is
slide production and slide standards enforcement, and it recurs weekly.**

A13 and A14 are the largest *remaining* costs and they are thinking work and
politics. Nothing built tonight should pretend to automate them.
