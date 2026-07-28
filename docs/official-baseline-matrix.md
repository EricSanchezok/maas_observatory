# Published baseline matrix

Review date: 2026-07-28

## Scope

This matrix records published model-card and benchmark-leaderboard values for
the upstream model families represented in the deployment catalog. It documents
the benchmark release, metric, precision, reasoning mode, and known
comparability constraints.

The machine-readable source of record is `config/baselines.yaml`. Values in
this document are descriptive references; the report computes a delta only
when the registry marks a reference as `exact` for the evaluated configuration.

## Reference coverage

| Configured deployment | Published benchmark | Published value | Additional published context | Comparability notes |
|---|---|---:|---|---|
| GLM 5.2 W4A8 | Toolathlon-Verified | 59.9 Pass@1 | MCP-Atlas Public 76.8; HLE w/ Tools 54.7 | The GLM-5.2 model card reports legacy Tool-Decathlon 48.2 and a different precision/context configuration |
| DeepSeek V4 Pro W4A8 | Toolathlon-Verified | 55.9 Pass@1 | MCP-Atlas 73.6; HLE w/ Tools 48.2 | The model card reports legacy Toolathlon Max 51.8; the configured deployment is W4A8 |
| DeepSeek V4 Flash W8A8 | Legacy Toolathlon | 47.8 Max | MCP-Atlas 69.0 Max; HLE w/ Tools 45.1 Max | The current Verified leaderboard has no same-name Flash row |
| Nex N2 Pro BF16 | Legacy Toolathlon | 51.9 | τ³-bench 71.1 | Harness and reasoning settings require alignment |
| Nex N2 Pro W8A8 | Legacy Toolathlon | 51.9 for the upstream model | τ³-bench 71.1 for the upstream model | No published value was identified for this W8A8 deployment |
| Kimi K2.6 W4A8 | Toolathlon-Verified | 58.0 Pass@1 | Claw-Eval 62.3 Pass^3; MCPMark 55.9 | The model card also reports legacy Toolathlon 50.0; MCPMark and MCP-Atlas are distinct |
| MiniMax M2.7 W8A8 | Legacy Toolathlon | 46.3 | MM-Claw 62.7 | No same-name current Verified result was identified; MM-Claw and Claw-Eval are distinct |
| MiMo V2.5 Pro W8A8 | τ³-bench | 72.9 | Claw-Eval 63.8 Pass^3 | The published model-card configuration is FP8 mixed precision |
| Qwen3.6-27B BF16 | Claw-Eval | 60.6 Pass^3 / 72.4 average | QwenClawBench 53.4; SkillsBench Avg5 48.2 | No same-name Toolathlon or BFCL value was identified |

Source URLs and access dates are retained in the baseline registry.

## Toolathlon score series

| Series | Covered upstream models in the registry | Published values | Comparison rule |
|---|---|---|---|
| Legacy Toolathlon/Tool-Decathlon | GLM 5.2, DeepSeek V4 Pro/Flash, Nex N2 Pro, Kimi K2.6, MiniMax M2.7 | 48.2 / 51.8 / 47.8 / 51.9 / 50.0 / 46.3 | Comparable only to a reproduction of the corresponding legacy release and settings |
| Toolathlon-Verified, sequence beginning 2026-06-30 | GLM 5.2, DeepSeek V4 Pro, Kimi K2.6 | 59.9 / 55.9 / 58.0 Pass@1 | Comparable only to the matching Verified release and harness |

The two series use different releases and are not combined into one ranking.

## Comparison structure

The report presents two independent views:

1. **Standardized evaluation:** configured deployments evaluated under the same
   repository task and resource settings.
2. **Published context:** upstream values with source, metric, release,
   precision, and reasoning metadata.

An official delta requires alignment of:

- benchmark release, task split, and verifier;
- agent scaffold, system prompt, maximum turns, and timeout;
- reasoning mode, temperature, top-p, and output limit;
- tool schema, parallel-call behavior, and context handling;
- simulator or judge model where applicable;
- precision and serving configuration.

References that do not satisfy these conditions remain `contextual` and are
displayed without a computed delta.

## Serving metadata

Serving configuration is recorded because it can affect protocol output and
context limits. Relevant published settings include:

- Qwen tool-call parser configuration;
- MiMo reasoning and tool-call parser configuration;
- DeepSeek encoding and decoding configuration;
- GLM context-window configuration;
- quantization and precision for every configured deployment.

The repository does not infer an unobserved server configuration. Unknown
values remain explicitly unknown in the report.

## Report fields

Published comparisons include:

- source URL and access date;
- benchmark release and metric;
- published model identity, precision, and reasoning mode;
- configured deployment identity and precision;
- standardized task success and reliability metrics;
- comparability label;
- exact delta only when the registry and experiment configuration permit it.
