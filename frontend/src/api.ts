import type {
  AvailabilityItem,
  CatalogItem,
  CompareItem,
  ContextTier,
  Envelope,
  ExperienceItem,
  ExperienceSeriesData,
  MetricPoint,
  MetricSeries,
  ObservatoryEvent,
  TieredSeries,
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

const SERIES_METRICS = [
  "first_token_seconds",
  "first_response_seconds",
  "total_response_seconds",
  "output_speed_tps",
  "reported_prompt_tokens",
  "reasoning_tokens_estimated",
  "ref_prompt_tokens",
  "scheduler_lag_seconds"
];

function unitForMetric(metric: string): string {
  if (metric === "output_speed_tps") return "tokens/s";
  if (metric === "scheduler_lag_seconds" || metric.endsWith("_seconds")) return "s";
  return "tokens";
}

function buildMetricSeries(tier: ExperienceSeriesData["tiers"]["1k"]): MetricSeries {
  const points = tier?.points ?? [];
  return Object.fromEntries(
    SERIES_METRICS.map((metric) => [
      metric,
      points.map((point): MetricPoint => ({
        timestamp: point.timestamp,
        value:
          point.quality !== "exact"
            ? null
            : metric === "scheduler_lag_seconds"
              ? point.scheduler_lag_seconds
              : point.measurements[metric] ?? null,
        unit: unitForMetric(metric),
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

export async function fetchExperienceSeries(
  deploymentId: string,
  window: WindowOption,
  signal?: AbortSignal
): Promise<TieredSeries> {
  const result = await request<ExperienceSeriesData>(
    `/api/v1/deployments/${encodeURIComponent(
      deploymentId
    )}/experience/series?window=${window}`,
    signal
  );
  return {
    deployment_id: result.data.deployment_id,
    tiers: Object.fromEntries(
      (["1k", "16k", "64k"] as ContextTier[]).map((tier) => [
        tier,
        buildMetricSeries(result.data.tiers[tier])
      ])
    ) as Record<ContextTier, MetricSeries>
  };
}

export async function fetchAvailability(
  days: 7 | 30,
  signal?: AbortSignal
): Promise<Envelope<AvailabilityItem[]>> {
  return request<AvailabilityItem[]>(`/api/v1/availability?days=${days}`, signal);
}
