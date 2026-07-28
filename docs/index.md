# SII Holos Tool-use Bench

This project measures whether SII Holos model deployments can reliably use
tools through the OpenAI-compatible protocol and inside longer agent loops.

The evaluation keeps three questions separate:

1. Did the endpoint and serving stack return a valid protocol response?
2. Did the model select and call the correct tools?
3. Did the full agent complete the task reliably?

The repository contains configuration, immutable run records, deterministic
statistics, public baseline provenance, and a sanitized release pipeline. It
does not publish private endpoint URLs or credentials.

Start with the [methodology](methodology.md), then follow the
[reproducibility guide](reproducibility.md).
