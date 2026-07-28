# SII Holos Tool-use Bench

[![License: Apache-2.0](https://img.shields.io/badge/code-Apache--2.0-blue.svg)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-lightgrey.svg)](LICENSE-DATA)

A reproducible evaluation harness for tool use on OpenAI-compatible model
deployments operated by SII Holos. It separates transport and protocol failures
from function-calling accuracy and end-to-end agent performance.

> Project status: the harness and release pipeline are under active validation.
> No public model score is claimed until a signed-off release bundle is linked
> from the results page.

[中文说明](README.zh-CN.md) ·
[Methodology](docs/methodology.md) ·
[Reproducibility](docs/reproducibility.md) ·
[Benchmark research](docs/benchmark-research.md)

## Why this repository exists

An HTTP 200 response does not mean a deployment can be used by an agent. A
serving stack may emit a tool call as plain text, invalid JSON, or a
provider-specific format that standard clients cannot consume. This project
records those failures instead of silently retrying or dropping them.

The initial evaluation has three layers:

1. `probe`: five inexpensive OpenAI tool-calling protocol checks;
2. `bfcl-v4`: standardized function-calling diagnostics through a pinned
   EvalScope/BFCL runtime;
3. `toolathlon-verified`: long-horizon agent tasks through the official pinned
   client and a self-hosted evaluation service.

Every selected task is run three times in the release plan. Results report
Pass@1, Pass@3, Pass^3, uncertainty, error categories, latency, and efficiency.
No cross-benchmark composite score is calculated.

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

BFCL web-search subsets require `SERPAPI_API_KEY`. The hermetic Toolathlon
profile requires `TOOLATHLON_SERVER_HOST` pointing at a server deployed from
the pinned revision documented by the adapter.

Run the inexpensive three-trial protocol gate before the complete release plan:

```bash
uv run tooluse-bench run \
  --experiment config/experiments/protocol-smoke.yaml
```

The smoke plan uses a 90-second inactivity timeout, a 120-second POSIX
wall-clock deadline, no transport retry, and a 4,096-token output cap. These
settings are recorded in the experiment. The probe output cap also applies by
default in the release plan.

## Run, report, and release

```bash
uv run tooluse-bench run \
  --experiment config/experiments/release-v1.yaml

uv run tooluse-bench report build <run-id>
uv run tooluse-bench release build <run-id>
uv run tooluse-bench release validate <run-id>
```

Private runs are append-only under `runs/` and never tracked. A release bundle
contains sanitized trajectories, a manifest, completion record, aggregate
metrics, report, data license, and SHA-256 checksums. The deterministic archive
can be attached to an immutable GitHub Release.

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
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy src
uv run pytest --cov=tooluse_bench --cov-report=term-missing
uv run python scripts/check_schemas.py
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before adding a model or benchmark.
Code is licensed under Apache-2.0. Published reports and result data are
licensed under CC BY 4.0.
