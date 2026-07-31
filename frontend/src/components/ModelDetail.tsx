import {
  Brain,
  CaretDown,
  Check,
  Gauge,
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
  selectedId,
  onSelectedIdChange,
  dataWindow,
  onDataWindowChange,
  responseSeries,
  loading
}: {
  models: ExperienceItem[];
  selected: ExperienceItem;
  selectedId: string;
  onSelectedIdChange: (deploymentId: string) => void;
  dataWindow: WindowOption;
  onDataWindowChange: (window: WindowOption) => void;
  responseSeries: MetricSeries;
  loading: boolean;
}) {
  const isReasoning = selected.reasoning_tokens_p50 !== null;
  const firstToken = selected.first_token_p50;
  const first =
    selected.first_response_p50 ??
    latest(responseSeries.first_response_seconds);
  const reasoningTokens = selected.reasoning_tokens_p50;
  const speed =
    selected.output_speed_p50 ??
    latest(responseSeries.output_speed_tps);

  return (
    <Reveal>
      <section className="page-section detail-section" id="models">
        <SectionHeading
          title="Model response"
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
                  <h3>Median response</h3>
                </div>
                <p>
                  median · n={selected.sample_count}
                </p>
              </div>
              <div className="metric-grid metric-grid-two">
                {isReasoning ? (
                  <>
                    <MetricTile
                      label="First token (含推理)"
                      value={formatLatency(firstToken)}
                      icon={<Timer size={18} />}
                    />
                    <MetricTile
                      label="First answer"
                      value={formatLatency(first)}
                      icon={<Timer size={18} />}
                    />
                    <MetricTile
                      label="推理 tokens（估计）"
                      value={formatMetric(reasoningTokens, 0)}
                      icon={<Brain size={18} />}
                    />
                    <MetricTile
                      label="Output speed"
                      value={formatMetric(speed)}
                      unit="tok/s"
                      icon={<Gauge size={18} />}
                    />
                  </>
                ) : (
                  <>
                    <MetricTile
                      label="First response"
                      value={formatLatency(first)}
                      icon={<Timer size={18} />}
                    />
                    <MetricTile
                      label="Output speed"
                      value={formatMetric(speed)}
                      unit="tok/s"
                      icon={<Gauge size={18} />}
                    />
                  </>
                )}
              </div>
              <div className="experience-chart-grid">
                <MetricLineChart
                  title="First response"
                  kicker="SECONDS"
                  metric="first_response_seconds"
                  series={responseSeries}
                  window={dataWindow}
                  color="#9be7d8"
                  unit=""
                  valueFormatter={latencyFormatter}
                />
                <MetricLineChart
                  title="Output speed"
                  kicker="TOKENS / SECOND"
                  metric="output_speed_tps"
                  series={responseSeries}
                  window={dataWindow}
                  color="#e7b978"
                  unit="tok/s"
                />
              </div>
            </section>

            <section className="detail-block reliability-block">
              <div className="detail-block-head">
                <div>
                  <h3>Reliability</h3>
                </div>
              </div>
              <div className="reliability-line">
                <strong>
                  {selected.path_success_rate == null
                    ? "—"
                    : `${Math.round(selected.path_success_rate * 100)}%`}
                </strong>
                <span>successful checks</span>
                <span>{selected.sample_count} samples</span>
              </div>
            </section>
          </div>
        </div>
      </section>
    </Reveal>
  );
}
