export type Quality = "exact" | "incomplete" | "unavailable";
export type WindowOption = "1h" | "6h" | "24h" | "7d" | "30d";
export type ResponseState =
  | "current"
  | "collecting"
  | "delayed"
  | "unavailable"
  | "maintenance";

export type ContextTier = "1k" | "16k" | "64k";

export const CONTEXT_TIERS: ContextTier[] = ["1k", "16k", "64k"];
export const TIER_LABELS: Record<ContextTier, string> = {
  "1k": "1K",
  "16k": "16K",
  "64k": "64K"
};

export interface Envelope<T> {
  schema_version: "6";
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
  name: string;
  provider: string;
  family: string;
  upstream_model: string;
  precision: string;
  model_id: string;
}

export interface ResponseMeasurement {
  measured_at: string;
  first_token_seconds: number | null;
  first_response_seconds: number | null;
  total_response_seconds: number | null;
  output_speed_tps: number | null;
  reported_prompt_tokens: number | null;
  ref_prompt_tokens: number | null;
  reported_completion_tokens: number | null;
  fixture_id: string;
  block_id: string;
  scheduler_lag_seconds: number;
}

export interface TierExperience {
  sample_count: number;
  fixture_count: number;
  complete_fixture_set: boolean;
  response_state: ResponseState;
  state_reasons: string[];
  first_token_p50: number | null;
  first_token_p95: number | null;
  first_response_p50: number | null;
  first_response_p95: number | null;
  total_response_p50: number | null;
  total_response_p95: number | null;
  output_speed_p50: number | null;
  output_speed_p95: number | null;
  reasoning_tokens_p50: number | null;
  reasoning_tokens_quality: "reported" | "estimated" | "unavailable";
  reported_prompt_tokens_p50: number | null;
  ref_prompt_tokens_p50: number | null;
  latest: ResponseMeasurement | null;
  latest_attempt_outcome: "success" | "failed" | "skipped" | null;
  latest_attempt_error_class: string | null;
  latest_attempt_error_code: string | null;
  latest_attempt_reason:
    | "first_check_scheduled"
    | "maintenance"
    | "scheduled_later"
    | "measurement_limited"
    | "request_failed"
    | null;
  latest_attempt_at: string | null;
  measurement_age_seconds: number | null;
}

export interface ExperienceItem {
  deployment_id: string;
  alias: string;
  name: string;
  precision: string;
  reasoning_enabled: boolean;
  profile_id: string;
  definition_version: string;
  suite_version: string;
  vantage_id: string;
  collection_mode: "rapid" | "standard";
  path_success_rate: number | null;
  quality: Quality;
  uptime_24h: number | null;
  uptime_7d: number | null;
  uptime_30d: number | null;
  tiers: Record<ContextTier, TierExperience>;
}

export interface ExperienceSeriesPoint {
  timestamp: string;
  quality: Quality;
  reason: string | null;
  profile_id: string;
  definition_version: string;
  suite_version: string;
  vantage_id: string;
  collection_mode: "rapid" | "standard";
  fixture_id: string;
  block_id: string;
  scheduler_lag_seconds: number;
  source_kind: "streaming_request";
  observation_scope: "observatory_vantage";
  sample_count: number;
  measurements: Record<string, number | null>;
}

export interface ExperienceSeriesData {
  deployment_id: string;
  profile_id: string;
  tiers: Record<ContextTier, {
    points: ExperienceSeriesPoint[];
  }>;
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

export interface TierCompareData {
  first_token_p50: number | null;
  output_speed_p50: number | null;
  total_response_p50: number | null;
  quality: Quality;
  sample_count: number;
  fixture_count: number;
  complete_fixture_set: boolean;
  measured_at: string | null;
  latest_attempt_outcome: "success" | "failed" | "skipped" | null;
  latest_attempt_reason: string | null;
  latest_attempt_at: string | null;
  reason: string | null;
}

export interface CompareItem {
  deployment_id: string;
  alias: string;
  response_state: ResponseState;
  tiers: Record<ContextTier, TierCompareData>;
  source_kind: "streaming_request";
  observation_scope: "observatory_vantage";
  profile_id: string;
  definition_version: string;
  suite_version: string;
  vantage_id: string;
  collection_mode: "rapid" | "standard";
}

export interface ObservatoryEvent {
  id: number;
  deployment_id: string;
  alias: string;
  kind: string;
  severity: string;
  state: string;
  title: string;
  detail: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
}

export type MetricSeries = Record<string, MetricPoint[]>;

export interface TieredSeries {
  deployment_id: string;
  tiers: Record<ContextTier, MetricSeries>;
}

export interface AvailabilityDaily {
  date: string;
  uptime_pct: number | null;
  samples: number;
  maintenance_excluded: number;
}

export interface AvailabilityItem {
  deployment_id: string;
  alias: string;
  days: number;
  daily: AvailabilityDaily[];
}
