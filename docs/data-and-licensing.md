# Data and licensing

Source code and repository documentation are available under Apache-2.0 unless
a file states otherwise. Public result bundles and generated evaluation reports
are available under CC BY 4.0 as described in `LICENSE-DATA`.

Third-party benchmark code, datasets, tools, and services retain their original
licenses and terms. A result bundle does not redistribute an upstream dataset
unless that redistribution is separately permitted.

The pinned BFCL evaluator and BFCL dataset are published under Apache-2.0.
This repository invokes them only inside an independently locked environment.
The Toolathlon client protocol is reimplemented locally; no Toolathlon source,
dataset, task image, or service is redistributed here. The pinned upstream
Toolathlon repository did not expose an explicit license file when reviewed on
2026-07-28, so operators must review its current terms before running or
redistributing any upstream artifact. See `THIRD_PARTY_NOTICES.md` for exact
revisions and links.

Public bundles contain sanitized task trajectories, aggregates, provenance,
execution audits, checksums, and the data license. Private URLs, keys,
authorization headers, local user paths, and unpublished raw run directories
are excluded.

When reusing a result, cite the project, release tag, run ID, archive checksum,
benchmark release, and access date. Do not remove provenance or imply that a
contextual vendor score is an exact control.
