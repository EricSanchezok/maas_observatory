import { Pulse } from "@phosphor-icons/react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { useMemo } from "react";
import {
  chartRows,
  coverage,
  formatLatency,
  formatMetric,
  withSingleGapBridges
} from "../lib/format";
import type { MetricSeries, OverviewItem, WindowOption } from "../types";

const tooltipStyle = {
  background: "#12171b",
  border: "1px solid #293038",
  borderRadius: "8px",
  color: "#edf2f4",
  fontFamily: "inherit",
  fontSize: "12px",
  boxShadow: "0 16px 36px rgba(0,0,0,.28)"
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
        {percent}% coverage · {valid}/{total} buckets
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
  valueFormatter = formatMetric,
  area = false,
  compact = false
}: {
  title: string;
  kicker: string;
  metric: string;
  series: MetricSeries;
  window: WindowOption;
  color: string;
  unit: string;
  valueFormatter?: (value: number | null | undefined) => string;
  area?: boolean;
  compact?: boolean;
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
  const gradientId = `fill-${metric.replaceAll("_", "-")}`;
  const enoughData = stats.valid >= 2;

  return (
    <article className={`chart-panel ${compact ? "is-compact" : ""}`}>
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
        {enoughData ? (
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
                tick={{ fill: "#6f7a84", fontSize: 10 }}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={54}
                tick={{ fill: "#6f7a84", fontSize: 10 }}
                tickFormatter={(value: number) => formatMetric(value, 0)}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                labelStyle={{ color: "#8c98a2", marginBottom: 6 }}
                formatter={(value) => [
                  `${valueFormatter(Number(value))} ${unit}`,
                  title
                ]}
              />
              {area ? (
                <Area
                  type="monotone"
                  dataKey={metric}
                  stroke={color}
                  strokeWidth={2}
                  fill={`url(#${gradientId})`}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ) : (
                <Line
                  type="monotone"
                  dataKey={metric}
                  stroke={color}
                  strokeWidth={2}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )}
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
          <EmptyChart label="At least two valid buckets are required" />
        )}
      </div>
      <Coverage {...stats} />
    </article>
  );
}

export function RequestLoadChart({
  series,
  window
}: {
  series: MetricSeries;
  window: WindowOption;
}) {
  const rows = useMemo(
    () =>
      chartRows(series, ["requests_running", "requests_waiting"], window),
    [series, window]
  );
  const stats = useMemo(
    () => coverage(rows, "requests_running"),
    [rows]
  );

  return (
    <article className="chart-panel request-load-chart">
      <header className="chart-panel-head">
        <div>
          <span>REQUESTS</span>
          <h4>Running and waiting</h4>
        </div>
        <div className="chart-legend">
          <span><i className="legend-light" />Running</span>
          <span><i className="legend-amber" />Waiting</span>
        </div>
      </header>
      <div className="chart-body">
        {stats.valid >= 2 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={rows}
              margin={{ top: 14, right: 10, bottom: 4, left: -16 }}
            >
              <CartesianGrid stroke="#20272c" vertical={false} />
              <XAxis
                dataKey="time"
                tickLine={false}
                axisLine={false}
                minTickGap={70}
                tick={{ fill: "#6f7a84", fontSize: 10 }}
              />
              <YAxis
                allowDecimals={false}
                tickLine={false}
                axisLine={false}
                width={54}
                tick={{ fill: "#6f7a84", fontSize: 10 }}
              />
              <Tooltip contentStyle={tooltipStyle} />
              <Line
                type="stepAfter"
                dataKey="requests_running"
                name="Running"
                stroke="#edf2f4"
                strokeWidth={1.75}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
              <Line
                type="stepAfter"
                dataKey="requests_waiting"
                name="Waiting"
                stroke="#e7b978"
                strokeWidth={1.75}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <EmptyChart label="Awaiting request gauge history" />
        )}
      </div>
      <Coverage {...stats} />
    </article>
  );
}

export function ErrorChart({ models }: { models: OverviewItem[] }) {
  const data = models.map((model) => ({
    alias: model.alias,
    service: model.error_statistics_24h.service_failures,
    transport: model.error_statistics_24h.transport_unconfirmed,
    measurement: model.error_statistics_24h.measurement_errors
  }));
  const total = data.reduce(
    (sum, row) => sum + row.service + row.transport + row.measurement,
    0
  );

  return (
    <article className="chart-panel error-chart">
      <header className="chart-panel-head">
        <div>
          <span>LAST 24 HOURS</span>
          <h4>Errors by deployment</h4>
        </div>
        <strong>{total}<small>recorded</small></strong>
      </header>
      <div className="chart-body">
        {total > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={data}
              margin={{ top: 14, right: 8, bottom: 4, left: -22 }}
            >
              <CartesianGrid stroke="#20272c" vertical={false} />
              <XAxis
                dataKey="alias"
                tickLine={false}
                axisLine={false}
                interval={0}
                tick={{ fill: "#6f7a84", fontSize: 9 }}
                tickFormatter={(value: string) => value.split("-")[0]}
              />
              <YAxis
                allowDecimals={false}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "#6f7a84", fontSize: 10 }}
              />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="service" name="Service" stackId="a" fill="#f07872" />
              <Bar
                dataKey="transport"
                name="Transport"
                stackId="a"
                fill="#e7b978"
              />
              <Bar
                dataKey="measurement"
                name="Measurement"
                stackId="a"
                fill="#9384c8"
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyChart label="No errors recorded in the last 24 hours" />
        )}
      </div>
    </article>
  );
}

export const latencyFormatter = formatLatency;
