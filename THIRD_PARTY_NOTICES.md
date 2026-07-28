# Third-party notices

The core package depends on open-source software listed in `uv.lock`. Isolated
benchmark runtimes and their complete dependency graphs are recorded in:

- `benchmark-envs/bfcl/uv.lock`
- `benchmark-envs/toolathlon/uv.lock`

## BFCL / Gorilla

The BFCL runtime uses `bfcl-eval==2025.12.17` and `evalscope==1.2.0` from the
independently locked environment above. BFCL source and data are published by
the Gorilla project under Apache-2.0:

- https://github.com/ShishirPatil/gorilla
- https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard

The upstream code and dataset are not relicensed by this repository.

## Toolathlon

The controlled client targets revision
`3dd5fcabb53eebe8199ad77a7d3607a0db890924` of:

- https://github.com/hkust-nlp/Toolathlon

No explicit upstream license file was exposed at that pinned revision when
reviewed on 2026-07-28. This repository therefore reimplements only the
documented client protocol and does not copy or redistribute Toolathlon source,
datasets, task images, or services. Operators must review the upstream
repository's current license and service terms before execution or
redistribution. Toolathlon remains governed by those upstream terms and is not
relicensed by this repository.
