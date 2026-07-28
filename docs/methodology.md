# Methodology

## Research questions

The primary question is whether each evaluated deployment can be integrated
into a standards-based agent. Secondary questions identify where failures
occur and whether internal deployment results differ from a genuinely
comparable upstream control.

## Evaluation lanes

The `standardized` lane applies the same task, tool schema, retry policy, and
reporting semantics to every selected deployment. It supports fair comparisons
within the SII Holos deployment set.

The `official-reproduction` lane is used only when the adapter implements the
benchmark's pinned official harness. A lane name alone does not make an
upstream score comparable.

## Benchmark layers

### Protocol probe

The probe tests exact arguments, refusal of irrelevant tools, tool selection,
parallel calls, and clarification when required information is missing. Only
native `message.tool_calls` are accepted; a textual imitation is a protocol
failure for common agent clients. Probe requests cap output at 4,096 tokens by
default so a simple protocol diagnostic cannot consume a deployment's entire
maximum output allowance.

### BFCL V4

BFCL provides task-level function-calling diagnostics across simple, parallel,
multi-turn, irrelevance, long-context, web-search, and memory subsets. The
adapter delegates evaluation to an isolated pinned runtime. Subset scores are
preserved; BFCL is not treated as proof of long-horizon agent success.

### Toolathlon-Verified

Toolathlon evaluates long-chain use of many real tools with programmatic
verification. The default configuration uses a self-hosted service at a pinned
revision to avoid public-service quotas and uncontrolled changes.

## Repetitions and metrics

The release plan performs three trials per task with deterministic trial seeds.
The report includes:

- Pass@1: mean success over recorded task trials;
- Pass@3: fraction of tasks with at least one success in three trials;
- Pass^3: fraction of tasks successful in every expected trial;
- deterministic task bootstrap 95% interval for Pass@1;
- error and not-run counts;
- p50/p95 latency, turns, tool calls, and token use where available.

Transport retries are limited to 429 responses, server errors, timeouts, and
transport exceptions. Content or protocol failures are not retried. All failed
and not-run observations remain in the trajectory file.

## Error taxonomy

Errors are categorized as transport, protocol, selection, arguments, planning,
tool-result integration, policy, timeout, infrastructure, or insufficient
protocol support. The category indicates the observed failure boundary, not a
causal claim about model weights.

## Official baselines

Every external score has a source URL, access date, model name, benchmark
release, metric, precision description, reasoning mode, and comparability
label. Only an `exact` record tied to a compatible deployment may produce an
official delta. The registry currently treats published vendor and leaderboard
figures as contextual until a same-harness reproduction proves alignment.

No composite across unrelated benchmarks is reported.
