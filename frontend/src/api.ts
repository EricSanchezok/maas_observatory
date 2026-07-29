import type {
  CatalogItem,
  CompareItem,
  Envelope,
  ExperienceItem,
  ExperienceSeriesData,
  MetricSeries,
  ObservatoryEvent,
  OverviewItem,
  SeriesData,
  WindowOption
} from "./types";

const SERIES_METRICS = [
  "aggregate_output_tps",
  "prompt_tps",
  "requests_running",
  "requests_waiting",
  "kv_cache_usage",
  "ttft_p95",
  "e2e_p95"
] as const;

async function request<T>(path: string, signal?: AbortSignal): Promise<Envelope<T>> {
  const response = await fetch(path, {
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
  overview: Envelope<OverviewItem[]>;
  compare: Envelope<CompareItem[]>;
  experience: Envelope<ExperienceItem[]>;
  contextExperience: Envelope<ExperienceItem[]>;
  events: Envelope<ObservatoryEvent[]>;
}> {
  const [catalog, overview, compare, experience, contextExperience, events] =
    await Promise.all([
    request<CatalogItem[]>("/api/v1/catalog", signal),
    request<OverviewItem[]>(`/api/v1/overview?window=${window}`, signal),
    request<CompareItem[]>(`/api/v1/compare?window=${window}`, signal),
    request<ExperienceItem[]>(
      `/api/v1/experience/overview?profile=interactive-short-v1&window=${window}`,
      signal
    ),
    request<ExperienceItem[]>(
      `/api/v1/experience/overview?profile=context-16k-v1&window=${window}`,
      signal
    ),
    request<ObservatoryEvent[]>(
      `/api/v1/events?window=${window}&limit=40`,
      signal
    )
    ]);
  return { catalog, overview, compare, experience, contextExperience, events };
}

export async function fetchExperienceSeries(
  deploymentId: string,
  profile: "interactive-short-v1" | "context-16k-v1",
  window: WindowOption,
  signal?: AbortSignal
): Promise<MetricSeries> {
  const result = await request<ExperienceSeriesData>(
    `/api/v1/deployments/${encodeURIComponent(
      deploymentId
    )}/experience/series?profile=${profile}&window=${window}`,
    signal
  );
  const metrics = [
    "client_ttft_seconds",
    "first_visible_content_seconds",
    "steady_state_output_tps",
    "client_e2e_seconds",
    "stream_event_gap_p95_seconds",
    "reported_prompt_tokens"
  ];
  return Object.fromEntries(
    metrics.map((metric) => [
      metric,
      result.data.points.map((point) => ({
        timestamp: point.timestamp,
        value: point.measurements[metric] ?? null,
        unit: metric.endsWith("_seconds") ? "s" : metric.includes("tps") ? "tokens/s" : "tokens",
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

export async function fetchSeries(
  deploymentId: string,
  window: WindowOption,
  signal?: AbortSignal
): Promise<MetricSeries> {
  const resolution = window === "1h" ? "1m" : "5m";
  const responses = await Promise.all(
    SERIES_METRICS.map(async (metric) => {
      const result = await request<SeriesData>(
        `/api/v1/deployments/${encodeURIComponent(
          deploymentId
        )}/series?metric=${metric}&window=${window}&resolution=${resolution}`,
        signal
      );
      return [metric, result.data.points] as const;
    })
  );
  return Object.fromEntries(responses);
}
