import { Pulse } from "@phosphor-icons/react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  Tooltip,
  XAxis,
  YAxis,
  ResponsiveContainer
} from "recharts";
import { useMemo } from "react";
import {
  chartRows,
  coverage,
  formatMetric,
  withSingleGapBridges
} from "../lib/format";
import type { AvailabilityDaily, MetricSeries, WindowOption } from "../types";

const tooltipStyle = {
  background: "#12171b",
  border: "1px solid #293038",
  borderRadius: "8px",
  color: "#edf2f4",
  fontFamily: "inherit",
  fontSize: "12px"
};

function EmptyChart({ label }: { label: string }) {
  return (
    <div className="empty-chart">
      <Pulse size={21} weight="light" />
      <span>{label}</span>
    </div>
  );
}

function Coverage({
  valid,
  total,
  percent
}: {
  valid: number;
  total: number;
  percent: number;
}) {
  return (
    <div className="chart-coverage">
      <div>
        <span style={{ width: `${percent}%` }} />
      </div>
      <p>
        {valid === 0
          ? "No valid checks yet"
          : `${valid} of ${total} checks available`}
      </p>
    </div>
  );
}

export function MetricLineChart({
  title,
  kicker,
  metric,
  series,
  window,
  color,
  unit,
  domain,
  valueFormatter = formatMetric
}: {
  title: string;
  kicker: string;
  metric: string;
  series: MetricSeries;
  window: WindowOption;
  color: string;
  unit: string;
  domain?: [number, number];
  valueFormatter?: (value: number | null | undefined) => string;
}) {
  const rows = useMemo(
    () => withSingleGapBridges(chartRows(series, [metric], window), metric),
    [metric, series, window]
  );
  const stats = useMemo(() => coverage(rows, metric), [metric, rows]);
  const latestValue = useMemo(() => {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
      const value = rows[index][metric];
      if (typeof value === "number") return value;
    }
    return null;
  }, [metric, rows]);
  const gradientId = `fill-${metric.replaceAll("_", "-")}-${Math.random().toString(36).slice(2, 8)}`;

  return (
    <article className="chart-panel">
      <header className="chart-panel-head">
        <div>
          <span>{kicker}</span>
          <h4>{title}</h4>
        </div>
        <strong>
          {valueFormatter(latestValue)}
          {latestValue !== null && unit && <small>{unit}</small>}
        </strong>
      </header>
      <div className="chart-body">
        {stats.valid >= 2 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={rows}
              margin={{ top: 14, right: 10, bottom: 4, left: -16 }}
            >
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.2} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#20272c" vertical={false} />
              <XAxis
                dataKey="time"
                tickLine={false}
                axisLine={false}
                minTickGap={70}
                tick={{ fill: "#77828c", fontSize: 10 }}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={54}
                domain={domain ?? ["auto", "auto"]}
                tick={{ fill: "#77828c", fontSize: 10 }}
                tickFormatter={(value: number) => formatMetric(value, 0)}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                labelStyle={{ color: "#bbc4cb", marginBottom: 6 }}
                formatter={(value) => [
                  `${valueFormatter(Number(value))} ${unit}`,
                  title
                ]}
              />
              <Area
                type="monotone"
                dataKey={metric}
                stroke={color}
                strokeWidth={2}
                fill={`url(#${gradientId})`}
                connectNulls={false}
                isAnimationActive={false}
              />
              <Line
                type="linear"
                dataKey={`${metric}_bridge`}
                stroke={color}
                strokeWidth={1.5}
                strokeDasharray="4 5"
                strokeOpacity={0.55}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
                legendType="none"
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <EmptyChart label="Waiting for valid checks" />
        )}
      </div>
      <Coverage {...stats} />
    </article>
  );
}

export const latencyFormatter = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return "—";
  if (value < 1) return `${formatMetric(value * 1000, 0)} ms`;
  return `${formatMetric(value, 2)} s`;
};

const UPTIME_MINT = "#9be7d8";
const UPTIME_AMBER = "#e7b978";
const UPTIME_CORAL = "#f07872";
const UPTIME_EMPTY = "#3a3f4b";

function uptimeColor(uptimePct: number | null): string {
  if (uptimePct === null) return UPTIME_EMPTY;
  if (uptimePct >= 99.5) return UPTIME_MINT;
  if (uptimePct >= 95) return UPTIME_AMBER;
  return UPTIME_CORAL;
}

export function DailyUptimeBars({ daily }: { daily: AvailabilityDaily[] }) {
  const days = daily.slice(-30);
  return (
    <div
      className="daily-uptime-bars"
      role="img"
      aria-label={`Daily uptime over ${days.length} days`}
    >
      {days.map((day) => (
        <span
          className="daily-uptime-bar"
          key={day.date}
          title={`${day.date} · ${
            day.uptime_pct === null
              ? "no samples"
              : `${day.uptime_pct.toFixed(1)}%`
          } · n=${day.samples}`}
        >
          <i
            style={{
              height:
                day.uptime_pct === null
                  ? "3px"
                  : `${Math.max(day.uptime_pct, 2)}%`,
              background: uptimeColor(day.uptime_pct)
            }}
          />
        </span>
      ))}
    </div>
  );
}
