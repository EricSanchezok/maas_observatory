# Changelog

All notable changes to this project are documented here. The project follows
semantic versioning for the Python package and explicit schema versions for
public data formats.

## Unreleased

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
