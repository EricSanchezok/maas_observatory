import type { ContextTier, MetricPoint, MetricSeries, ResponseState, TierCompareData, TierExperience, TieredSeries, WindowOption } from "../types";
import { CONTEXT_TIERS } from "../types";

export function formatMetric(
  value: number | null | undefined,
  maximumFractionDigits = 1
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits,
    minimumFractionDigits: maximumFractionDigits
  }).format(value);
}

export function formatLatency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value <= 0) return "—";
  if (value < 0.001) return `${formatMetric(value * 1_000_000, 0)} µs`;
  if (value < 1) return `${formatMetric(value * 1000, 0)} ms`;
  return `${formatMetric(value, 2)} s`;
}

export function formatClock(timestamp: string | null | undefined): string {
  if (!timestamp) return "Not measured";
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(new Date(timestamp));
}

export function formatDateTime(timestamp: string | null | undefined): string {
  if (!timestamp) return "Not measured";
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(new Date(timestamp));
}

export function formatAge(seconds: number | null): string {
  if (seconds === null) return "Waiting for first check";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

export function latest(points: MetricPoint[] | undefined): number | null {
  if (!points) return null;
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].quality === "exact" && points[index].value !== null) {
      return points[index].value;
    }
  }
  return null;
}

export function average(points: MetricPoint[] | undefined): number | null {
  const values = (points ?? [])
    .map((point) => point.value)
    .filter((value): value is number => value !== null);
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export interface ChartRow {
  timestamp: string;
  time: string;
  [metric: string]: string | number | null;
}

export function chartRows(
  series: MetricSeries,
  metrics: readonly string[],
  _window: WindowOption
): ChartRow[] {
  const values = new Map<string, Record<string, number | null>>();

  for (const metric of metrics) {
    for (const point of series[metric] ?? []) {
      const row = values.get(point.timestamp) ?? {};
      row[metric] = point.value;
      values.set(point.timestamp, row);
    }
  }

  return [...values.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([timestamp, row]) => ({
      timestamp,
      time: formatClock(timestamp),
      ...Object.fromEntries(
        metrics.map((metric) => [metric, row[metric] ?? null])
      )
    }));
}

export function withSingleGapBridges(
  rows: ChartRow[],
  metric: string
): ChartRow[] {
  return rows.map((row, index) => {
    const previous = rows[index - 1]?.[metric];
    const current = row[metric];
    const next = rows[index + 1]?.[metric];
    const bridgeKey = `${metric}_bridge`;
    let bridge: number | null = null;

    if (typeof current === "number") {
      const previousMissing = rows[index - 1]?.[metric] === null;
      const nextMissing = rows[index + 1]?.[metric] === null;
      const canStartBridge =
        nextMissing && typeof rows[index + 2]?.[metric] === "number";
      const canEndBridge =
        previousMissing && typeof rows[index - 2]?.[metric] === "number";
      if (canStartBridge || canEndBridge) bridge = current;
    } else if (typeof previous === "number" && typeof next === "number") {
      bridge = (previous + next) / 2;
    }
    return { ...row, [bridgeKey]: bridge };
  });
}

export function coverage(
  rows: ChartRow[],
  metric: string
): { valid: number; total: number; percent: number } {
  const valid = rows.filter((row) => typeof row[metric] === "number").length;
  const total = rows.length;
  return {
    valid,
    total,
    percent: total === 0 ? 0 : Math.round((valid / total) * 100)
  };
}

export function formatUptime(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${formatMetric(value, 1)}%`;
}

// ─── Single production completeness predicate ───

export function isTierComplete(tier: TierExperience): boolean {
  return tier.complete_fixture_set && tier.response_state === "current";
}

/** For compare data where response_state is not present, check fixture set only */
export function isCompareRankable(td: TierCompareData, metricKey: "first_token_p50" | "output_speed_p50" | "total_response_p50"): boolean {
  const value = td[metricKey];
  return value !== null && td.complete_fixture_set;
}

// ─── Tier status labels (text only, no decorative glyphs) ───

export function tierStatusLabel(tier: TierExperience): string {
  if (tier.response_state === "current" && tier.complete_fixture_set) return "Complete";
  if (tier.response_state === "collecting") return "Collecting";
  if (tier.response_state === "delayed") return "Delayed";
  if (tier.response_state === "maintenance") return "Maintenance";
  if (tier.response_state === "unavailable") return "Unavailable";
  return "Pending";
}

// ─── Fleet model-level summary (typed, not fabricated) ───

export interface FleetModelSummary {
  isUnavailable: boolean;
  isComplete: boolean;
  summaryState: ResponseState;
}

export function fleetModelSummary(tiers: Record<ContextTier, TierExperience>): FleetModelSummary {
  const allTiers = Object.values(tiers);
  const isUnavailable = allTiers.every((t) => t.response_state === "unavailable");
  const isComplete = allTiers.some(
    (t) => t.response_state === "current" && t.complete_fixture_set
  );
  const summaryState: ResponseState = isUnavailable
    ? "unavailable"
    : allTiers.some((t) => t.response_state === "maintenance")
      ? "maintenance"
      : allTiers.some((t) => t.response_state === "delayed")
        ? "delayed"
        : allTiers.some((t) => t.response_state === "current")
          ? "current"
          : "collecting";
  return { isUnavailable, isComplete, summaryState };
}

// ─── Compare metric extraction ───

export type CompareMetric = "firstToken" | "outputSpeed" | "totalResponse";

export function extractCompareValue(
  td: TierCompareData,
  metric: CompareMetric
): number | null {
  switch (metric) {
    case "firstToken":
      return td.first_token_p50;
    case "outputSpeed":
      return td.output_speed_p50;
    case "totalResponse":
      return td.total_response_p50;
  }
}

export function extractOverviewCompareValue(
  tier: TierExperience,
  metric: CompareMetric
): number | null {
  switch (metric) {
    case "firstToken":
      return tier.first_token_p50;
    case "outputSpeed":
      return tier.output_speed_p50;
    case "totalResponse":
      return tier.total_response_p50;
  }
}

export function compareMetricLabel(metric: CompareMetric): string {
  switch (metric) {
    case "firstToken":
      return "First token";
    case "outputSpeed":
      return "Output speed";
    case "totalResponse":
      return "Total response";
  }
}

export function compareMetricMeta(
  metric: CompareMetric,
  sampleCount: number
): string {
  const sampleLabel = `n=${sampleCount}`;
  return metric === "outputSpeed" ? `tok/s · ${sampleLabel}` : sampleLabel;
}

export function compareDirection(metric: CompareMetric): "asc" | "desc" {
  // Latency: lower is better. Speed: higher is better.
  return metric === "outputSpeed" ? "desc" : "asc";
}

// ─── Shared y-domain for trend small multiples ───

export function sharedYDomain(
  tieredSeries: TieredSeries,
  metricKey: string
): [number, number] {
  let min = Infinity;
  let max = -Infinity;
  for (const tierKey of CONTEXT_TIERS) {
    const points = tieredSeries.tiers[tierKey]?.[metricKey] ?? [];
    for (const point of points) {
      if (point.quality === "exact" && point.value !== null) {
        if (point.value < min) min = point.value;
        if (point.value > max) max = point.value;
      }
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  const padding = (max - min) * 0.08 || 0.5;
  return [
    Math.max(0, min - padding),
    max + padding
  ];
}

export function tierLatestAttemptAge(tier: TierExperience): string {
  if (!tier.latest_attempt_at) return "First check scheduled";
  return formatAge(
    (Date.now() - Date.parse(tier.latest_attempt_at)) / 1000
  );
}
