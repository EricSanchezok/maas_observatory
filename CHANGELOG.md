# Changelog

All notable changes to this project are documented here. The project follows
semantic versioning for the Python package and explicit schema versions for
public data formats.

## Unreleased

- Treat prompt-token differences from the fixed reference tokenizer as a
  measurement quality signal instead of a failed request. Successful responses
  now remain in latency and throughput aggregates, with tokenizer mismatch
  quality exposed through the API and interface.

## 0.6.0 - 2026-08-01

- Add three-tier Agent context observability (1K / 16K / 64K) with all tiers
  visible together in the API and frontend.
- Bump public API schema to v6 and internal DB schema to v4; schema v4 requires
  a fresh `db reset --confirm response-suite-v5` — online migration from v3 is
  supported but previous datasets are not carried forward.
- Replace fixed short/context probe pairs with six deterministic Agent fixtures
  (`agent-1k-a` through `agent-64k-b`) in one balanced response profile.
- Standard mode uses strict equal-frequency scheduling: one block per 600
  seconds cycling through the six fixtures in order, same fixture and nonce sent
  concurrently to all nine deployments.
- Rapid mode now requires an explicit `MAAS_OBSERVATORY_RAPID_CONTEXT_TIER`
  environment variable and a single-tier selection; all modes obey the same
  per-deployment daily request, input-token, and output-token budget.
- Separate First token (reasoning-inclusive), First answer, and Total response
  measurements; Compare view supports First token, Output speed, and Total
  response.
- Frontend redesigned with per-tier fleet overview, side-by-side tier comparison,
  improved detail views, and responsive layout.
- Config schema v4 adds `rapid_context_tier`, standardized daily budget
  structure, and rapid-tier validation.

## 0.3.0 - 2026-07-28

- Add report-builder provenance and release schema v3.
- Add validated, atomic public-snapshot construction and CLI commands.
- Add deterministic SVG/PNG benchmark figures with BFCL subset diagnostics and
  explicitly labeled official/contextual reference values.
- Preserve BFCL per-subset coverage and distinguish inference transport errors
  from capability failures.
- Require official-reproduction lane and exact configuration digest for
  machine-computed official deltas.
- Terminate timed-out benchmark process groups and expand recursive secret
  redaction to auxiliary benchmark credentials.
- Harden protocol clarification scoring, result identity uniqueness, archive
  validation, and third-party licensing disclosures.
