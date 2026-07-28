# SII Holos Tool-use Evaluation — `20260728T054932740593Z-protocol-smoke-v1-5f0271f4-8dd04365`

## Provenance

- Git commit: `5f0271f4ef14fc4b035ee2108ae6c346eec2adce`
- Working tree dirty at launch: `false`
- Created at: `2026-07-28T05:49:32.740593+00:00`
- Configuration SHA-256: `8dd0436596417a28f253e1f2e2f5d30b38705d16ed2552287b84804064e8fa05`
- Dependency lock SHA-256: `739092f5522e0d38f128ff22b82840305d228310824c6541b7dfa5384509beb3`

## Results

| Benchmark | Lane | Deployment | Tasks | Pass@1 | 95% CI | Pass@3 | Pass^3 | Error rate |
|---|---|---|---:|---:|---:|---:|---:|---:|
| probe | standardized | deepseek-v4-flash | 5 | 100.0% | 100.0-100.0% | 100.0% | 100.0% | 0.0% |
| probe | standardized | deepseek-v4-pro | 5 | 6.7% | 0.0-20.0% | 20.0% | 0.0% | 93.3% |
| probe | standardized | glm-5.2 | 5 | 0.0% | 0.0-0.0% | 0.0% | 0.0% | 100.0% |
| probe | standardized | kimi-k2.6 | 5 | 0.0% | 0.0-0.0% | 0.0% | 0.0% | 100.0% |
| probe | standardized | mimo-v2.5-pro | 5 | 93.3% | 80.0-100.0% | 100.0% | 80.0% | 6.7% |
| probe | standardized | minimax-m2.7 | 5 | 0.0% | 0.0-0.0% | 0.0% | 0.0% | 100.0% |
| probe | standardized | nex-n2-pro-bf16 | 5 | 100.0% | 100.0-100.0% | 100.0% | 100.0% | 0.0% |
| probe | standardized | nex-n2-pro-w8a8 | 5 | 93.3% | 80.0-100.0% | 100.0% | 80.0% | 6.7% |
| probe | standardized | qwen3.6-27b | 5 | 60.0% | 20.0-100.0% | 60.0% | 60.0% | 40.0% |

No cross-benchmark composite score is calculated. A missing value means the lane did not produce comparable task-level observations.

## Official and published context

Only baselines marked `exact` may produce an official delta. The entries below are contextual unless their precision, benchmark release, harness, and reasoning configuration match the evaluated deployment.

| Upstream model | Benchmark release | Metric | Score | Comparability | Source |
|---|---|---|---:|---|---|
| DeepSeek-V4-Flash | hle-with-tools / vendor-reported | score | 45.1% | contextual | [source](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) |
| DeepSeek-V4-Flash | mcp-atlas / vendor-reported | score | 69.0% | contextual | [source](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) |
| DeepSeek-V4-Flash | toolathlon-legacy / pre-verified | pass_at_1 | 47.8% | contextual | [source](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) |
| DeepSeek-V4-Pro | hle-with-tools / vendor-reported | score | 48.2% | contextual | [source](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) |
| DeepSeek-V4-Pro | mcp-atlas / vendor-reported | score | 73.6% | contextual | [source](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) |
| DeepSeek-V4-Pro | toolathlon-legacy / pre-verified | pass_at_1 | 51.8% | contextual | [source](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) |
| DeepSeek-V4-Pro (max) | toolathlon-verified / verified-2026-06-30 | pass_at_1 | 55.9% | contextual | [source](https://toolathlon.xyz/docs/leaderboard) |
| GLM-5.2 | hle-with-tools / vendor-reported | score | 54.7% | contextual | [source](https://huggingface.co/zai-org/GLM-5.2) |
| GLM-5.2 | mcp-atlas-public / vendor-reported | score | 76.8% | contextual | [source](https://huggingface.co/zai-org/GLM-5.2) |
| GLM-5.2 | toolathlon-legacy / pre-verified | pass_at_1 | 48.2% | contextual | [source](https://huggingface.co/zai-org/GLM-5.2) |
| GLM-5.2 (max) | toolathlon-verified / verified-2026-06-30 | pass_at_1 | 59.9% | contextual | [source](https://toolathlon.xyz/docs/leaderboard) |
| Kimi-K2.6 | claw-eval / vendor-reported | pass_pow_3 | 62.3% | contextual | [source](https://huggingface.co/moonshotai/Kimi-K2.6) |
| Kimi-K2.6 | mcpmark / vendor-reported | score | 55.9% | contextual | [source](https://huggingface.co/moonshotai/Kimi-K2.6) |
| Kimi-K2.6 | toolathlon-legacy / pre-verified | pass_at_1 | 50.0% | contextual | [source](https://huggingface.co/moonshotai/Kimi-K2.6) |
| Kimi-K2.6 | toolathlon-verified / verified-2026-06-30 | pass_at_1 | 58.0% | contextual | [source](https://toolathlon.xyz/docs/leaderboard) |
| MiMo-V2.5-Pro | claw-eval / vendor-reported | pass_pow_3 | 63.8% | contextual | [source](https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro) |
| MiMo-V2.5-Pro | tau3-bench / vendor-reported | score | 72.9% | contextual | [source](https://huggingface.co/XiaomiMiMo/MiMo-V2.5-Pro) |
| MiniMax-M2.7 | mm-claw / vendor-reported | score | 62.7% | contextual | [source](https://github.com/MiniMax-AI/MiniMax-M2.7) |
| MiniMax-M2.7 | toolathlon-legacy / pre-verified | pass_at_1 | 46.3% | contextual | [source](https://github.com/MiniMax-AI/MiniMax-M2.7) |
| Nex-N2-Pro | tau3-bench / vendor-reported | score | 71.1% | contextual | [source](https://huggingface.co/nex-agi/Nex-N2-Pro) |
| Nex-N2-Pro | toolathlon-legacy / pre-verified | pass_at_1 | 51.9% | contextual | [source](https://huggingface.co/nex-agi/Nex-N2-Pro) |
| Qwen3.6-27B | claw-eval / vendor-reported | average | 72.4% | contextual | [source](https://huggingface.co/Qwen/Qwen3.6-27B) |
| Qwen3.6-27B | claw-eval / vendor-reported | pass_pow_3 | 60.6% | contextual | [source](https://huggingface.co/Qwen/Qwen3.6-27B) |
| Qwen3.6-27B | qwen-claw-bench / vendor-reported | score | 53.4% | contextual | [source](https://huggingface.co/Qwen/Qwen3.6-27B) |
| Qwen3.6-27B | skillsbench / vendor-reported | avg5 | 48.2% | contextual | [source](https://huggingface.co/Qwen/Qwen3.6-27B) |

## Interpretation constraints

- Transport and infrastructure errors are retained; they are never dropped.
- Quantized SII Holos deployments are not assumed equivalent to upstream releases.
- Toolathlon-Verified and pre-Verified Toolathlon are separate score series.
- MCPMark, MCP-Atlas, MM-Claw, and Claw-Eval are distinct benchmarks.
