import {
  CaretDown,
  ChartLineUp,
  Check,
  Clock,
  Gauge,
  Pulse,
  Timer
} from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { formatLatency, formatMetric, latest } from "../lib/format";
import type {
  ExperienceItem,
  MetricSeries,
  OverviewItem,
  WindowOption
} from "../types";
import {
  DataFootnote,
  MetricTile,
  Reveal,
  SectionHeading,
  StatePill,
  WindowControl
} from "./common";
import { latencyFormatter, MetricLineChart, RequestLoadChart } from "./charts";

function ModelChooser({
  models,
  selected,
  onChange
}: {
  models: ExperienceItem[];
  selected: ExperienceItem;
  onChange: (deploymentId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div className="model-chooser" ref={rootRef}>
      <button
        type="button"
        className="model-chooser-trigger"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((value) => !value)}
      >
        <span>
          <small>VIEWING MODEL</small>
          <strong>{selected.display_name}</strong>
        </span>
        <CaretDown size={18} className={open ? "is-open" : ""} />
      </button>
      {open && (
        <div className="model-menu" role="listbox" aria-label="Select deployment">
          {models.map((model) => {
            const active = model.deployment_id === selected.deployment_id;
            return (
              <button
                type="button"
                role="option"
                aria-selected={active}
                className={active ? "active" : ""}
                key={model.deployment_id}
                onClick={() => {
                  onChange(model.deployment_id);
                  setOpen(false);
                }}
              >
                <span
                  className={`model-menu-state ${model.experience_state}`}
                />
                <span>
                  <strong>{model.alias}</strong>
                  <small>{model.precision}</small>
                </span>
                {active && <Check size={15} weight="bold" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ModelDetail({
  models,
  selected,
  telemetry,
  context,
  selectedId,
  onSelectedIdChange,
  dataWindow,
  onDataWindowChange,
  experienceSeries,
  contextSeries,
  telemetrySeries,
  loading
}: {
  models: ExperienceItem[];
  selected: ExperienceItem;
  telemetry: OverviewItem | undefined;
  context: ExperienceItem | undefined;
  selectedId: string;
  onSelectedIdChange: (deploymentId: string) => void;
  dataWindow: WindowOption;
  onDataWindowChange: (window: WindowOption) => void;
  experienceSeries: MetricSeries;
  contextSeries: MetricSeries;
  telemetrySeries: MetricSeries;
  loading: boolean;
}) {
  const ttft =
    selected.ttft_p50 ??
    selected.latest?.client_ttft_seconds ??
    latest(experienceSeries.client_ttft_seconds);
  const streaming =
    selected.streaming_tps_p50 ??
    selected.latest?.steady_state_output_tps ??
    latest(experienceSeries.steady_state_output_tps);
  const e2e =
    selected.e2e_p50 ??
    selected.latest?.client_e2e_seconds ??
    latest(experienceSeries.client_e2e_seconds);
  const aggregateOutput =
    telemetry?.metrics.aggregate_output_tps ??
    latest(telemetrySeries.aggregate_output_tps);

  return (
    <Reveal>
      <section className="page-section detail-section" id="models">
        <SectionHeading
          index="02"
          title="Model experience"
          meta="One model and one time window across every panel"
        />
        <div className="detail-frame">
          <div className="detail-toolbar">
            <ModelChooser
              models={models}
              selected={selected}
              onChange={onSelectedIdChange}
            />
            <div className="detail-toolbar-meta">
              {telemetry && (
                <StatePill
                  state={telemetry.service_state}
                  telemetry={telemetry.telemetry_state}
                />
              )}
              <WindowControl value={dataWindow} onChange={onDataWindowChange} />
            </div>
          </div>

          <div
            className={`detail-content ${loading ? "is-loading" : ""}`}
            data-selected-deployment={selectedId}
          >
            <section className="detail-block performance-block">
              <div className="detail-block-head">
                <div>
                  <span>OBSERVER PATH</span>
                  <h3>Interactive experience</h3>
                </div>
                <p>{selected.profile_id} · {selected.vantage_id}</p>
              </div>
              <div className="metric-grid metric-grid-three">
                <MetricTile
                  label="Client TTFT"
                  value={formatLatency(ttft)}
                  note="Request start to first output event"
                  icon={<Timer size={18} />}
                />
                <MetricTile
                  label="Streaming"
                  value={formatMetric(streaming)}
                  unit="tok/s"
                  note="Reported tokens across the steady-state stream"
                  icon={<Gauge size={18} />}
                />
                <MetricTile
                  label="End to end"
                  value={formatLatency(e2e)}
                  note="Complete observer-path request duration"
                  icon={<Clock size={18} />}
                />
              </div>
              <div className="experience-chart-grid">
                <MetricLineChart
                  title="Time to first token"
                  kicker="CLIENT TTFT"
                  metric="client_ttft_seconds"
                  series={experienceSeries}
                  window={dataWindow}
                  color="#9be7d8"
                  unit=""
                  valueFormatter={latencyFormatter}
                />
                <MetricLineChart
                  title="Streaming rate"
                  kicker="TOKENS / SECOND"
                  metric="steady_state_output_tps"
                  series={experienceSeries}
                  window={dataWindow}
                  color="#e7b978"
                  unit="tok/s"
                />
                <MetricLineChart
                  title="End-to-end time"
                  kicker="CLIENT E2E"
                  metric="client_e2e_seconds"
                  series={experienceSeries}
                  window={dataWindow}
                  color="#9384c8"
                  unit=""
                  valueFormatter={latencyFormatter}
                />
              </div>
            </section>

            <section className="detail-block context-block">
              <div className="detail-block-head">
                <div>
                  <span>LONG CONTEXT</span>
                  <h3>16 KiB request path</h3>
                </div>
                <p>Actual tokens come from provider usage</p>
              </div>
              <div className="metric-grid metric-grid-three">
                <MetricTile
                  label="Prompt tokens"
                  value={formatMetric(
                    context?.latest?.reported_prompt_tokens ??
                      latest(contextSeries.reported_prompt_tokens),
                    0
                  )}
                  note="Tokenizer-reported input size"
                  icon={<Pulse size={18} />}
                />
                <MetricTile
                  label="Client TTFT"
                  value={formatLatency(
                    context?.latest?.client_ttft_seconds ??
                      latest(contextSeries.client_ttft_seconds)
                  )}
                  note="Prefill-sensitive first output"
                  icon={<Timer size={18} />}
                />
                <MetricTile
                  label="Streaming"
                  value={formatMetric(
                    context?.latest?.steady_state_output_tps ??
                      latest(contextSeries.steady_state_output_tps)
                  )}
                  unit="tok/s"
                  note={context?.latest ? "Latest valid sample" : "Awaiting safe gate"}
                  icon={<Gauge size={18} />}
                />
              </div>
            </section>

            <section className="detail-block telemetry-block">
              <div className="detail-block-head">
                <div>
                  <span>SERVER-SIDE</span>
                  <h3>Serving telemetry</h3>
                </div>
                <p>
                  {telemetry?.observed_source_count ?? 0}/
                  {telemetry?.expected_source_count ?? 0} instances observed
                </p>
              </div>
              <div className="metric-grid">
                <MetricTile
                  label="Aggregate output"
                  value={formatMetric(aggregateOutput)}
                  unit="tok/s"
                  note="Sum of per-instance counter rates"
                  icon={<ChartLineUp size={18} />}
                />
                <MetricTile
                  label="Running"
                  value={formatMetric(telemetry?.metrics.requests_running, 0)}
                  note="Fresh instances summed"
                  icon={<Pulse size={18} />}
                />
                <MetricTile
                  label="Waiting"
                  value={formatMetric(telemetry?.metrics.requests_waiting, 0)}
                  note="Fresh instances summed"
                  icon={<Clock size={18} />}
                />
                <MetricTile
                  label="KV peak"
                  value={
                    telemetry?.metrics.kv_cache_usage == null
                      ? "—"
                      : `${formatMetric(
                          telemetry.metrics.kv_cache_usage * 100,
                          0
                        )}%`
                  }
                  note="Maximum across observed instances"
                  icon={<Gauge size={18} />}
                />
              </div>
              <MetricLineChart
                title="Aggregate output throughput"
                kicker="SERVER-SIDE TOKENS / SECOND"
                metric="aggregate_output_tps"
                series={telemetrySeries}
                window={dataWindow}
                color="#d8e1e5"
                unit="tok/s"
                area
              />
              <RequestLoadChart series={telemetrySeries} window={dataWindow} />
            </section>
          </div>
        </div>
        <DataFootnote>
          <span>Experience: observer-path streaming requests</span>
          <span>Telemetry: instance-scoped vLLM Prometheus counters</span>
          <span>Missing data is never converted to zero</span>
        </DataFootnote>
      </section>
    </Reveal>
  );
}
