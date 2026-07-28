# Protocol smoke candidate — 2026-07-28

This is a completed three-trial protocol diagnostic, not an immutable public
release and not an official upstream benchmark comparison.

- Run ID:
  `20260728T054932740593Z-protocol-smoke-v1-5f0271f4-8dd04365`
- Git commit: `5f0271f4ef14fc4b035ee2108ae6c346eec2adce`
- Clean working tree at launch: yes
- Records: 135 (9 deployments × 5 tasks × 3 trials)
- Private result SHA-256:
  `78f48695001314f838958153086d99a1e1fa2f8c35f5fd81c24922d3ab3504ca`
- Sanitized compressed result SHA-256:
  `685da97cca18fccac70a5eb30475fbc8f036861a126a05f2d04caa9578bd4ab1`
- Candidate archive SHA-256:
  `1b38e732e640870c3071625b698ab9c2433f6b8ddc3412da836170b105eeace7`

| Deployment | Pass@1 | Pass@3 | Pass^3 | Error rate | Observed errors |
|---|---:|---:|---:|---:|---|
| DeepSeek V4 Flash W8A8 | 100.0% | 100.0% | 100.0% | 0.0% | none |
| Nex N2 Pro BF16 | 100.0% | 100.0% | 100.0% | 0.0% | none |
| MiMo V2.5 Pro W8A8 | 93.3% | 100.0% | 80.0% | 6.7% | 1 timeout |
| Nex N2 Pro W8A8 | 93.3% | 100.0% | 80.0% | 6.7% | 1 timeout |
| Qwen3.6-27B BF16 | 60.0% | 60.0% | 60.0% | 40.0% | 6 timeouts |
| DeepSeek V4 Pro W4A8 | 6.7% | 20.0% | 0.0% | 93.3% | 14 timeouts |
| GLM 5.2 W4A8 | 0.0% | 0.0% | 0.0% | 100.0% | 15 transport errors |
| Kimi K2.6 W4A8 | 0.0% | 0.0% | 0.0% | 100.0% | 15 transport errors |
| MiniMax M2.7 W8A8 | 0.0% | 0.0% | 0.0% | 100.0% | 15 transport errors |

## Interpretation

The probe accepts only native OpenAI `message.tool_calls`. It caps output at
4,096 tokens and applies a 30-second wall-clock budget without retry. A timeout
therefore means the deployment did not complete the simple protocol task under
that budget; it does not prove that the eventual response would be invalid.

A transport error occurs before task scoring. The zero Pass@1 values for GLM,
Kimi, and MiniMax must not be interpreted as model capability scores. They show
that these configured endpoints were not evaluable from this harness during
the recorded run. Follow-up work should diagnose reachability and serving
health before rerunning.

DeepSeek V4 Flash and Nex BF16 completed every trial. MiMo and Nex W8A8 each had
one intermittent timeout, so their Pass@3 hides a reliability failure visible
in Pass^3. Qwen consistently completed the exact-call, selection, and parallel
cases but timed out on irrelevance and missing-information behavior. DeepSeek
V4 Pro completed only one irrelevance case within the budget.

No official delta is calculated: the native probe has no matching upstream
published baseline. Earlier interrupted development runs have no completion
record and are excluded.

## Publication status

The sanitized release candidate passes the repository's schema, redaction, and
checksum validator. It has not been attached to an immutable GitHub Release
because this local repository has no Git remote. Full trajectories should be
published only by attaching the validated archive whose checksum is listed
above; do not upload the private `runs/` directory.

BFCL full-public web subsets remain limited by the absent `SERPAPI_API_KEY`.
Toolathlon-Verified remains not runnable with the hermetic configuration until
`TOOLATHLON_SERVER_HOST` points to a pinned self-hosted evaluation service.
