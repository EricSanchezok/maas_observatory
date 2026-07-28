# Tool-use benchmark selection record

Review date: 2026-07-28

## Evaluation dimensions

The benchmark review considered three independently reported dimensions:

1. OpenAI-compatible tool-calling protocol conformance;
2. function selection, argument construction, parallel calls, and multi-turn
   tool-result handling;
3. task completion in stateful agent environments.

Benchmark scores remain separate because their datasets, agent harnesses,
verifiers, and metrics differ. Published comparisons require a matching
benchmark release, task split, model configuration, and evaluation protocol.

## Benchmarks included in the initial suite

| Benchmark | Scope | Evaluation method | Role in this repository | External requirements |
|---|---|---|---|---|
| Protocol probe | Five OpenAI-compatible tool-calling cases | Native `message.tool_calls` checks repeated across trials | Protocol conformance and request reliability | Model endpoint |
| [BFCL V4](https://gorilla.cs.berkeley.edu/leaderboard.html) | Simple, multiple, parallel, irrelevance, live, multi-turn, long-context, search, and memory subsets | Pinned EvalScope/BFCL task scorer | Standardized function-calling evaluation with subset-level results | Model endpoint; `SERPAPI_API_KEY` for web-search subsets |
| [Toolathlon-Verified](https://toolathlon.xyz/docs/leaderboard) | Stateful, multi-application tool tasks | Pinned client and programmatic verifier | Long-horizon agent evaluation | Version-attested self-hosted evaluation service |

The exact upstream models in the deployment catalog do not have published BFCL
V4 values under a shared configuration. BFCL results therefore support
within-suite comparisons but do not produce an upstream delta unless an exact
reference is added later.

Toolathlon-Verified publishes same-benchmark values for a subset of the
upstream model families. Legacy Toolathlon/Tool-Decathlon and
Toolathlon-Verified are retained as different score series.

## Additional benchmarks reviewed

| Benchmark | Coverage | Evaluation approach | Integration considerations |
|---|---|---|---|
| [ACEBench](https://github.com/chenchen0103/ACEBench) | Chinese and English Normal, Special, and Agent tasks | Rule-based and simulated multi-turn evaluation over 4,538 APIs | Requires an OpenAI-compatible endpoint adapter |
| [ComplexFuncBench](https://github.com/zai-org/ComplexFuncBench) | Multi-step calls, constraints, implicit parameters, long parameters, and long context | 1,000 samples with automated evaluation | Some modes require RapidAPI or an additional model |
| [ToolSandbox](https://github.com/apple/ToolSandbox) | Stateful tools, implicit dependencies, missing information, and parameter normalization | Intermediate and final state milestones | Requires an OpenAI-compatible model adapter |
| [τ³-bench](https://github.com/sierra-research/tau2-bench) | User-agent-tool interaction over domain databases and policies | Final database state and pass^k | Requires a user simulator and separate environment setup |
| [MCPMark Verified](https://github.com/eval-sys/mcpmark) | Long-horizon CRUD tasks over Notion, GitHub, filesystem, Postgres, and Playwright | Isolated environments with programmatic verifiers | Measures the combined model, harness, and MCP environment |
| [NESTFUL](https://github.com/IBM/NESTFUL) | Nested tool sequences | Executable sequence checks | Narrower scope than a general agent benchmark |
| [HammerBench](https://github.com/MadeAgents/HammerBench) | Multi-turn slot filling and reference resolution | Fine-grained function-call metrics | Requires a separate adapter |
| [StableToolBench](https://github.com/THUNLP-MT/StableToolBench) | Large-scale tool retrieval, selection, and planning | MirrorAPI and StableToolEval | Substantial service footprint |
| [API-Bank](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/api-bank) | API retrieval, planning, and calls | 73 executable APIs and 314 dialogues | Smaller and older task collection |
| [ToolBench](https://github.com/OpenBMB/ToolBench) | Retrieval, planning, and calls over a large API collection | RapidAPI, DFSDT, and ToolEval | External API availability and judge configuration affect reproducibility |

These benchmarks are not aggregated into the initial suite. Adding one requires
a pinned dataset revision, adapter, scorer, resource policy, license record,
and test coverage.

## BFCL integration

The isolated BFCL runtime is pinned to `bfcl-eval==2025.12.17` and
EvalScope 1.2.0. The adapter supports three profiles:

- `smoke`: bounded samples from simple, parallel, and irrelevance;
- `core`: eight single-turn and multi-turn core subsets;
- `full-public`: all 22 public subsets declared by this repository.

Each deployment receives a separate artifact directory. The adapter reads only
per-sample review records, normalizes inference exceptions separately from
model-scored failures, and fsyncs each completed subset.

The BFCL official CLI requires a registered model handler for custom model IDs.
This repository uses EvalScope's OpenAI-compatible path while retaining the
pinned BFCL scorer and dataset revision.

## Experiment structure

### Protocol probe

Each deployment receives three trials of five cases:

- exact arguments;
- irrelevance;
- tool selection;
- parallel calls;
- clarification when required information is missing.

Reported fields include task status, native tool-call conformance, latency,
attempt count, usage when available, and structured error categories.

### BFCL V4

The full-public profile reports every subset separately. The overall row is
marked partial whenever a declared subset lacks complete evidence.

### Toolathlon-Verified

All deployments use the same pinned client, service revision, task-image
digest, worker limit, and trial policy. The adapter refuses the controlled
self-hosted profile when the required service attestations are absent.

### Upstream context

Published model-card and leaderboard values are stored in
`config/baselines.yaml`. A value is contextual unless the benchmark release,
task split, verifier, harness, precision, serving configuration, reasoning
mode, and sampling settings match the evaluated deployment.

## Controls and attribution

The report records:

- deployment identity and precision;
- benchmark and adapter revisions;
- prompt, tool schema, sampling, retry, timeout, and resource policies;
- task-level pass, fail, error, and not-run states;
- transport, protocol, selection, argument, planning, tool-result,
  policy, timeout, and infrastructure boundaries;
- immutable configuration and dependency hashes.

The error category identifies where an observation occurred. It is not, by
itself, a causal statement about model weights, serving software, or external
infrastructure.
