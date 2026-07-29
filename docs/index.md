# MaaS Observatory

This repository contains two independent applications for OpenAI-compatible
MaaS deployments:

1. `maas_observatory` provides low-impact operational telemetry and a public,
   read-only API;
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
[service observability research](service-observability.md) defines the
low-impact monitoring, latency, throughput, and capacity-measurement model for
the configured deployments. The
[MaaS Observatory data backend design](observability-data-backend-design.md)
defines the authoritative priorities, collection policy, public API, and
frontend data contract for the real-time observability site.
The [operations guide](maas-observatory-operations.md) documents the implemented
CLI, storage lifecycle, deployment model, public endpoints, and recovery steps.
