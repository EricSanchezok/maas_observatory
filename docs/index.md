# SII Holos Tool-use Bench

This project evaluates SII Holos model deployments through the
OpenAI-compatible tool-calling protocol and longer agent workflows.

The evaluation reports three dimensions separately:

1. endpoint and serving-stack protocol conformance;
2. tool selection and function-call correctness;
3. end-to-end agent task completion and reliability.

The repository contains configuration, immutable run records, deterministic
statistics, public baseline provenance, and a sanitized release pipeline. It
does not publish private endpoint URLs or credentials.

Start with the [methodology](methodology.md), then follow the
[reproducibility guide](reproducibility.md).
