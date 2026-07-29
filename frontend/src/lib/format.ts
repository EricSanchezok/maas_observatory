import type { MetricPoint, MetricSeries, WindowOption } from "../types";

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
  if (seconds === null) return "No telemetry";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

export function latest(points: MetricPoint[] | undefined): number | null {
  if (!points) return null;
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].value !== null) return points[index].value;
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

const WINDOW_BUCKETS: Record<WindowOption, { count: number; seconds: number }> = {
  "1h": { count: 60, seconds: 60 },
  "6h": { count: 72, seconds: 300 },
  "24h": { count: 288, seconds: 300 }
};

export function chartRows(
  series: MetricSeries,
  metrics: readonly string[],
  window: WindowOption
): ChartRow[] {
  const { count, seconds } = WINDOW_BUCKETS[window];
  const bucketMs = seconds * 1000;
  const nowBucket = Math.floor(Date.now() / bucketMs) * bucketMs;
  const values = new Map<string, Record<string, number | null>>();

  for (const metric of metrics) {
    for (const point of series[metric] ?? []) {
      const bucket = Math.floor(new Date(point.timestamp).getTime() / bucketMs) * bucketMs;
      const key = new Date(bucket).toISOString();
      const row = values.get(key) ?? {};
      row[metric] = point.value;
      values.set(key, row);
    }
  }

  return Array.from({ length: count }, (_, index) => {
    const timestamp = new Date(nowBucket - (count - index - 1) * bucketMs).toISOString();
    return {
      timestamp,
      time: formatClock(timestamp),
      ...Object.fromEntries(metrics.map((metric) => [metric, values.get(timestamp)?.[metric] ?? null]))
    };
  });
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
