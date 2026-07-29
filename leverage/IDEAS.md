# IDEAS

Ten candidates. Evidence lines refer to EVIDENCE.md (E-prefixed are OBSERVED
in this repo, A-prefixed are ASSUMED from stated role context).

Mechanism uniqueness was enforced. One candidate was cut for violating it: an
"audience re-cut" tool (same content at exec / steering / working depth) is the
same mechanism as idea 3, one source rendered many ways, so it is a dimension
of idea 3 rather than an eleventh idea.

---

### 1. Deckwright: a spec compiler for slides
What it replaces: A1, A2, A3, A4, A8, A9, A12 - the shared operation under all
of them, turning a short structured statement of content into standards-clean
slides and proving the standards held (A10).
Leverage arithmetic: 55 min saved per deck x 14 deck-instances/month = 770
min/month. Assumes the spec is faster to write than the slides are to draw.
Build cost: 5-7 hours to a validated v1 with 10 components and a validator.
Shippable unattended tonight: yes.
Failure mode: the spec never gets written. On a Tuesday with 20 minutes left he
opens last week's file and edits it, because a known-bad path beats a new one.
Ceiling: every recurring deck he owns is a text file in version control, and
"regenerate with this month's numbers" is one command.

### 2. Deck linter for decks he did not write
What it replaces: A9, A10 - reformatting inherited slides, and policing
consistency across a finished deck.
Leverage arithmetic: 18 min saved per instance x 12 instances/month = 216
min/month.
Build cost: 2-3 hours, largely shared with idea 1's validator.
Shippable unattended tonight: yes.
Failure mode: it reports 40 violations on a deck due in an hour, none of which
he has time to fix, so he stops running it.
Ceiling: no deck leaves his hands off-standard, and the check runs on a
directory rather than a file.

### 3. Fan-out renderer: one source, N outputs
What it replaces: A2, and the cut candidate above - per-market packs, and the
same deck re-cut for a different audience.
Leverage arithmetic: 35 min saved per market-pack x 4 markets x 1/month = 140
min/month, plus 45 x 3 = 135 for audience re-cuts. 275 min/month.
Build cost: 2 hours, but only on top of idea 1. Alone it is meaningless.
Shippable unattended tonight: partial. The renderer needs a renderer.
Failure mode: markets diverge. Market 4 wants a slide the others do not, the
shared source grows conditionals, and it becomes harder than four files.
Ceiling: one status file per programme, and every audience's pack falls out of
it on demand.

### 4. Decision ledger with retrieval
What it replaces: E3 - rationale re-documented repeatedly, and the archaeology
of "why did we decide X" months later.
Leverage arithmetic: 20 min saved per lookup x 8 lookups/month = 160 min/month.
Weakest link in the arithmetic is the lookup frequency, which is unmeasured.
Build cost: 2-3 hours.
Shippable unattended tonight: yes.
Failure mode: writing to it is a cost paid now for a benefit paid later, which
is the exact shape of thing that stops getting written by week three.
Ceiling: every programme decision has a durable record with its reason, and new
joiners stop asking him to be the memory.

### 5. Extractor: pptx back to spec
What it replaces: A9, A12 - inherited slides that must be reworked, and decks
re-cut for another audience where the content already exists in a file.
Leverage arithmetic: 25 min saved per instance x 6 instances/month = 150
min/month.
Build cost: 3-4 hours. Needs idea 1's spec format to exist.
Shippable unattended tonight: partial.
Failure mode: real decks are messy. Extraction gets 70% right, and reviewing
what it got wrong costs more than retyping the content.
Ceiling: any deck he receives becomes editable as text on a phone.

### 6. UNCOMFORTABLE. Stakeholder commitment tracker
What it replaces: A14 - chasing status inputs from people who do not report
to him.
Leverage arithmetic: 25 min saved per week x 4 = 100 min/month, and the real
claim is not time but fewer dropped commitments.
Build cost: 3 hours.
Shippable unattended tonight: yes.
Failure mode: it is a nag list. Its value depends on him updating it after
every conversation, and the conversations are exactly when he has no hands.
Ceiling: nothing agreed verbally is lost, and escalation is triggered by age
rather than by memory.

### 7. SUBTRACTION. Retire the recurring deck, send a one-page pre-read
What it replaces: A1 outright, and A11 partially - stop producing the weekly
deck; send one page of verdict, decisions needed, and exceptions.
Leverage arithmetic: 135 min saved per deck x 6/month = 810 min/month, the
largest number on this list, achieved by deleting work rather than automating it.
Build cost: 0 hours of code. The cost is entirely political.
Shippable unattended tonight: no. It is not a build.
Failure mode: he does not control the governance forum's format. The deck is
the price of the slot, and arriving without one reads as unpreparedness.
Ceiling: senior time is spent on decisions rather than on being walked through
slides.

### 8. SPECULATIVE. Talk-track and speaker-notes generator
What it replaces: no evidence line. Inferred from the existence of A1, not from
anything observed or stated.
Leverage arithmetic: unknown. Possibly 15 min per deck x 6 = 90 min/month.
Build cost: 2 hours.
Shippable unattended tonight: yes, but it would need a model call at runtime.
Failure mode: he already knows what he wants to say. Generated notes are
something to read and reject, which is a new task rather than a removed one.
Ceiling: unclear, and that is the point of marking it speculative.

### 9. UNCOMFORTABLE. Deck diff: what changed between v3 and v7
What it replaces: A10 partially - the re-check after every revision.
Leverage arithmetic: 10 min per revision x 8 revisions/month = 80 min/month.
Build cost: 3 hours.
Shippable unattended tonight: yes.
Failure mode: it answers a question nobody asked out loud. Version churn is
tolerated, not tracked, and a tool that surfaces it may just be uncomfortable
reading.
Ceiling: every revision is reviewable as a change rather than as a new file.

### 10. UNCOMFORTABLE, and a rebuke to this whole document. Effort telemetry
What it replaces: nothing. It measures A1-A14 so that next month's build is
chosen on data instead of on the estimates in EVIDENCE.md, twelve of which are
ASSUMED and none of which are measured.
Leverage arithmetic: 0 min/month saved. Negative in month one. Its return is
that it makes every other number on this list real.
Build cost: 1 hour.
Shippable unattended tonight: yes.
Failure mode: it is manual data entry about the work instead of the work. Two
weeks of logging, then silence, then a dataset too small to conclude from.
Ceiling: the leverage arithmetic in this repo stops being guesswork. This is the
most intellectually honest idea here and the least likely to survive contact
with a Tuesday.

---

Speculative count: 1 of 10 (idea 8). Idea 10 cites the absence of evidence
deliberately rather than lacking a citation. Limit was 3.
