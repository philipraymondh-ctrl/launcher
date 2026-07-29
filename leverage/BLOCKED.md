# BLOCKED

Nothing hit the three-attempt limit. Everything the build loop attempted
shipped, and the failures found along the way are recorded in PROGRESS.md and
DECISIONS.md rather than here.

What follows is not a list of questions. Each item is a decision that is yours
to make, stated with what happens if you make it either way, and with the
default that is in the repo right now.

## 1. The palette is a guess about your brand, and it is one file

**In the repo now:** navy `#001965` as specified, plus nine supporting colours I
chose (`src/theme.js`): an ink, a muted grey, a rule grey, two panel greys, a
white, and red, amber and green for status.

**The decision:** if Novo Nordisk's real palette differs, replace
`theme.PALETTE`. The validator then enforces the new set on every deck
automatically, including on inherited decks, with no other change anywhere.

**If you leave it:** decks are internally consistent and off-brand in the
supporting colours. The navy, which is the one that gets noticed, is correct.

## 2. Overflow is estimated, and it has never been checked against real PowerPoint

**In the repo now:** Arial advance widths from Helvetica metrics, which Arial
matches to about 1%. The decks were rendered and inspected through LibreOffice,
which substituted Liberation Sans. Liberation Sans is metrically identical to
Arial by design, so that check is meaningful, but it is not PowerPoint.

**The decision:** either accept the 1% band, which the thresholds are built to
absorb (error above 100%, warning from 92%, components target 85%), or open one
built deck in real PowerPoint once and confirm nothing clips. Ten minutes,
settles it permanently.

**If you leave it:** the risk is a slide that clips by a hair and passes. The
warning band exists to surface exactly that case, so you would see it flagged
rather than shipped, which is why this is not on the blocked list proper.

## 3. The deck workflow runs on every push that touches `leverage/**`

**In the repo now:** `.github/workflows/deck.yml` triggers on push to any branch
under that path, and on manual dispatch with a pasted spec.

**The decision:** if that is too much Actions minutes for a repo that already
runs an hourly scraper, narrow `branches` to `main` and rely on manual dispatch
for everything else. One line.

**If you leave it:** every spec edit produces a validated deck as a run
artifact, which is the phone path working as intended, at the cost of a run per
push.

## 4. En-dashes warn, they do not fail

**In the repo now:** an em-dash fails the build. An en-dash warns, because
`Q3–Q4` is legitimate typography and failing it would make the rule feel
arbitrary.

**The decision:** if you want none of either, change one line in `src/lint.js`
and the test in `test/lint.test.js` that asserts the warning behaviour.

## 5. What gets built next is a real fork, and both branches are already argued

**The decision:** fan-out (IDEAS.md idea 3, ranked third) or the extractor
(idea 5, ranked fourth).

Fan-out multiplies the saving by the number of markets and is the only route
BASELINE.md identifies to a whole-task ratio above 3x, because it changes the
denominator. It is worth roughly 2 hours of build on top of what exists. The
extractor turns decks you receive into specs you can edit, which is the only
thing on the list that helps with work you did not originate.

**Default if you say nothing:** fan-out, for the reason in BASELINE.md.
