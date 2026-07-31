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
