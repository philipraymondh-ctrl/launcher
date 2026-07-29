# DECISIONS

Append-only. Each entry: the decision, the reason, what it rules out.

## D1. The project lives in `launcher/leverage`, not a standalone git repo

The brief said `git init` a repo at `./leverage`. The harness assigned branch
`claude/philip-leverage-overnight-gwrro7` in `philipraymondh-ctrl/launcher`,
and this container is ephemeral: it is reclaimed after inactivity and anything
not pushed to a remote is lost. A fresh `git init` with no remote would have
produced a night of work that evaporates.

So: `./leverage` as a subdirectory of the launcher repo, committed to the
designated branch, pushed. Same path the brief asked for, relative to the
working directory, and it survives the night.

Rules out: a clean separate history. Acceptable. The wine scraper and this
tool never import each other, and `leverage/` touches no file above it.

## D2. Node 22 and pptxgenjs 4.x, installed and verified before Phase 1

`npm install pptxgenjs` succeeded through the proxy in 3s. The locked standard
already named pptxgenjs, so this was checked before generating ideas rather
than after picking one, because an idea that cannot install its dependency is
not an idea.

## D3. Validation reads the built file back, it does not trust the builder

`validate-deck.js` unzips the produced `.pptx` and asserts against its XML.
A validator that inspected the in-memory spec would pass a deck that
PowerPoint cannot open. Grounded in EVIDENCE E2.

Rules out: any "done" claim based on the build step exiting 0.

## D4. Text overflow is estimated from Arial metrics, and the estimate is stated as an estimate

pptxgenjs cannot measure rendered text; there is no font engine in the loop.
So overflow detection uses per-character advance widths for Arial (Helvetica
AFM metrics, which Arial matches to within ~1% for Latin text) to wrap and
sum line heights. This is an approximation and the validator says so: it fails
at >100% of box height and warns from 90%, so a borderline case surfaces
rather than silently rendering clipped.

Rules out: claiming pixel-exact overflow detection. It is not that, and
pretending otherwise would be the silent failure E4 warns about.

## D5. The generator refuses to invent content

Any value the author has not supplied renders as a visible `PLACEHOLDER`
token, never as a plausible-looking figure, name or date. A spec field left
blank produces a placeholder and a build warning, not a guess. This is the
locked standard about Novo Nordisk facts, encoded as behaviour rather than
written as prose.

## D6. Writing rules are enforced at build time, not documented

Em-dashes and emojis are rejected by `src/lint.js` during the build, with the
slide and field named. A rule in a README is a rule that drifts.

## D7. Feasibility gate: Deckwright passed all four (Phase 3)

1. *Validated v1 unattended in remaining budget* - yes. Pure Node, no network
   at runtime, dependency already installed and verified (D2).
2. *Machine-checkable acceptance test* - yes. `validate-deck.js` reads the
   built `.pptx` back from disk and asserts slide count, font, palette and
   overflow. Runnable, and provably able to fail: a deliberately-broken deck is
   part of the test suite.
3. *No credential, access or decision needed* - yes. No API, no token, no
   account.
4. *Replaces cited repeated work* - yes. A1, A2, A3, A4, A8, A9, A10, A12.

Runner-up not promoted. Gate not re-run.

## D8. Drift check at increment 5: none

Re-read SELECTION.md as required. What was picked was a spec compiler plus a
validator that works on foreign decks. What exists is a spec compiler plus a
validator that works on foreign decks. No drift.

The one addition beyond the original scope is `.github/workflows/deck.yml`, and
it is not scope creep: SELECTION.md names the Operator's objection (the build
needs a terminal, so a laptop is in the loop) as partially answered, with a
dispatchable build as the answer. This is that answer, and it follows the pattern
already proven in this repo by `dashboard.py`, which exists so the scraper can be
operated from a phone (EVIDENCE E1).

## D9. The pasted spec reaches the workflow through the environment, never
through shell interpolation

`inputs.spec` is arbitrary text from whoever runs the workflow. Interpolating it
into a `run:` line would put attacker-controlled text through a shell parser. It
is passed as an env var and read by Node instead, and the file name is
sanitised to `[A-Za-z0-9._-]` with dot runs collapsed and leading dots stripped,
verified against `../../etc/passwd` and `..` as inputs.
