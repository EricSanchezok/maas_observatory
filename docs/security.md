# Security and privacy

Credentials and endpoint URLs are loaded from `.env` or the process
environment. `.env` is ignored, public examples contain blank values, and
subprocess adapters receive only an allow-listed environment.

Raw runs may contain prompts, model responses, error details, or tool artifacts
that are inappropriate for publication. Keep `runs/` private. Publish only a
generated release bundle after automated validation and human inspection.

The redactor removes known environment values, authorization-like fields,
bearer and common key patterns, known private endpoint values, and absolute
user-home paths. The validator then scans every textual file,
decompresses trajectory data, checks schemas, and verifies hashes.

No redaction system can recognize every sensitive value. If a benchmark can
surface personal, licensed, or confidential data, add task-specific filtering
and review before execution as well as before release.
