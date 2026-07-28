# PLAN — the tracker went quiet, and the quiet is a bug

## What the runs actually say

Nothing is broken. Run 30401865128 (21:44 UTC today, `success`):

```
55 raw producer match(es) this run.
54 listing(s) recorded; 106 in the reference pool.
No newly alert-worthy hits this run (cooldown or no change) -- silent run is valid.
```

Every bottle currently on those shelves was emailed once already. Since
then `should_alert` has answered "no" for all 55 of them, every hour, for
the same reason: they are inside the 30-day cooldown.

## The defect: the cooldown suppresses news, not repetition

Read the rule as implemented:

```
prev is None            -> alert
within 30 days          -> no
otherwise               -> only a >10% drop or an improvement to DEAL
```

An *unchanged* item never re-alerts, cooldown or no cooldown — the third
branch requires news. So the cooldown window is not what stops the digest
repeating itself; the news requirement is. The only thing the 30-day gate
actually does is **delay real news by up to a month**. A wine we already
reported at EUR 100 dropping to EUR 60 tomorrow is silenced until late
August. That is the exact failure mode this project exists to avoid, and
it is what "it doesn't send anything anymore" looks like from the inbox.

`test_price_drop_over_10_percent_re_alerts_after_cooldown_window_not_needed`
asserts the current behaviour deliberately, so this is a decision being
reversed, not a bug being patched. The reversal is narrow.

### A. A price drop always alerts

```
prev is None                     -> alert
price <= last_alerted * 0.9      -> alert   (new; cooldown does not apply)
within 30 days                   -> no
otherwise                        -> improvement to DEAL
```

Bounded by construction, no new state: the comparison is against
`last_alerted_price`, which every alert resets. A monotonic decline alerts
once per further -10% step; an oscillation between EUR 60 and EUR 50
alerts once and then never again, because 50 is not ≤ 45.

Classification stays behind the cooldown on purpose. It is *derived* from
the observed market pool, which shifts every hour as other shops are
crawled, so DEAL→FAIR→DEAL flapping is realistic in a way a price
round-trip is not.

## B. Silence is indistinguishable from breakage

Even with A, a week where nothing new appears and nothing drops sends
nothing — correctly — and the owner cannot tell that apart from a broken
adapter, expired secrets, or a workflow that stopped firing. They asked
exactly that question today.

So: **if nothing has been emailed for 7 days and there are hits, send a
recap** of everything currently matched.

- New reserved key in `seen.json`: `_meta.last_recap_at`. Keys are sha256
  hex, so `_meta` cannot collide with an item, and `select_alerts` never
  touches it. `notify.py` is the only reader of that file.
- Any digest that goes out resets the clock — the promise is "you hear
  from it at least weekly", not "you get an extra email weekly".
- A recap **must not** mark anything alerted. Marking is what silences an
  item, and a recap is not a find. It refreshes `last_price` only, exactly
  as a silent run already does.
- No hits at all -> still silent. There is nothing to recap, and the
  weekly clock is not a heartbeat for the workflow.
- Same table, an extra first line saying it is a recap, and its own
  subject so it is filterable.

## Order of work

1. Tests first (both changes), run, confirm they fail.
2. Rewrite the one existing test that pins the reversed decision, and say
   in it why it was reversed.
3. Minimal implementation.
4. Whole suite green.
5. Re-read every changed file against this plan.

## Risks and containment

| risk | containment |
|---|---|
| price-drop alerts every hour | baseline is `last_alerted_price`, reset on each alert; a further -10% is required |
| recap consumes the cooldown | recap path saves the same state a silent run does, plus `_meta`; asserted |
| recap fires weekly *and* a digest goes out | one clock, reset by both paths |
| `_meta` mistaken for an item | keys are sha256 hex; `select_alerts` only writes keys it computed |
| a failed send resets the clock | `save_state` stays after `send_email`, unchanged |
| `send_email` gains a parameter | `subject` is keyword-with-default; stubs in tests updated to `**kw` |

## Definition of done

- Suite green (335 before this change).
- No new file, no workflow change, no new dependency.
- Every changed file re-read against this plan.

---

# SELF-AUDIT — two things the plan did not anticipate

Both surfaced while making the tests pass, and both are in the diff.

## 1. The silent branch persisted state even under `DRY_RUN`

`run_digest` returned early on both sending paths, so "a dry run leaves no
trace" held for them — but the *silent* branch called `save_state`
unconditionally, refreshing `last_price` for every hit. Harmless in effect,
wrong as a rule, and now the recap depends on that file, so it was worth
closing rather than documenting. One condition, plus a test that fails
without it (verified by reverting the guard).

## 2. State that predates this change recaps immediately

The `seen.json` in the Actions cache has no `_meta`, so `recap_due` has
nothing to measure a week from and returns True. The first run after this
merges therefore sends a recap of all ~55 currently matched hits rather
than waiting seven days.

That is the right reading, not a bug to paper over: that state *is* the
situation the recap exists for — a full shelf, everything in cooldown, days
of silence. Pinned in
`test_state_that_predates_the_recap_gets_one_promptly` so it can't change by
accident.

## Verified against the plan

- `should_alert`: drop before the cooldown gate, classification after it.
- Recap marks nothing alerted (asserted on `last_alerted_at` directly).
- One clock, reset by both paths; a failed send resets neither.
- `save_state` still strictly after `send_email`.
- No hits -> still silent, however long it has been.
- No workflow change, no new file, no new dependency.
- 357 tests pass (335 before, 21 added, 1 rewritten).
