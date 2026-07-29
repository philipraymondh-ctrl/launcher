# Round 2 — ten ideas, a council, three picks

Written after reading today's live run (`30401865128`) and re-running the
real captured pages in `probe_pages/` through the current code, so every
claim below is evidence, not intuition.

## What the evidence says

```
[mareehaute] skipped 2135 sold-out listing(s)
[mareehaute] ok, 19 hit(s) from 3481 product(s)
[leszinzinsduvin] followed 10 producer page(s), 8 listing nothing; 8 product(s), 0 in stock
55 raw producer match(es) this run.
Watched but found nowhere (13): Overnoy/Houillon, Domaine des Miroirs/Kagami,
  Domaine Calice, Thomas Popy, Roumier, Alice Fahrenkrug, Jules Brochet,
  Bruyere Houillon, Allante et Boulanger, Domaine des Murmures,
  Tom Gauditiabois, Lattard, Romain Lawson
```

13 of 16 watched producers "found nowhere" — while one shop alone hid 2135
sold-out listings, and sold-out listings are dropped in `check_shop`
*before* `matched_aliases` ever runs. So that line cannot distinguish a
broken alias from a shelf that is simply empty today. It was added to make
a broken alias visible; as built it can't.

Two more facts from the captures:

- `probe_pages/purewijnen.index.html` (real) contains
  `<a href="/nl/renaud-bruyere-houillon">Renaud Bruyère-Houillon</a>` — and
  `autoselect.find_producer_links` already finds exactly that one link and
  correctly ignores `Overnoy-Crinquand`. purewijnen is dark for a reason
  that no longer holds.
