import { Gauge, Timer } from "@phosphor-icons/react";
import { useCallback, useRef } from "react";
import { CONTEXT_TIERS, TIER_LABELS } from "../types";
import type {
  AvailabilityDaily,
  AvailabilityItem,
  ExperienceItem,
  WindowOption
} from "../types";
import { DailyUptimeBars } from "./charts";
import { Reveal, SectionHeading, StatePill } from "./common";
import {
  fleetModelSummary,
  formatLatency,
  formatMetric,
  formatUptime,
  tierLatestAttemptAge,
  tierStatusLabel
} from "../lib/format";

function dailyFor(
  model: ExperienceItem,
  availability: AvailabilityItem[]
): AvailabilityDaily[] {
  return (
    availability.find((item) => item.deployment_id === model.deployment_id)
      ?.daily ?? []
  );
}

function uptimeForWindow(
  model: ExperienceItem,
  dataWindow: WindowOption
): number | null {
  if (dataWindow === "7d") return model.uptime_7d;
  if (dataWindow === "30d") return model.uptime_30d;
  return model.uptime_24h;
}

function uptimeLabel(dataWindow: WindowOption): string {
  if (dataWindow === "7d") return "Uptime 7d";
  if (dataWindow === "30d") return "Uptime 30d";
  return "Uptime 24h";
}

export function FleetOverview({
  models,
  dataWindow,
  availability,
  selectedId,
  hoveredId,
  onSelect,
  onHover
}: {
  models: ExperienceItem[];
  dataWindow: WindowOption;
  availability: AvailabilityItem[];
  selectedId: string;
  hoveredId: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const gridRef = useRef<HTMLDivElement>(null);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent, modelId: string) => {
      if (!gridRef.current) return;
      const rows = Array.from(
        gridRef.current.querySelectorAll('[role="row"]')
      ).filter((r) => r.getAttribute("role") === "row" && r.getAttribute("tabindex") !== null);
      const idx = rows.indexOf(e.currentTarget as HTMLElement);

      let handled = true;
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          if (idx < rows.length - 1) {
            (rows[idx + 1] as HTMLElement).focus();
          }
          break;
        case "ArrowUp":
          e.preventDefault();
          if (idx > 0) {
            (rows[idx - 1] as HTMLElement).focus();
          }
          break;
        case "Home":
          e.preventDefault();
          (rows[0] as HTMLElement).focus();
          break;
        case "End":
          e.preventDefault();
          (rows[rows.length - 1] as HTMLElement).focus();
          break;
        case "Enter":
        case " ":
          e.preventDefault();
          onSelect(modelId);
          break;
        default:
          handled = false;
      }
      if (handled) return;
    },
    [onSelect]
  );

  const focusedIndexRef = useRef(-1);

  const onGridFocus = useCallback(() => {
    if (!gridRef.current) return;
    const rows = Array.from(
      gridRef.current.querySelectorAll('[role="row"]')
    ).filter((r) => r.getAttribute("role") === "row" && r.getAttribute("tabindex") !== null);
    if (rows.length === 0) return;
    const idx = Math.max(0, focusedIndexRef.current);
    const target = rows[Math.min(idx, rows.length - 1)] as HTMLElement;
    target.focus();
  }, []);

  if (models.length === 0) {
    return (
      <Reveal>
        <section className="page-section fleet-section" id="fleet">
          <SectionHeading title="Fleet context matrix" />
          <div className="fleet-matrix-empty">
            No models are reporting data yet. Models appear here once the observatory begins collecting response checks.
          </div>
        </section>
      </Reveal>
    );
  }

  return (
    <Reveal>
      <section className="page-section fleet-section" id="fleet">
        <SectionHeading title="Fleet context matrix" />
        <div
          className="fleet-matrix"
          ref={gridRef}
          role="grid"
          aria-label="Fleet context matrix"
          onFocus={onGridFocus}
        >
          <div className="fleet-matrix-header" role="row">
            <div className="fleet-matrix-model-col" role="columnheader">
              <span>Deployment</span>
            </div>
            {CONTEXT_TIERS.map((tier) => (
              <div
                className="fleet-matrix-tier-col"
                key={tier}
                role="columnheader"
              >
                {TIER_LABELS[tier]}
              </div>
            ))}
          </div>

          {models.map((model, gridIndex) => {
            const uptime = uptimeForWindow(model, dataWindow);
            const isSelected = model.deployment_id === selectedId;
            const isHovered = model.deployment_id === hoveredId;
            const summary = fleetModelSummary(model.tiers);

            return (
              <div
                className={`fleet-matrix-row ${isSelected ? "is-selected" : ""} ${isHovered ? "is-hovered" : ""}`}
                key={model.deployment_id}
                role="row"
                tabIndex={gridIndex === 0 ? 0 : -1}
                aria-selected={isSelected}
                onClick={() => onSelect(model.deployment_id)}
                onKeyDown={(e) => onKeyDown(e, model.deployment_id)}
                onMouseEnter={() => onHover(model.deployment_id)}
                onMouseLeave={() => onHover(null)}
                onFocus={() => {
                  focusedIndexRef.current = gridIndex;
                  onHover(model.deployment_id);
                }}
                onBlur={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                    onHover(null);
                  }
                }}
              >
                <div className="fleet-matrix-model-col" role="rowheader">
                  <div className="fleet-matrix-model-meta">
                    <strong>{model.alias}</strong>
                    <span>{model.precision}</span>
                    <span className="fleet-matrix-model-global">
                      <StatePill
                        state={summary.summaryState}
                        compact
                      />
                    </span>
                  </div>
                  <div className="fleet-matrix-uptime">
                    <span>{uptimeLabel(dataWindow)}</span>
                    <strong>{formatUptime(uptime)}</strong>
                    <DailyUptimeBars daily={dailyFor(model, availability)} />
                  </div>
                </div>

                {CONTEXT_TIERS.map((tier) => {
                  const t = model.tiers[tier];
                  const attemptFailed = t.latest_attempt_outcome === "failed";
                  const reasoningLabel = model.reasoning_enabled
                    ? "First token (incl. reasoning)"
                    : "First token";

                  return (
                    <div
                      className={`fleet-matrix-tier-col ${tier}-tier`}
                      key={tier}
                      role="gridcell"
                    >
                      <div className="fleet-tier-metrics">
                        <div className="fleet-tier-metric">
                          <Timer size={14} aria-hidden="true" />
                          <span>{reasoningLabel}</span>
                          <strong>{formatLatency(t.first_token_p50)}</strong>
                        </div>
                        <div className="fleet-tier-metric">
                          <Gauge size={14} aria-hidden="true" />
                          <span>Output speed</span>
                          <strong>
                            {formatMetric(t.output_speed_p50)}
                            {t.output_speed_p50 !== null && (
                              <small> tok/s</small>
                            )}
                          </strong>
                        </div>
                      </div>
                      <div className="fleet-tier-footer">
                        <span className="fleet-tier-sample">
                          p50 · n={t.sample_count}
                        </span>
                        <span
                          className="fleet-tier-status"
                          data-status={t.response_state}
                        >
                          {tierStatusLabel(t)}
                        </span>
                        <span className="fleet-tier-age">
                          {tierLatestAttemptAge(t)}
                        </span>
                      </div>
                      {attemptFailed && (
                        <div className="fleet-tier-failure" role="status">
                          Latest attempt failed
                        </div>
                      )}
                      {t.prompt_token_quality === "reference_mismatch" && (
                        <div className="fleet-tier-quality" role="status">
                          Tokenizer differs from reference
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </section>
    </Reveal>
  );
}
