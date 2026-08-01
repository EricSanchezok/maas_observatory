import {
  Brain,
  CaretDown,
  Check,
  Gauge,
  Timer
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CONTEXT_TIERS, TIER_LABELS } from "../types";
import type {
  ExperienceItem,
  TieredSeries,
  WindowOption
} from "../types";
import { MetricLineChart, latencyFormatter } from "./charts";
import { MetricTile, Reveal, SectionHeading, StatePill } from "./common";
import {
  formatLatency,
  formatMetric,
  sharedYDomain
} from "../lib/format";

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
  const listboxRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeWithEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeWithEscape);
    };
  }, [open]);

  // Focus first option when opening
  useEffect(() => {
    if (open && listboxRef.current) {
      const first = listboxRef.current.querySelector('[role="option"]') as HTMLElement;
      first?.focus();
    }
  }, [open]);

  const handleOptionKeyDown = useCallback(
    (e: React.KeyboardEvent, deploymentId: string) => {
      const options = Array.from(
        listboxRef.current?.querySelectorAll('[role="option"]') ?? []
      );
      const idx = options.indexOf(e.currentTarget as HTMLElement);

      let handled = true;
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          if (idx < options.length - 1) {
            (options[idx + 1] as HTMLElement).focus();
          }
          break;
        case "ArrowUp":
          e.preventDefault();
          if (idx > 0) {
            (options[idx - 1] as HTMLElement).focus();
          }
          break;
        case "Home":
          e.preventDefault();
          (options[0] as HTMLElement).focus();
          break;
        case "End":
          e.preventDefault();
          (options[options.length - 1] as HTMLElement).focus();
          break;
        case "Enter":
        case " ":
          e.preventDefault();
          onChange(deploymentId);
          setOpen(false);
          triggerRef.current?.focus();
          break;
        case "Escape":
          e.preventDefault();
          setOpen(false);
          triggerRef.current?.focus();
          break;
        default:
          handled = false;
      }
      if (handled) return;
    },
    [onChange]
  );

  return (
    <div className="model-chooser" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="model-chooser-trigger"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <span>
          <small>MODEL</small>
          <strong>{selected.name}</strong>
        </span>
        <CaretDown size={18} className={open ? "is-open" : ""} />
      </button>
      {open && (
        <div className="model-menu" ref={listboxRef} role="listbox" aria-label="Select model">
          {models.map((model) => {
            const active = model.deployment_id === selected.deployment_id;
            const hasCurrent = Object.values(model.tiers).some(
              (t) => t.response_state === "current" && t.complete_fixture_set
            );
            const isUnavailable = Object.values(model.tiers).every(
              (t) => t.response_state === "unavailable"
            );
            return (
              <button
                type="button"
                role="option"
                aria-selected={active}
                className={active ? "active" : ""}
                key={model.deployment_id}
                tabIndex={active ? 0 : -1}
                onClick={() => {
                  onChange(model.deployment_id);
                  setOpen(false);
                }}
                onKeyDown={(e) => handleOptionKeyDown(e, model.deployment_id)}
              >
                <span
                  className={`model-menu-state ${
                    hasCurrent ? "state-current"
                    : isUnavailable ? "state-unavailable"
                    : ""
                  }`}
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

const METRIC_CHART_MAP: Array<{
  metric: string;
  label: string;
  kicker: string;
  color: string;
  isLatency: boolean;
}> = [
  {
    metric: "first_token_seconds",
    label: "First token",
    kicker: "SECONDS",
    color: "#9be7d8",
    isLatency: true
  },
  {
    metric: "first_response_seconds",
    label: "First answer",
    kicker: "SECONDS",
    color: "#84b6e3",
    isLatency: true
  },
  {
    metric: "total_response_seconds",
    label: "Total response",
    kicker: "SECONDS",
    color: "#9384c8",
    isLatency: true
  },
  {
    metric: "output_speed_tps",
    label: "Output speed",
    kicker: "TOKENS / SECOND",
    color: "#e7b978",
    isLatency: false
  }
];

export function ModelDetail({
  models,
  selected,
  selectedId,
  onSelectedIdChange,
  dataWindow,
  tieredSeries,
  loading
}: {
  models: ExperienceItem[];
  selected: ExperienceItem;
  selectedId: string;
  onSelectedIdChange: (deploymentId: string) => void;
  dataWindow: WindowOption;
  tieredSeries: TieredSeries | null;
  loading: boolean;
}) {
  // Guard series data by deployment_id
  const safeSeries =
    tieredSeries && tieredSeries.deployment_id === selectedId
      ? tieredSeries
      : null;

  return (
    <Reveal>
      <section className="page-section detail-section" id="models">
        <SectionHeading title="Model response" />
        <div className="detail-frame" aria-busy={loading}>
          <div className="detail-toolbar">
            <ModelChooser
              models={models}
              selected={selected}
              onChange={onSelectedIdChange}
            />
            <div className="detail-toolbar-meta">
              <span className="sr-only" role="status" aria-live="polite">
                {loading ? "Loading model data" : ""}
              </span>
            </div>
          </div>

          <div
            className={`detail-content ${loading ? "is-loading" : ""}`}
          >
            <section className="detail-block performance-block">
              <div className="detail-block-head">
                <div>
                  <h3>Per-tier metrics</h3>
                </div>
                <p>All three context windows</p>
              </div>

              <div className="tier-metric-grid">
                {CONTEXT_TIERS.map((tier) => {
                  const t = selected.tiers[tier];
                   const reasoningLabel = selected.reasoning_enabled

                    ? "First token (incl. reasoning)"
                    : "First token";
                  return (
                    <div className="tier-metric-group" key={tier}>
                      <h4 className="tier-metric-group-label">
                        {TIER_LABELS[tier]}
                        <StatePill state={t.response_state} compact />
                      </h4>
                      <div className="metric-grid metric-grid-two tier-metric-group-grid">
                        {selected.reasoning_enabled ? (
                          <>
                            <MetricTile
                              label={reasoningLabel}
                              value={formatLatency(t.first_token_p50)}
                              icon={<Timer size={18} />}
                            />
                            <MetricTile
                              label="First answer"
                              value={formatLatency(t.first_response_p50)}
                              icon={<Timer size={18} />}
                            />
                            <MetricTile
                              label="Total response"
                              value={formatLatency(t.total_response_p50)}
                              icon={<Timer size={18} />}
                            />
                            <MetricTile
                              label={
                                t.reasoning_tokens_quality === "reported"
                                  ? "Reasoning tokens"
                                  : "Reasoning estimate"
                              }
                              value={formatMetric(t.reasoning_tokens_p50, 0)}
                              unit="tokens"
                              icon={<Brain size={18} />}
                            />
                            <MetricTile
                              label="Output speed"
                              value={formatMetric(t.output_speed_p50)}
                              unit="tok/s"
                              icon={<Gauge size={18} />}
                            />
                          </>
                        ) : (
                          <>
                            <MetricTile
                              label="First token"
                              value={formatLatency(t.first_token_p50)}
                              icon={<Timer size={18} />}
                            />
                            <MetricTile
                              label="First answer"
                              value={formatLatency(t.first_response_p50)}
                              icon={<Timer size={18} />}
                            />
                            <MetricTile
                              label="Total response"
                              value={formatLatency(t.total_response_p50)}
                              icon={<Timer size={18} />}
                            />
                            <MetricTile
                              label="Output speed"
                              value={formatMetric(t.output_speed_p50)}
                              unit="tok/s"
                              icon={<Gauge size={18} />}
                            />
                          </>
                        )}
                      </div>
                      <div className="tier-metric-note">
                        n={t.sample_count} · {t.complete_fixture_set ? "2/2 fixtures" : "incomplete fixture set"}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="detail-block trends-block">
              <div className="detail-block-head">
                <div>
                  <h3>Trends</h3>
                </div>
                <p>Small multiples across tiers</p>
              </div>

              <div className="trends-small-multiples">
                {safeSeries
                  ? METRIC_CHART_MAP.map((chartDef) => {
                      const [domainMin, domainMax] = sharedYDomain(
                        safeSeries,
                        chartDef.metric
                      );

                      return (
                        <div className="trend-row" key={chartDef.metric}>
                          <div className="trend-row-label">
                            <span className="trend-row-kicker">
                              {chartDef.kicker}
                            </span>
                            <h4>{chartDef.label}</h4>
                          </div>
                          <div className="trend-row-charts">
                            {CONTEXT_TIERS.map((tierKey) => (
                              <div className="trend-tier-chart" key={tierKey}>
                                <span className="trend-tier-chart-label">
                                  {TIER_LABELS[tierKey]}
                                </span>
                                <MetricLineChart
                                  title={chartDef.label}
                                  kicker={chartDef.kicker}
                                  metric={chartDef.metric}
                                  series={safeSeries.tiers[tierKey] ?? {}}
                                  window={dataWindow}
                                  color={chartDef.color}
                                  unit={chartDef.isLatency ? "" : "tok/s"}
                                  domain={[domainMin, domainMax]}
                                  valueFormatter={
                                    chartDef.isLatency ? latencyFormatter : formatMetric
                                  }
                                />
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })
                  : null}
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
                <span>
                  {Object.values(selected.tiers).reduce(
                    (sum, t) => sum + t.sample_count,
                    0
                  )}{" "}
                  total samples
                </span>
              </div>
            </section>
          </div>
        </div>
      </section>
    </Reveal>
  );
}
