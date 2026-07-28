# BFCL runtime

This isolated `uv` project pins the exact third-party runtime used by the BFCL
adapter. It is deliberately separate from the core package because EvalScope
has a large dependency graph and changes independently.

Create the environment with:

```bash
uv sync --project benchmark-envs/bfcl --frozen
```
