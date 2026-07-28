# Contributing

Contributions that improve reproducibility, protocol coverage, benchmark
adapters, or documentation are welcome.

## Before opening a pull request

1. Create a focused branch and keep unrelated changes out of the commit.
2. Add or update tests for every behavior change.
3. Regenerate schemas with `uv run python scripts/export_schemas.py`.
4. Run the complete local verification sequence from `README.md`.
5. Confirm that no endpoint, credential, trajectory, or private path is staged.

Never add a real URL or key to a tracked file, fixture, issue, or log. Use an
`.invalid` hostname and obviously synthetic credentials in tests.

## Compatibility policy

Public YAML and JSON formats are strict, versioned schemas. A backwards
incompatible change requires a schema version increment, a migration note, and
an entry in `CHANGELOG.md`. Benchmark revisions and dependency locks must never
be changed without documenting the effect on score comparability.

See [adding models and benchmarks](docs/adding-models-and-benchmarks.md) for the
extension workflow.
