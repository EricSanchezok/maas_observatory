# Reproducibility

## Environment

Use Python 3.12 or 3.13 and a frozen `uv` environment:

```bash
uv sync --extra dev --extra docs --frozen
uv sync --project benchmark-envs/bfcl --frozen
uv sync --project benchmark-envs/toolathlon --frozen
```

Copy `.env.example` to `.env` and fill only the variables required for your
authorized deployments. Never pass a key on a command line.

## Validate before running

```bash
uv run tooluse-bench models validate --require-endpoints
uv run tooluse-bench benchmarks validate
```

A BFCL warning about `SERPAPI_API_KEY` means web-search subsets cannot be fully
evaluated. A Toolathlon server error means the official profile is not runnable
with the controlled self-hosted configuration. The Toolathlon service must also
attest the pinned source revision and task-image digest shown in
`benchmark-envs/toolathlon/README.md`. Do not hide any of these limitations in
a report.

## Execute

Validate the actual pinned BFCL runtime on its bounded harness smoke before a
costly release run:

```bash
uv run tooluse-bench run \
  --experiment config/experiments/bfcl-harness-smoke.yaml
```

The smoke uses one deployment, one trial, three subsets, and a limit of three.
It is a harness validation artifact, not a publishable model score.

Before changing release concurrency, run the bounded slow-deployment profile:

```bash
uv run tooluse-bench run \
  --experiment config/experiments/bfcl-throughput-smoke.yaml
```

It uses three subsets, 25 samples per subset, and 25 concurrent requests.
Its only purpose is validating capacity and failure isolation; it is not a
publishable model score.

Then execute the complete plan:

```bash
uv run tooluse-bench run \
  --experiment config/experiments/release-v1.yaml
```

The run manifest records the Git commit, dirty-worktree flag, package and
Python versions, platform, benchmark revisions, selected deployments, lanes,
and SHA-256 digests of configuration and every root/isolated dependency lock.
It also records one combined lock digest. Task records are appended to JSONL
and fsynced. Finalization writes the record count, status counts, and
result-file hash.

Reporting refuses a run without `completion.json` and rechecks the result hash,
record count, status counts, and run IDs. Adapter records are appended as soon
as they are yielded, so a later adapter exception does not erase earlier task
evidence.

Native-transport experiments may set `transport_timeout_seconds`,
`transport_wall_timeout_seconds`, and `transport_max_retries` in benchmark
options. The protocol smoke uses a 90-second inactivity timeout, a 30-second
POSIX wall-clock deadline, and no retry so an unavailable endpoint cannot stall
the diagnostic gate. Real deadline-bound requests run in a clean spawned worker
that the parent can terminate; credentials remain in process memory and never
enter the command line. The release plan explicitly records a 90-second
inactivity timeout, a 60-second wall deadline, no retry, and a 4,096-token cap
for each protocol-probe request. BFCL records a 48-hour benchmark-process
ceiling and Toolathlon records a six-hour ceiling; BFCL fsyncs every completed
subset before continuing.

BFCL formal runs also record `batch_size`, `request_timeout_seconds`, and
`sdk_max_retries` (capped at two). These are passed to EvalScope and its
OpenAI-compatible client. SDK retries apply only to transport/status failures,
not to incorrect model content. The official-comparison BFCL result is one full
run, matching leaderboard practice; repeatability is measured separately
instead of changing the primary score into an unofficial three-run mean.

## Report and publish

```bash
uv run tooluse-bench report build <run-id>
uv run tooluse-bench release build <run-id>
uv run tooluse-bench release validate <run-id>
```

The release builder verifies the private completion hash, recursively redacts
known secrets and private paths, validates every sanitized task record, creates
file checksums, and produces a deterministic tar archive. The release metadata
retains both the private source-result hash and the published compressed-result
hash without exposing the private contents.

Before upload, inspect the complete staging directory manually and record the
archive SHA-256 in the GitHub Release notes.

## Reproducing a public result

Use the release's Git commit and frozen lock files. Recreate the environment,
use an equivalent deployment only if its precision and serving configuration
are documented, rerun the experiment, and compare per-task records before
aggregate metrics. Byte-identical scores are not expected from stochastic
models; the separately reported reliability metrics and uncertainty should
guide the comparison.
