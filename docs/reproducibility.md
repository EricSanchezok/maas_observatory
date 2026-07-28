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
with the hermetic default. Do not hide either limitation in a report.

## Execute

```bash
uv run tooluse-bench run \
  --experiment config/experiments/release-v1.yaml
```

The run manifest records the Git commit, dirty-worktree flag, package and
Python versions, platform, benchmark revisions, selected deployments, lanes,
and SHA-256 digests of configuration and dependency locks. Task records are
appended to JSONL and fsynced. Finalization writes the record count, status
counts, and result-file hash.

Native-transport experiments may set `transport_timeout_seconds`,
`transport_wall_timeout_seconds`, and `transport_max_retries` in benchmark
options. The protocol smoke uses a 90-second inactivity timeout, a 120-second
POSIX wall-clock deadline, and no retry so an unavailable endpoint cannot stall
the diagnostic gate; the release plan retains the transport defaults.

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
models; the three-trial reliability metrics and uncertainty should guide the
comparison.
