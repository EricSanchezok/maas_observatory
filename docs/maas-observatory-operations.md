# MaaS Observatory operations

This guide describes the implemented MaaS Observatory service. It runs as one
Python process and serves both the public React interface and a read-only
FastAPI contract. It does not read serving-engine metrics. Its only live
evidence is a lightweight OpenAI-compatible route check and real streaming
requests sent from the Observatory host.

## Start the service

Copy the environment template and provide the nine inference URLs and API keys:

```bash
cp .env.example .env
uv sync --all-extras --frozen
npm --prefix frontend ci
npm --prefix frontend run build
uv run maas-observatory serve
```

The repository default is `standard` collection:

```bash
MAAS_OBSERVATORY_COLLECTION_MODE=standard
```

Use `rapid` only for an attended collection session:

```bash
MAAS_OBSERVATORY_COLLECTION_MODE=rapid
```

Rapid mode requires an explicit context tier via
`MAAS_OBSERVATORY_RAPID_CONTEXT_TIER` and shares the same per-deployment daily
budget as Standard. Switch it back to Standard manually after the session. The
active mode appears in the startup log, API metadata, and interface.

The service listens on `0.0.0.0:8080`. `/healthz` reports that the HTTP process
is alive. `/readyz` returns 200 only after migration, SQLite `quick_check`,
catalog synchronization, and writer startup have succeeded.

For frontend development:

```bash
# Terminal 1
uv run maas-observatory serve

# Terminal 2
npm --prefix frontend run dev
```

Vite listens on 5173 and proxies API and health requests to 8080. Same-origin
deployment needs no CORS. For a separate frontend origin:

```bash
MAAS_OBSERVATORY_CORS_ORIGINS=https://status.example.edu
```

## What is measured

`/v1/models` is checked every 60 seconds without inference. Response timing is
measured with versioned streaming requests from one declared observer vantage:

- **First token**: request start to the first output event of any kind
  (reasoning-inclusive). This reflects the earliest response signal from the
  deployment.
- **First answer**: request start to the first non-empty visible content.
  Hidden reasoning and metadata frames do not stop this timer.
- **Total response**: request start to the last output event.
- **Output speed**: `(reported completion tokens - 1) / (last output event -
  first output event)`.

If streaming usage is absent, Output speed is unavailable. A successfully
completed response still counts as a successful path check. Characters and SSE
chunks are never used as token estimates.

`response-suite-v5` contains six deterministic Agent fixtures at three context
tiers (1K, 16K, 64K) with two variants each, in one balanced response profile.
The repository stores fixture definitions and payload digests, but the database,
logs, API, and exports do not retain prompt or response content. A sampling
block uses the same fixture and nonce for all nine deployments. The deployment
order rotates and reverses deterministically to avoid a fixed first/last
position.

All six fixtures allow up to 16,384 completion tokens. This ceiling gives
steady-state speed measurements enough output events to reduce short-stream
variance across all tiers. Responses may stop naturally before the ceiling.
Changing this limit requires a new suite and definition version so results
collected under different request shapes are never combined.

Observer HTTP clients explicitly ignore workstation proxy environment and
system settings. Requests follow the host routing table directly, which keeps
the declared observer vantage stable and prevents a desktop VPN or proxy from
silently changing the measured network path.

Waiting for a response to begin and detecting a stalled stream use separate
timeouts. A request may take up to 180 seconds from request start to produce its
first non-empty output event. Once output has started, a 30-second gap without
another stream line is classified as a stream stall. Response headers, blank
lines, and heartbeat frames do not reset the first-output deadline. This
prevents a slow queue or long prefill from being misreported as a broken stream
while retaining a finite failure boundary.

The Live response area shows only the latest completed request. A failure clears
the displayed values immediately. Model summaries use the arithmetic mean of
valid samples in the selected window. Comparison requires six successful samples
covering all six fixtures.

## Collection modes

All generation checks share one process-wide lock. There is never more than one
active inference request.

