export type Quality = "exact" | "incomplete" | "unavailable";
export type WindowOption = "1h" | "6h" | "24h";
export type ServiceState =
  | "operational"
  | "slow"
  | "degraded"
  | "unavailable"
  | "maintenance"
  | "unknown";

export interface Envelope<T> {
  schema_version: "2";
  generated_at: string;
  data_window: string;
  freshness_seconds: number | null;
  sample_count: number;
  source_mix: Record<string, number>;
  quality: Quality;
  data: T;
}

export interface CatalogItem {
  deployment_id: string;
  alias: string;
  display_name: string;
  provider: string;
  family: string;
  upstream_model: string;
  precision: string;
  model_id: string;
}

export interface OverviewItem {
  deployment_id: string;
  alias: string;
  name: string;
  family: string;
  precision: string;
  service_state: ServiceState;
  telemetry_state: "fresh" | "partial" | "stale" | "unavailable";
  reasons: string[];
  telemetry_at: string | null;
  measured_at: string | null;
  measurement_age_seconds: number | null;
  source_coverage: number | null;
  expected_source_count: number;
  observed_source_count: number;
  quality: Quality;
  error_statistics_24h: {
    service_failures: number;
    transport_unconfirmed: number;
    measurement_errors: number;
  };
  metrics: Record<string, number | null>;
}

export interface ExperienceMeasurement {
  measured_at: string;
  client_ttft_seconds: number | null;
  first_visible_content_seconds: number | null;
  steady_state_output_tps: number | null;
  client_e2e_seconds: number | null;
  stream_event_gap_p95_seconds: number | null;
  reported_prompt_tokens: number | null;
  reported_completion_tokens: number | null;
}

export interface ExperienceItem {
  deployment_id: string;
  alias: string;
  display_name: string;
  precision: string;
  profile_id: string;
  definition_version: string;
  vantage_id: string;
  experience_state:
    | "experience_fresh"
    | "experience_stale"
    | "experience_unavailable"
    | "experience_collecting";
  sample_count: number;
  executed_count: number;
  path_success_rate: number | null;
  quality: Quality;
  reason: string | null;
  ttft_p50: number | null;
  ttft_p90: number | null;
  streaming_tps_p50: number | null;
  streaming_tps_p10: number | null;
  e2e_p50: number | null;
  e2e_p90: number | null;
  latest: ExperienceMeasurement | null;
  latest_attempt_outcome: string | null;
  latest_attempt_reason: string | null;
  latest_attempt_at: string | null;
}

export interface ExperienceSeriesPoint {
  timestamp: string;
  quality: Quality;
  reason: string | null;
  profile_id: string;
  definition_version: string;
  vantage_id: string;
  source_kind: "experience_probe";
  observation_scope: "observer_path";
  sample_count: number;
  measurements: Record<string, number | null>;
}

export interface ExperienceSeriesData {
  deployment_id: string;
  profile_id: string;
  points: ExperienceSeriesPoint[];
}

export interface MetricPoint {
  timestamp: string;
  value: number | null;
  unit: string;
  source_kind: string;
  observation_scope: string;
  quality: Quality;
  sample_count: number;
  profile_id: string | null;
  definition_version: string;
  reason: string | null;
}

export interface SeriesData {
  deployment_id: string;
  metric: string;
  resolution: string;
  points: MetricPoint[];
  reason: string | null;
}

export interface CompareItem {
  deployment_id: string;
  alias: string;
  value: number | null;
  unit: string;
  source_kind: "experience_probe";
  observation_scope: "observer_path";
  quality: Quality;
  sample_count: number;
  profile_id: string | null;
  definition_version: string;
  vantage_id: string;
  measured_at: string | null;
  latest_attempt_outcome: "success" | "failed" | "unavailable" | "skipped" | null;
  latest_attempt_reason:
    | "busy"
    | "telemetry_pending"
    | "recently_active"
    | "budget_deferred"
    | "scheduled_interval"
    | "maintenance"
    | "deferred"
    | "attempt_failed"
    | "awaiting_turn"
    | null;
  latest_attempt_at: string | null;
  reason: string | null;
}

export interface ObservatoryEvent {
  id: number;
  deployment_id: string;
  kind: string;
  severity: string;
  state: string;
  title: string;
  detail: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
}

export type MetricSeries = Record<string, MetricPoint[]>;
