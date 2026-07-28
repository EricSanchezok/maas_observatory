# Methodology

## Research questions

The evaluation characterizes protocol conformance, function-calling task
success, request reliability, and end-to-end agent task completion for each
configured deployment. A separate comparison layer records differences from
upstream references when the evaluation configurations are comparable.

## Evaluation lanes

The `standardized` lane applies the same task, tool schema, retry policy, and
reporting semantics to every selected deployment.

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
reported independently from long-horizon agent results. The runtime executes
and fsyncs one subset at a time so completed subsets remain available if a
later subset or external service returns an error.

### Toolathlon-Verified

Toolathlon evaluates long-chain use of many real tools with programmatic
verification. The default configuration uses a self-hosted service at a pinned
revision to avoid public-service quotas and uncontrolled changes. Both the
official source commit and the upstream task-container image digest are fixed
in the experiment. Before execution, the operator must attest that the remote
service uses those values. This catches configuration drift but is not a
cryptographic remote-attestation protocol.

## Repetitions and metrics

The release plan performs three trials per protocol-probe and
Toolathlon-Verified task with deterministic trial seeds. Its primary BFCL
comparison is one complete run under deterministic request settings, matching
leaderboard practice; a separate experiment measures BFCL repeatability. The
report includes:

- Pass@1: mean success over recorded task trials;
- Pass@3: fraction of tasks with at least one success in three trials;
- Pass^3: fraction of tasks successful in every expected trial;
- deterministic task bootstrap 95% interval for Pass@1;
- error and not-run counts;
- p50/p95 latency, turns, tool calls, and token use where available.

Pass@3 and Pass^3 are emitted only for experiments with exactly three expected
trials. Other trial counts retain Pass@1 but leave those fields empty rather
than attaching a misleading label.

BFCL aggregate rows retain per-subset coverage and scores. A partially
completed subset is marked partial, and its transport or infrastructure errors
remain separate from model capability failures. The overall row cannot be
presented as complete when any declared subset is missing or partial.
For compatible runs created before per-record BFCL capability labels were
introduced, the report derives the observed boundary from the immutable subset
identifier (arguments, selection, planning, or tool-result integration). The
source task record is not rewritten.

Transport retries are limited to 429 responses, server errors, timeouts, and
transport exceptions. Content or protocol failures are not retried. All failed
and not-run observations remain in the trajectory file.

For the formal BFCL plan, SDK retries are disabled and requests have a
90-second timeout. A predeclared endpoint-health circuit opens when a completed
subset contains at least 50 samples and at least 95% are transport or timeout
errors. Remaining subsets are marked unscored infrastructure skips.
Model-scored failures do not contribute to the circuit-breaker condition.

## Error taxonomy

Errors are categorized as transport, protocol, selection, arguments, planning,
tool-result integration, policy, timeout, infrastructure, or insufficient
protocol support. The category indicates the observed failure boundary, not a
causal claim about model weights.

## Official baselines

Every external score has a source URL, access date, model name, benchmark
release, metric, precision description, reasoning mode, and comparability
label. Only an `exact` record tied to the `official-reproduction` lane, a
compatible deployment, and the exact experiment-configuration SHA-256 may
produce an official delta. The registry currently treats published vendor and
leaderboard figures as contextual until a same-harness reproduction proves
alignment.

No composite across unrelated benchmarks is reported.

## Figures

The release overview is rendered deterministically from the canonical
aggregate JSON. Solid blue bars show observed Pass@1; hatched amber segments
show transport or infrastructure errors; missing evidence remains visibly
unscored. BFCL subset cells preserve partial-coverage markers.

An official score on the same benchmark is shown as a diamond beside the
internal deployment. A filled diamond is reserved for an exact comparison; an
open diamond is contextual. Official scores from different benchmarks are
listed in a separate context panel with their benchmark and metric names. They
never share a computed delta with the internal score.
