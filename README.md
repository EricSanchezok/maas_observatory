# MaaS Observatory

[![License: Apache-2.0](https://img.shields.io/badge/code-Apache--2.0-blue.svg)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-lightgrey.svg)](LICENSE-DATA)

An open, reproducible repository for checking OpenAI-compatible MaaS responses
and evaluating tool use. The real-time `maas_observatory` service uses
lightweight route checks and real streaming requests; it does not publish
serving-engine metrics. The offline `tooluse_bench` runner is an independent
application. They share only the validated model catalog and neutral
configuration primitives.

> Project status: the harness and release pipeline are under active validation.
> No public model score is claimed until a signed-off release bundle is linked
> from the results page.

[中文说明](README.zh-CN.md) ·
[Methodology](docs/methodology.md) ·
[Reproducibility](docs/reproducibility.md) ·
[Benchmark research](docs/benchmark-research.md)

[Observatory operations](docs/maas-observatory-operations.md) ·
[Observatory backend design](docs/observability-data-backend-design.md)

## Real-time observatory

Build the React interface, then start the single-process service:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv run maas-observatory serve
```

Open `http://127.0.0.1:8080/`. FastAPI serves the compiled interface and the
read-only API from the same origin. For interface development, run
`npm --prefix frontend run dev`; Vite listens on port 5173 and proxies API
requests to port 8080.

Collection defaults to `standard`. Set
`MAAS_OBSERVATORY_COLLECTION_MODE=rapid` only for an attended session; Rapid
requires `MAAS_OBSERVATORY_RAPID_CONTEXT_TIER` (one of `1k`, `16k`, or `64k`)
uses each deployment's output limit from the model catalog. Switch back to
Standard manually after the session. See the
[operations guide](docs/maas-observatory-operations.md) for the exact request
schedule, measurement formulas, schema v5 migration, and public API v7.

## Scope

The harness measures protocol conformance, function-calling task success,
request reliability, and long-horizon agent task completion. Transport,
protocol, model-scored, and external-infrastructure observations are retained
as separate result categories.

The initial evaluation contains three layers:

1. `probe`: five inexpensive OpenAI tool-calling protocol checks;
2. `bfcl-v4`: standardized function-calling diagnostics through a pinned
   EvalScope/BFCL runtime;
3. `toolathlon-verified`: long-horizon agent tasks through the official pinned
   client and a self-hosted evaluation service.

The protocol and long-horizon reliability layers use three trials. The primary
BFCL result uses one complete deterministic-setting run to match leaderboard
practice; repeatability is measured separately rather than changing the
official-comparison metric. Results report applicable reliability statistics,
uncertainty, error categories, latency, and efficiency. No cross-benchmark
composite score is calculated.

## Evaluated deployments

The public catalog contains deployment metadata for DeepSeek V4 Pro/Flash,
GLM 5.2, Qwen3.6-27B, Kimi K2.6, MiniMax M2.7, MiMo V2.5 Pro, and Nex N2 Pro
W8A8/BF16. Private URLs and keys exist only in environment variables.

```bash
cp .env.example .env
# Fill .env locally. It is ignored by Git.

uv sync --extra dev --extra docs --frozen
uv run tooluse-bench models list
uv run tooluse-bench models validate --require-endpoints
uv run tooluse-bench benchmarks list
uv run tooluse-bench benchmarks validate
```

The benchmark runtimes are isolated and independently locked:

```bash
uv sync --project benchmark-envs/bfcl --frozen
uv sync --project benchmark-envs/toolathlon --frozen
```

BFCL web-search subsets require `SERPAPI_API_KEY`. The controlled self-hosted
Toolathlon profile requires `TOOLATHLON_SERVER_HOST` pointing at a server
deployed from the pinned revision and task-image digest documented by the
adapter. Toolathlon still exercises stateful external applications and is not
classified as hermetic.

Run the inexpensive three-trial protocol gate before the complete release plan:

```bash
uv run tooluse-bench run \
  --experiment config/experiments/protocol-smoke.yaml
```

Then validate the pinned EvalScope integration on a bounded real BFCL sample:

```bash
uv run tooluse-bench run \
  --experiment config/experiments/bfcl-harness-smoke.yaml
```

The smoke plan uses a 90-second inactivity timeout, a 30-second POSIX
wall-clock deadline, no transport retry, and a 4,096-token output cap. These
settings are recorded in the experiment. The probe output cap also applies by
default in the release plan, where its predeclared wall deadline is 60 seconds
without retry. Deadline-bound requests run in a clean spawned worker that the
parent process can terminate.

## Run, report, and release

```bash
uv run tooluse-bench run \
  --experiment config/experiments/release-v1.yaml

uv run tooluse-bench report build <run-id>
uv run tooluse-bench release build <run-id>
uv run tooluse-bench release validate <run-id>
uv run tooluse-bench publication build <run-id> \
  --title "Release candidate"
uv run tooluse-bench publication validate
```

Private runs are append-only under `runs/` and never tracked. A release bundle
contains sanitized trajectories, a manifest, completion record, aggregate
metrics, adapter execution audits, report, report-builder provenance, data
license, deterministic SVG/PNG benchmark overview, and SHA-256 checksums.
Execution audits disclose resource controls, process outcomes, and observable
SDK retry counts without publishing credentials or endpoint URLs. The
deterministic archive can be attached to an immutable GitHub Release.

Small, reviewable snapshots under `public-results/` are the only result source
consumed by the Pages site. CI validates their schema, cross-file identities,
record counts, timestamps, checksums, secret scan, and generated results page.
Each snapshot also retains the source release's checksum manifest plus its
exact report and metrics, so derived summaries are traceable to the archive.
The complete sanitized trajectories remain only in that release archive.

The overview figure is generated from validated `metrics.json`, never edited by
hand. Same-benchmark official scores use diamond markers; contextual values
with different releases, precision, or reasoning settings are labeled
separately and never produce a cross-benchmark delta.

Model-catalog `profiles` describe candidate serving or reasoning modes supplied
by the operator. They are not silently activated by the harness. A published
experiment must encode every active request parameter in its benchmark options
or identify the server-side mode as unknown; catalog metadata alone is not
evidence that a profile was reproduced.

## Official comparisons

`config/baselines.yaml` is the machine-readable source registry. Published
vendor and benchmark scores are marked `contextual` by default. The report only
computes an official delta when all of the following are explicitly aligned:

- benchmark release and metric;
- task split, verifier, and agent harness;
- precision and serving configuration;
- reasoning and sampling settings;
- compatible deployment identity.

This prevents, for example, comparing Toolathlon-Verified with legacy
Toolathlon, MCPMark with MCP-Atlas, or a quantized internal deployment with an
upstream score as if they were the same experiment.

## Development

```bash
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
uv run ruff format --check src tests scripts benchmark-envs/*/*.py
uv run ruff check src tests scripts benchmark-envs/*/*.py
uv run mypy src benchmark-envs/bfcl/normalize.py
uv run pytest --cov --cov-report=term-missing
uv run python scripts/check_schemas.py
uv run python scripts/check_public_results.py
uv run python scripts/check_links.py
uv run python scripts/check_licenses.py
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before adding a model or benchmark.
Code is licensed under Apache-2.0. Published reports and result data are
licensed under CC BY 4.0.
