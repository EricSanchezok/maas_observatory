# Capacity validation

## BFCL 25-way validation — 2026-07-28

This is an execution-capacity diagnostic, not a model benchmark result. It used
the slowest observed deployment to validate that 25-way concurrency remains
bounded and produces complete BFCL records before selecting a formal-run
setting.

- Run ID:
  `20260728T074910616047Z-bfcl-throughput-smoke-25-v1-05725e5b-54372423`
- Git commit: `05725e5b87317e016c62d2376fac854925d80366`
- Clean working tree at launch: yes
- Sample: 25 records from each of `simple_python`, `parallel`, and
  `irrelevance`
- Duration: 630.66 seconds
- Effective throughput: 7.14 scored records/minute
- Outcomes: 67 pass, 8 capability fail, 0 infrastructure error
- Observable OpenAI SDK retry log entries: 4
- Private result SHA-256:
  `78d136ed0e0d375d46d2d8d6d94efc6301526cfe743a4dbe4c4daaa7db689669`

The experiment declared `batch_size=25`, `request_timeout_seconds=180`,
`sdk_max_retries=2`, and a 1,800-second outer process deadline. A subsequent
source audit found that the pinned EvalScope BFCL adapter instantiated a second
OpenAI client inside `bfcl_eval`: the batch size was effective, but the declared
request timeout and retry settings were not forwarded to that client. Its SDK
default happened to use two retries, while its request timeout remained an
implicit default. Therefore this run validates only the 25-way capacity choice,
not the formal transport bounds.

The harness now injects the declared timeout and retry maximum into that exact
BFCL client, disables its separate stop-never RateLimit backoff, and records the
effective policy in a releaseable execution audit. Compared with the earlier
ten-way development diagnostic, effective throughput improved by about 72%,
but the observed long tail means a simple linear full-run time estimate is only
a lower-bound capacity check. Multi-turn, memory, and web-search tasks can
require additional model or tool interactions.

An unpublished end-to-end audit run later demonstrated a different failure
mode: a deployment became unreachable after several completed subsets, and the
48-hour per-model ceiling permitted thousands of redundant transport attempts.
Before the public run, the protocol was therefore frozen at a 90-second request
timeout, zero SDK retries, and a 12-hour process ceiling. It also gained the
predeclared 50-sample/95%-transport-error circuit breaker described in the
methodology. This operational audit is not used as a model score.
