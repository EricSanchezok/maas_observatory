# MaaS Observatory

This repository contains two independent applications for OpenAI-compatible
MaaS deployments:

1. `maas_observatory` checks route availability and real streaming response
   timing from one declared observer location, then provides a public read-only
   API;
2. `tooluse_bench` runs offline, reproducible tool-use evaluations.

The evaluation reports three dimensions separately:

1. endpoint and serving-stack protocol conformance;
2. tool selection and function-call correctness;
3. end-to-end agent task completion and reliability.

The repository contains configuration, immutable run records, deterministic
statistics, public baseline provenance, and a sanitized release pipeline. It
does not publish private endpoint URLs or credentials.

Start with the [methodology](methodology.md), then follow the
[reproducibility guide](reproducibility.md). The
[operations guide](maas-observatory-operations.md) is the authoritative
description of implemented response checks, collection modes, the public API,
storage lifecycle, and recovery. The
[service observability research](service-observability.md) and
[historical backend design](observability-data-backend-design.md) remain
research records. White-box serving metrics described there are future options
only if stable per-instance sources become available; they are not current
MaaS Observatory capabilities.
