# Standing decisions
Edited by the human only. The proxy reads, never writes.

- verified: true only when a real HTTP response was received and parsed in
  this run. Never inferred.
- Shop fetch failure: retry twice with backoff, mark failed, continue.
- Preflight fails: abort the whole run, touch nothing.
- Fixtures are replaceable. Schema changes are not, escalate.
- No progress reports mid-run. One final table.
