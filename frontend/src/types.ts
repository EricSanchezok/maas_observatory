export type Quality = "exact" | "incomplete" | "unavailable";
export type WindowOption = "1h" | "6h" | "24h";
export type ResponseState =
  | "current"
  | "collecting"
  | "delayed"
  | "unavailable"
  | "maintenance";

export interface Envelope<T> {
  schema_version: "4";
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
  first_response_seconds: number | null;
  output_speed_tps: number | null;
  reported_prompt_tokens: number | null;
  reported_completion_tokens: number | null;
  fixture_id: string;
  block_id: string;
  scheduler_lag_seconds: number;
}

export interface ExperienceItem {
  deployment_id: string;
  alias: string;
  name: string;
  precision: string;
  response_state: ResponseState;
  state_reasons: string[];
  profile_id: string;
  definition_version: string;
  suite_version: string;
  vantage_id: string;
  collection_mode: "rapid" | "standard";
  sample_count: number;
  fixture_count: number;
  complete_fixture_set: boolean;
  path_success_rate: number | null;
  quality: Quality;
  first_response_mean: number | null;
  output_speed_mean: number | null;
  latest: ResponseMeasurement | null;
  latest_attempt_outcome: "success" | "failed" | "skipped" | null;
  latest_attempt_error_class: string | null;
  latest_attempt_error_code: string | null;
  latest_attempt_reason:
    | "first_check_scheduled"
    | "maintenance"
    | "scheduled_later"
    | "request_failed"
    | null;
  latest_attempt_at: string | null;
  measurement_age_seconds: number | null;
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

export interface CompareItem {
  deployment_id: string;
  alias: string;
  value: number | null;
  unit: string;
  source_kind: "streaming_request";
  observation_scope: "observatory_vantage";
  quality: Quality;
  sample_count: number;
  fixture_count: number;
  complete_fixture_set: boolean;
  profile_id: string;
  definition_version: string;
  suite_version: string;
  vantage_id: string;
  collection_mode: "rapid" | "standard";
  measured_at: string | null;
  latest_attempt_outcome: "success" | "failed" | "skipped" | null;
  latest_attempt_reason: string | null;
  latest_attempt_at: string | null;
  reason: string | null;
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
