# Deckwright

A markdown spec that fits on a phone screen compiles to a standards-clean
`.pptx`. A validator reads the built file back off disk and proves the standards
held, on any deck, including ones this tool did not build.

```
$ deck build specs/steering-review.md
deck build: out/steering-review.pptx
  11 slide(s) in 92ms
     1  title         Analytics Capability Rollout
     2  summary       Where we stand
     ...
  22 PLACEHOLDER(s). Nothing here was invented; replace them before sending.

validate-deck: steering-review.pptx
  PASS  opens        221KB package, 11 slide part(s)
  PASS  slide count  11, as expected
  PASS  theme fonts  major and minor Latin fonts are Arial
  PASS  fonts        every typeface in every slide is Arial
  PASS  colours      every colour is in the palette (navy #001965 plus 9 others)
  PASS  bounds       every shape sits inside the slide
  PASS  overflow     127 text boxes measured, fullest at 83%
  PASS  writing      no em-dashes, no emojis

  VALID
```

## Install

```bash
cd leverage
npm install
npm test          # 80 tests, no network
npm run examples  # builds and validates every spec in specs/
```

Node 22, one runtime dependency (pptxgenjs) plus jszip for reading decks back.
No network calls at build or validate time. No model calls.

## Commands

```bash
node bin/deck.js build SPEC.md [-o OUT.pptx]   # compile, then validate. Non-zero if invalid
node bin/deck.js validate DECK.pptx            # validate any .pptx, including a foreign one
node bin/deck.js lint SPEC.md                  # writing standards only, no build
node bin/deck.js new -o my-deck.md             # a starter spec to edit
node bin/deck.js components                    # list slide types
node validate-deck.js DECK.pptx --json         # machine-readable report
node tools/render.js OUT.pptx                  # one PNG per slide (needs LibreOffice)
```

`build` runs the validator itself and exits non-zero when it fails. There is no
path through the CLI that reports success on an unvalidated deck.

## The spec format

Four markers, all of which are typeable with thumbs.

```markdown
# Deck title                    <- also accepts "title:"
subtitle: Steering review
presenter: Philip Raymond
footer: Internal

## summary: Where we stand      <- "## type: Title" starts a slide
verdict: One sentence. The verdict, not the context.
- a supporting point           <- "- " is a bullet
- another

## roadmap: Delivery phases
### Q3 2026 | Foundations       <- "### Name" is a group. A pipe splits off a label
- contracts agreed

## raid: Risks and decisions
headers: Type | Item | Owner | Status
| Risk | Residency sign-off outstanding | PLACEHOLDER | Red |
```

- `## Title` with no type is a bullets slide, the common case
- keys are case and space insensitive: `Label:`, `label :` and `label:` agree
- a title slide is added automatically from the deck title. `titleslide: no`
  suppresses it
- a `headers:` line is a field even though its value contains pipes. A row
  starting with `|` is always a row, even when a cell contains a colon
- an unknown slide type is an error with a line number, not a guess

## Slide types

| type | what it draws |
|---|---|
| `title` | full-bleed navy opener |
| `divider` | section marker on a navy band |
| `summary` | verdict panel, then supporting points |
| `bullets` | a list. The default when a heading gives no type |
| `columns` | two to four columns, one per `###` group |
| `flow` | process steps left to right, wrapping above six |
| `roadmap` | phased plan, one band per `### period \| phase` group |
| `capabilities` | capability map, one row per `###` layer |
| `raid` | table from `\| a \| b \|` rows, last column read as a status |
| `stakeholders` | influence and interest grid |
| `metric` | one number at 86pt, a label, a source note |

A status cell reading red, amber, green, blocked, at risk, on track, live and
several other spellings is colour coded automatically. That is the formatting
step that otherwise gets done by hand every week.

## What the validator checks

| check | what fails it |
|---|---|
| opens | not a zip, no `ppt/presentation.xml`, no slide parts, empty file |
| slide count | disagrees with `--slides N`, `--spec FILE`, or the build manifest |
| theme fonts | the presentation theme's major or minor Latin font is not Arial |
| fonts | any run typeface in any slide is not Arial |
| colours | any `srgbClr` outside `src/theme.js`'s palette |
| bounds | any shape outside the slide, rotation accounted for |
| overflow | any text box whose text needs more height than the box has |
| writing | an em-dash or an emoji in the text as written to the file |

`test/validate.test.js` builds decks that are deliberately wrong in each of those
ways and asserts the matching check catches each one. A validator that cannot
fail is not a validator.

## The estimator, and what it cannot do

There is no font engine in this pipeline, so overflow is estimated:
`src/text/metrics.js` holds Arial advance widths (Helvetica metrics, which Arial
matches to about 1% for Latin text), wraps greedily, and sums line heights.

The thresholds are set so the approximation fails loudly rather than quietly.
Above 100% of the box is an error. From 92% it warns, because that is where a 1%
metric error changes the answer. Components aim for 85% fill, deliberately below
the warning band, so a warning means something unusual rather than something
routine.

**Rendering catches what geometry cannot.** Three real defects survived a clean
validation run and were only visible once rendered through LibreOffice: a
rotated axis label sitting in the middle of the grid it labelled, process-flow
boxes sized from the region rather than from their content, and bullet lists
top-aligned in a region twice their height. Every one of those shapes was inside
the slide and inside its own box. If you change a component's layout, run
`tools/render.js` and look at it.

## Never invented

A field the author did not supply renders the literal token `PLACEHOLDER` and
raises a build warning. There is no fallback that guesses a plausible figure,
name or date. The build prints how many placeholders the deck contains so it
cannot be sent with them still in.

## Extending it

1. Add the component to `src/components/index.js`. Take a region from
   `theme.REGIONS` and subdivide it. Never hardcode a y position: the day a title
   wraps to two lines every such constant is wrong.
2. Add the type to `TYPES` in `src/spec/parse.js`.
3. Add a case to `CASES` in `test/components.test.js`, with content that
   overfills it. A test asserts every component in the library has one, so this
   is not optional.
4. `npm test && npm run examples`, then `tools/render.js` and look at it.

Read `CLAUDE.md` for the rules that are not negotiable.

## Project files

The reasoning that produced this tool rather than another one:
`EVIDENCE.md` (what is actually repeated), `IDEAS.md` (ten candidates),
`COUNCIL.md` (six adversarial reviewers, six vetoes), `SELECTION.md` (why this
won and what objection it never answered), `BASELINE.md` (measured, not
asserted), `DECISIONS.md`, `PROGRESS.md`, `HANDOVER.md`.