- `probe_pages/capture.domaine-6-193-ganevat-jean-franecois-html.html`
  (real, leszinzinsduvin's Ganevat page) contains the words "Les vins de
  Ganevat Jean françois" and then *nothing*: no `vin-NNNN` links, no `€`.
  The 8-of-10 empty grower pages are real emptiness, not a parse failure.
  Nothing to fix there.

## The ten ideas

1. **Match sold-out listings too, and say which case it is.** "Found
   nowhere" today means "found nowhere in stock". Split it into *matched
   nowhere at all* (alias suspect) and *matched, every listing sold out*
   (real scarcity) — and name the shops in the second case.
2. **Separator-insensitive matching.** `normalize()` strips accents and
   lowercases, nothing else, so `Bruyère-Houillon`, `Allanté & Boulanger`
   and `l'Allanté` need hand-written alias variants. `PRODUCERS` already
   carries `overnoy-houillon`, `houillon-overnoy`, `bruyere-houillon` for
   exactly this, i.e. a maintenance cost per producer per punctuation
   style.
3. **Near-miss diagnosis for producers matched nowhere.** Search the run's
   own text for tokens one edit away from a watched alias, and name them.
   Turns "found nowhere" from a mystery into "'gauditiabois' vs
   'gaudiciabois' at cavepurjus".
4. **Reach purewijnen through the producer index** (evidence above), and
   record what the other three dark shops actually serve rather than
   leaving them as "needs work".
5. **Pin the restock alert.** A listing that was sold out and comes back
   alerts *by accident*: sold-out items never enter `seen.json`, so
   `prev is None` and it reads as new. Nothing tests that. The single most
   valuable alert for allocated wine rests on a side effect.
6. **Audit the text stock heuristic against platform truth** where a shop
   gives both, and report disagreements. 61% of mareehaute reads as sold
   out; if the text backstop over-fires anywhere, real finds vanish.
7. **Price history in the digest row** ("EUR 45, was EUR 52 on 12 Jul")
   from `observations.json`.
8. **Retire the genuinely unreachable shops** (vinscheznous no longer
   resolves, naturavin 403s) instead of carrying them as pending work.
9. **Scheduled auto-probe** so platform drift is caught before it zeroes a
   shop for a week.
10. **Surface the run's notes on the dashboard** so the phone shows state
    without waiting for an email.

## The council

**The scraping engineer.** 4 first: coverage is the product. purewijnen
stocks Bruyère-Houillon *today* and we are not looking. But it needs a
live probe to confirm, and 2 makes matching work on the page it will find.

**The data skeptic.** 2 is the one that scares me, in both directions.
Collapsing punctuation widens every alias at once — "Overnoy, Houillon" in
one text block becomes "overnoy houillon". I want it done as one shared
comparison rule, not four copies, and I want the namesake cases from
`probe_pages` in the tests: `Overnoy-Crinquand` and `Renaud
Bruyère-Houillon` sit in the same real `<ul>` and must land on different
sides. 6 is my other worry but there is no evidence of harm yet —
mareehaute is a platform-truth shop, so its 2135 come from `available`,
not from the heuristic.

**The operator.** 1, 2, 3 and 5 cost zero extra requests: they all read
data the run already has in memory. 4 costs a probe and a handful of
grower pages. 9 costs 24 probes a day to detect something the DRIFT line
already reports for free — no. 7 and 10 are new surface for no new
knowledge.

**The collector.** I do not care how many products parsed. I care that
Ganevat and Overnoy exist somewhere I can buy them. 1 is the one that
changes my behaviour: "sold out at mareehaute and petitescaves" tells me
where to watch, and it is the difference between "your list is broken" and
"the wine is gone". 5 protects the only alert I would ever act on
instantly.

**The test archaeologist.** Every bug in this repo was a silent one, and
two of these are silent *right now*: the found-nowhere line is misleading
(1), and the restock alert is an accident nobody asserts (5). Fix the
first, pin the second in the same change — they are the same seam.

### Verdict

- **A — Coverage honesty (1, with 5 folded in).** Match every parsed
  listing; split absent from sold-out; name the shops; pin the restock
  behaviour with a test so recording sold-out state can never silently
  kill it.
- **B — One comparison rule, separator-insensitive, plus near-miss
  diagnosis (2 + 3).**
- **C — purewijnen through the producer index, and the truth about the
  other three (4).** Offline: a test over the real captured index. Live: a
  probe run, which is the only thing allowed to flip `verified`.

Not chosen: 6 (no evidence of harm; mareehaute's count is platform truth),
7, 8, 9, 10 — all either new surface for no new knowledge, or work the
DRIFT line and the new recap already do.

---

# Implementation plan

## A. Coverage honesty

- `check_shop` runs `matched_aliases` on **every** parsed item, not only
  in-stock ones. In-stock matches become hits as today; out-of-stock
  matches go into a second list.
- `ShopResult` gains `sold_out` alongside `products_parsed`. Still a
  `list` subclass, so its fifteen callers keep working.
- Sold-out matches never become hits: not evaluated, not in `hits.json`,
  not in the market pool, and above all **not written to `seen.json`** —
  that last one is what keeps a restock reading as new.
- `main()` derives three sets: `found` (in stock), `sold_out_only`
  (matched, nothing in stock), `unseen` (matched nowhere at all). The
  existing "Watched but found nowhere" note keeps its name and finally
  means what it says; a new "Matched but sold out everywhere" note names
  producer → shops.
- Both notes go to the digest and the recap through the existing `notes`
  dict, which renders only non-empty blocks.

## B. One comparison rule

- `normalize()` exists **four times** — `scraper`, `market`, `evaluate`,
  `apply_issue` — each an identical accent-strip-and-lower, each with a
  comment explaining why it was copied. Changing matching in one of them
  is how a producer added through the issue form silently never matches.
  So: one `textnorm.py`, imported by all four (and `autoselect`, which has
  its own `_strip_accents`).
- The rule: NFKD, drop combining marks, lowercase, `&` → ` et `, every
  remaining non-alphanumeric → space, collapse runs of space. Aliases and
  haystacks both go through it, so `bruyere houillon` matches
  `Bruyère-Houillon` with no per-producer variant.
- `PRODUCERS` is left exactly as it is. The redundant hyphen variants are
  harmless; the point is that the *next* producer needs none.
- Near-miss: `near_misses(unseen, corpus)` where `corpus` is
  `{shop: set(tokens)}` accumulated during the run. For each unseen
  producer, take the tokens of its longest alias with ≥6 characters and
  report corpus tokens at edit distance 1, sharing the first character,
  within one character of the same length. Capped, and only computed for
  producers that matched nothing — usually a handful.
- It goes in the notes as `Alias near-misses`, worded as a suspicion, not
  a claim.

## C. purewijnen

- Offline: trim the real `probe_pages/purewijnen.index.html` into a test
  fixture and assert `find_producer_links` returns the Bruyère-Houillon
  link and *not* Overnoy-Crinquand. That is a namesake test on real
  markup, which is worth more than the synthetic one we have.
- Live: dispatch `probe.yml` for the four dark shops. Only `probe.py
  --apply` may set `verified`/replace a fixture, so nothing here flips a
  shop by hand. If the probe cannot read purewijnen's grower pages, that
  is the answer and it gets written down instead of implemented around.

## Order of work

1. Tests for A and B, run, confirm failure.
2. `textnorm.py` and the four call sites, then A, then near-miss.
3. Whole suite green.
4. Re-read every changed file against this plan.
5. Live: probe dispatch (C) and a `DRY_RUN` scraper dispatch on this
   branch, to see the new notes against real catalogues.

## Risks and containment

| risk | containment |
|---|---|
| collapsing punctuation widens an alias into a namesake | real-markup namesake tests from `probe_pages`; longest-alias rule unchanged |
| four `normalize`s drift again | one module; a test asserts each caller exposes the same object |
| sold-out matches leak into hits/state and kill restock alerts | asserted directly: no `seen.json` entry for a sold-out key, and a restock alerts |
| the new notes make every digest noisy | rendered only when non-empty, exactly like the existing two |
| near-miss floods the note with noise | only for producers matched nowhere, tokens ≥6 chars, distance 1, capped |
| `market.cuvee_tokens` behaves differently under the new rule | it splits on whitespace after normalising, so collapsing punctuation to space is what it already wanted; covered by existing market tests |
| a `ShopResult` field addition breaks a caller | it stays a `list`; `probe.py` reads `products_parsed`, which is unchanged |

## Definition of done

- Suite green (357 before this change).
- No new dependency. One new module, documented in CLAUDE.md.
- Live run on this branch shows the new notes and no regression in shop
  coverage.

---

# PLAN REVIEW — three corrections found by reading the code first

## R1. Consolidating `normalize` as-is would break vintage parsing

The plan said "one rule, imported everywhere". Checked what
`market.normalize` actually feeds: `VINTAGE_RE`, whose whole job is to tell
a vintage from a price using currency markers —
`(?<![\d€$£.,])(19[5-9]\d|20[0-4]\d)(?![\d.,]*\s*(?:€|eur|usd|\$))`.
Collapse every non-alphanumeric to a space and `2018,50 €` becomes
`2018 50`, so the lookahead that currently blocks it sees nothing and the
price reads as a vintage. That is the repo's oldest rule ("never treat a
bare 4-digit number as a price") broken from the other end.

**Corrected:** `textnorm.py` exposes *two* functions, not one.

- `strip_accents(text)` — NFKD, drop combining marks, lowercase. Byte-for-
  byte what all four copies do today, so importing it changes no
  behaviour anywhere; it is pure de-duplication.
- `match_key(text)` — `strip_accents` plus `&` → ` et `, remaining
  non-alphanumerics → space, runs of space collapsed. Used **only** where
  a name decides a match.

`market` and `evaluate` keep their current semantics (via
`strip_accents`); `VINTAGE_RE`, `BUNDLE_RE` and the cru patterns never see
`match_key`.

## R2. Only two call sites actually need the new rule

Which places compare a *name*? `scraper.matched_aliases` (alias vs listing
text) and `apply_issue`'s alias derivation (which must agree with it, or a
producer added through the issue form never matches anything — a silent
failure with a two-week feedback loop). `market.cuvee_tokens` also
normalises an alias, but only to strip the producer's name out of a title
as a best effort; widening it there buys nothing and risks the token
comparison the module exists for.

**Corrected:** `match_key` at those two sites. A test asserts they agree.

## R3. The near-miss corpus does not need to be a corpus

The plan had `main()` accumulating every token from every shop, then
filtering after the fact — up to ~10k titles, kept for the one case where
a producer matched nothing.

**Corrected:** filter at collection. The watched aliases are known up
front, so a token is only worth keeping if some alias token shares its
first character and is within one character of its length. That reduces
the per-shop set to a handful of candidates, and the near-miss pass then
only has to consider tokens that could possibly be one edit away.

## Unchanged after review

A is exactly as planned: the sold-out path stays out of `hits.json`, out of
the market pool and out of `seen.json`, and the restock behaviour gets the
test it never had. C stays a probe's decision, not a hand edit.


---

# OUTCOME — what the live runs decided

## A and B: verified against real catalogues

Two `DRY_RUN` dispatches on this branch (runs 30403926578 and 30404303597),
which fetch for real, send nothing and consume no cooldown.

```
Matched but sold out everywhere (1): Domaine des Murmures [mareehaute]
Watched but found nowhere (12): Overnoy/Houillon, ... Romain Lawson
Shops that returned nothing (1): vinnaturel
```

Both notes do what they were built for, and "found nowhere" is 12 instead
of 13 because Murmures is now correctly described as stocked-but-empty.

The runs also caught two things the fixtures could not, both now fixed with
tests quoting the evidence:

1. The digest's only DEAL was a shopping cart -- "Voir mon panier", EUR 0,
   at `/commande`, scored against a EUR 99 reference. A zero is not a price.
2. The near-miss hint fired on ordinary French, twice: `'pierres'` at five
   shops, then `'pierra'`, `'pierro'`, `'domain'`, `'malice'` at one shop
   each. Both ends now have to be words the corpus does *not* show at
   several shops, and the target floor is seven letters.

A consequence worth stating: with the cart no longer counted, vinnaturel
parses zero products and appears in the DRIFT note. That is honest -- it
was never reading that shop's catalogue, only its furniture.

## C: the probe said no, with evidence

Run 30403841248 probed all four dark shops with `--apply` and verified
none. purewijnen was the promising one -- its landing page really is a
grower index and `find_producer_links` really does find Renaud
Bruyère-Houillon in it. So the deciding page was captured directly:
`probe_pages/capture.nl-renaud-bruyere-houillon.html`, 28KB, **zero
currency markers**. It is a producer bio with no wine list.

So the producer-index route was never the missing piece for these four, and
the shops stay unverified. Written into CLAUDE.md with the run ids, because
"needs a probe" was the standing note for months and this is what a probe
actually said.
