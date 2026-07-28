# Adding models and benchmarks

## Add a deployment

Add one entry to `config/models.yaml`. Use stable lowercase identifiers and
environment-variable references for the URL and key. Never put either value in
YAML. Document the upstream source, precision, context/output limits,
modalities, serving engine, chat template, reasoning parser, and tool-call
parser. Use `unknown` when evidence is unavailable.

Then update `.env.example`, regenerate schemas, and run:

```bash
uv run tooluse-bench models validate
uv run pytest
```

## Add a benchmark

Implement `BenchmarkAdapter`, including:

- immutable metadata with an exact version or source revision;
- supported profiles and lanes;
- preflight validation for required infrastructure;
- one schema-valid `TaskResult` for every attempted or explicitly unrun task;
- stable task IDs and a documented scoring mapping.

Register the adapter under the `tooluse_bench.benchmarks` entry-point group in
`pyproject.toml`. Heavy or conflicting dependencies belong in a separate
`benchmark-envs/<name>/` project with its own lock file and a minimal bridge.
Subprocess environments must be allow-listed so unrelated secrets are not
inherited.

Tests must cover success, malformed output, subprocess failure, timeout, missing
infrastructure, and redaction of adapter artifacts.

## Comparability review

Changing a dataset revision, verifier, agent prompt, maximum turns, timeout,
reasoning mode, sampling setting, or judge model creates a new score series.
Record it as a new benchmark release. Do not edit an old baseline to make it
appear compatible.
