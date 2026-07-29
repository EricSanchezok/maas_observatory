import {
  CaretDown,
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
  WindowOption
} from "../types";
import {
  MetricTile,
  Reveal,
  SectionHeading,
  StatePill,
  WindowControl
} from "./common";
import { latencyFormatter, MetricLineChart } from "./charts";

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
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeWithEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeWithEscape);
    };
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
          <small>MODEL</small>
          <strong>{selected.name}</strong>
        </span>
        <CaretDown size={18} className={open ? "is-open" : ""} />
      </button>
      {open && (
        <div className="model-menu" role="listbox" aria-label="Select model">
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
                  className={`model-menu-state state-${model.response_state}`}
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
  context,
  selectedId,
  onSelectedIdChange,
  dataWindow,
  onDataWindowChange,
  shortSeries,
  contextSeries,
  loading
}: {
  models: ExperienceItem[];
  selected: ExperienceItem;
  context: ExperienceItem | undefined;
  selectedId: string;
  onSelectedIdChange: (deploymentId: string) => void;
  dataWindow: WindowOption;
  onDataWindowChange: (window: WindowOption) => void;
  shortSeries: MetricSeries;
  contextSeries: MetricSeries;
  loading: boolean;
}) {
  const first =
    selected.first_response_p50 ??
    selected.latest?.first_response_seconds ??
    latest(shortSeries.first_response_seconds);
  const speed =
    selected.output_speed_p50 ??
    selected.latest?.output_speed_tps ??
    latest(shortSeries.output_speed_tps);
  const total =
    selected.total_time_p50 ??
    selected.latest?.total_time_seconds ??
    latest(shortSeries.total_time_seconds);

  return (
    <Reveal>
      <section className="page-section detail-section" id="models">
        <SectionHeading
          title="Model response"
          meta="The model and time window apply to every panel below"
        />
        <div className="detail-frame">
          <div className="detail-toolbar">
            <ModelChooser
              models={models}
              selected={selected}
              onChange={onSelectedIdChange}
            />
            <div className="detail-toolbar-meta">
              <StatePill state={selected.response_state} />
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
                  <span>QUICK CHECK</span>
                  <h3>Recent response</h3>
                </div>
                <p>
                  {selected.fixture_count}/3 fixtures · n={selected.sample_count}
                </p>
              </div>
              <div className="metric-grid metric-grid-three">
                <MetricTile
                  label="First response"
                  value={formatLatency(first)}
                  note="Request start to visible answer text"
                  icon={<Timer size={18} />}
                />
                <MetricTile
                  label="Output speed"
                  value={formatMetric(speed)}
                  unit="tok/s"
                  note="Provider-reported tokens across the stream"
                  icon={<Gauge size={18} />}
                />
                <MetricTile
                  label="Total time"
                  value={formatLatency(total)}
                  note="Request start to completed response"
                  icon={<Clock size={18} />}
                />
              </div>
              <div className="experience-chart-grid">
                <MetricLineChart
                  title="First response"
                  kicker="SECONDS"
                  metric="first_response_seconds"
                  series={shortSeries}
                  window={dataWindow}
                  color="#9be7d8"
                  unit=""
                  valueFormatter={latencyFormatter}
                />
                <MetricLineChart
                  title="Output speed"
                  kicker="TOKENS / SECOND"
                  metric="output_speed_tps"
                  series={shortSeries}
                  window={dataWindow}
                  color="#e7b978"
                  unit="tok/s"
                />
                <MetricLineChart
                  title="Total time"
                  kicker="SECONDS"
                  metric="total_time_seconds"
                  series={shortSeries}
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
                  <span>16 KIB INPUT</span>
                  <h3>Long-context check</h3>
                </div>
                <p>
                  {context?.fixture_count ?? 0}/3 fixtures · n=
                  {context?.sample_count ?? 0}
                </p>
              </div>
              <div className="metric-grid metric-grid-three">
                <MetricTile
                  label="Prompt tokens"
                  value={formatMetric(
                    context?.latest?.reported_prompt_tokens ??
                      latest(contextSeries.reported_prompt_tokens),
                    0
                  )}
                  note="Input size reported by the provider"
                  icon={<Pulse size={18} />}
                />
                <MetricTile
                  label="First response"
                  value={formatLatency(
                    context?.latest?.first_response_seconds ??
                      latest(contextSeries.first_response_seconds)
                  )}
                  note="Includes long-input processing"
                  icon={<Timer size={18} />}
                />
                <MetricTile
                  label="Output speed"
                  value={formatMetric(
                    context?.latest?.output_speed_tps ??
                      latest(contextSeries.output_speed_tps)
                  )}
                  unit="tok/s"
                  note={
                    context?.latest
                      ? "Latest valid check"
                      : "Waiting for a valid sample"
                  }
                  icon={<Gauge size={18} />}
                />
              </div>
            </section>

            <section className="detail-block reliability-block">
              <div className="detail-block-head">
                <div>
                  <span>RECENT REQUESTS</span>
                  <h3>Reliability</h3>
                </div>
              </div>
              <div className="reliability-line">
                <strong>
                  {selected.path_success_rate == null
                    ? "—"
                    : `${Math.round(selected.path_success_rate * 100)}%`}
                </strong>
                <span>completed request path</span>
                <span>
                  Measurement limitations do not count as request failures.
                </span>
              </div>
            </section>
          </div>
        </div>
      </section>
    </Reveal>
  );
}