| Mode | Schedule | Limit |
|---|---|---|
| `rapid` | one request per model per minute; single-tier A/B variants alternate | daily request, input-token, and output-token limits |
| `standard` | one balanced six-fixture block every 10 minutes; strict equal-frequency order across all three tiers | daily request, input-token, and output-token limits |

Nine calls are spread across each block. If a request or block runs long, the
next block is delayed. The scheduler records lag and does not launch concurrent
catch-up requests or a burst of missed checks. Maintenance is the only runtime
gate for normal scheduled requests.

## Response states

- `collecting`: no completed response yet;
- `current`: the route works and the latest response succeeded within two schedule
  periods;
- `delayed`: the latest successful check is old or the scheduler is late;
- `unavailable`: the latest real request or route check failed;
- `maintenance`: operator-controlled maintenance state.

Skipped or not-due work is excluded from success rates. A local measurement
limitation is not counted as a service failure.

## Storage and migration

Runtime data is ignored by Git:

```text
var/maas-observatory/
├── observatory.sqlite3
├── backups/
└── exports/
```

SQLite uses WAL, foreign keys, a five-second busy timeout,
`synchronous=NORMAL`, incremental auto-vacuum, and one asynchronous writer.
Run one Uvicorn worker and one application replica per database.

Schema v4 retains response probes with context-tier awareness, profile
definitions, scheduler position, events, per-deployment daily budget ledgers,
and valid response history. When migrating from schema v3, the service creates
an online backup before adding the context-tier column and replacing the budget
table. Schema v2 first creates a backup then drops all scrape, counter,
histogram, rollup, metrics-source, and telemetry-state data. Schema v1 requires
a full reset.

Useful commands:

```bash
uv run maas-observatory db migrate
uv run maas-observatory db check
uv run maas-observatory db backup
uv run maas-observatory export --format json
uv run maas-observatory export --format csv

# Route and profile validation only
uv run maas-observatory inventory --no-generation

# Explicit operator-authorized streaming checks
uv run maas-observatory inventory --generation
uv run maas-observatory probe run \
  --model glm-5.2 \
  --kind experience

# Destructive rebuild: stop the service and back up first
uv run maas-observatory db backup
uv run maas-observatory db reset --confirm response-suite-v5
uv run maas-observatory db check
```

The process creates a daily online backup at 03:00, retains daily and weekly
backup generations according to configuration, and retains response probes for
365 days by default.

## Public API

```text
GET|HEAD /healthz
GET|HEAD /readyz
GET|HEAD /api/v1/catalog
GET|HEAD /api/v1/experience/overview
GET|HEAD /api/v1/deployments/{id}/experience/latest
GET|HEAD /api/v1/deployments/{id}/experience/series
GET|HEAD /api/v1/experience/profiles
GET|HEAD /api/v1/compare
GET|HEAD /api/v1/events
GET|HEAD /api/v1/meta
```

The removed passive endpoints `/api/v1/overview` and
`/api/v1/deployments/{id}/series` return 404. API envelopes use schema version
6, ETags, and:

```text
Cache-Control: public, max-age=10, stale-while-revalidate=30
```

Missing values are `null` with a reason, never zero. Public responses may
include the suite, fixture, block, collection mode, response state, vantage,
and scheduler lag. They never include endpoint URLs, credentials, internal
addresses, prompts, completions, or stream content.

## Container

```bash
docker build -t maas-observatory .
docker run --rm \
  --env-file .env \
  -p 8080:8080 \
  -v "$PWD/var/maas-observatory:/app/var/maas-observatory" \
  maas-observatory
```

TLS and public access control belong at the port-forwarding, reverse-proxy, or
platform-network layer.

## Release checks

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run build
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest --cov --cov-report=term-missing
uv build
```

The test suite covers deterministic fixture rotation, shared block nonce,
streaming reasoning/content handling, missing usage, service and transport
failures, scheduler lag, rapid and standard schedules, global single
concurrency, migration backup, removed passive tables and routes, state
transitions, ETags, null semantics, and secret-safe public responses.
