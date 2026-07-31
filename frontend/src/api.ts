import type {
  AvailabilityItem,
  CatalogItem,
  CompareItem,
  Envelope,
  ExperienceItem,
  ExperienceSeriesData,
  MetricSeries,
  ObservatoryEvent,
  WindowOption
} from "./types";

/**
 * Resolve API paths against the directory of the current page so the app
 * works both at the site root and behind a reverse proxy with a path prefix
 * (e.g. VSCode NAT forwarding under /proxy/8080/).
 */
function apiBase(): string {
  const { pathname, origin } = window.location;
  const directory = pathname.endsWith("/")
    ? pathname.slice(0, -1)
    : pathname;
  return origin + directory;
}

async function request<T>(path: string, signal?: AbortSignal): Promise<Envelope<T>> {
  const response = await fetch(apiBase() + path, {
    signal,
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as Envelope<T>;
}

export async function fetchOverview(
  window: WindowOption,
  signal?: AbortSignal
): Promise<{
  catalog: Envelope<CatalogItem[]>;
  compare: Envelope<CompareItem[]>;
  experience: Envelope<ExperienceItem[]>;
  events: Envelope<ObservatoryEvent[]>;
}> {
  const [catalog, compare, experience, events] = await Promise.all([
      request<CatalogItem[]>("/api/v1/catalog", signal),
      request<CompareItem[]>(`/api/v1/compare?window=${window}`, signal),
      request<ExperienceItem[]>(
        `/api/v1/experience/overview?window=${window}`,
        signal
      ),
      request<ObservatoryEvent[]>(
        `/api/v1/events?window=${window}&limit=40`,
        signal
      )
    ]);
  return { catalog, compare, experience, events };
}

export async function fetchExperienceSeries(
  deploymentId: string,
  window: WindowOption,
  signal?: AbortSignal
): Promise<MetricSeries> {
  const result = await request<ExperienceSeriesData>(
    `/api/v1/deployments/${encodeURIComponent(
      deploymentId
    )}/experience/series?window=${window}`,
    signal
  );
  const metrics = [
    "first_response_seconds",
    "output_speed_tps",
    "stream_gap_p95_seconds",
    "reported_prompt_tokens",
    "scheduler_lag_seconds"
  ];
  return Object.fromEntries(
    metrics.map((metric) => [
      metric,
      result.data.points.map((point) => ({
        timestamp: point.timestamp,
        value:
          point.quality !== "exact"
            ? null
            : metric === "scheduler_lag_seconds"
              ? point.scheduler_lag_seconds
              : point.measurements[metric] ?? null,
        unit: metric.endsWith("_seconds")
          ? "s"
          : metric.includes("tps")
            ? "tokens/s"
            : "tokens",
        source_kind: point.source_kind,
        observation_scope: point.observation_scope,
        quality: point.quality,
        sample_count: point.sample_count,
        profile_id: point.profile_id,
        definition_version: point.definition_version,
        reason: point.reason
      }))
    ])
  );
}

export async function fetchAvailability(
  days: 7 | 30,
  signal?: AbortSignal
): Promise<Envelope<AvailabilityItem[]>> {
  return request<AvailabilityItem[]>(`/api/v1/availability?days=${days}`, signal);
}
