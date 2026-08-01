import { Gauge, Pulse, Timer } from "@phosphor-icons/react";
import { useCallback, useState } from "react";
import { CONTEXT_TIERS, TIER_LABELS } from "../types";
import type { CompareItem, ContextTier } from "../types";
import type { CompareMetric } from "../lib/format";
import {
  comparisonStatusCounts,
  getRankedItems,
  getSharedMetricMaximum
} from "../lib/comparison";
import {
  compareMetricMeta,
  extractCompareValue,
  formatDateTime,
  formatLatency,
  formatMetric
} from "../lib/format";
import { Reveal, SectionHeading } from "./common";

const REASON_LABELS: Record<string, string> = {
  first_check_scheduled: "First check scheduled",
  scheduled_later: "Scheduled later",
  maintenance: "Maintenance",
  measurement_limited: "Measurement unavailable",
  request_failed: "Request failed"
};

const METRIC_OPTIONS: { value: CompareMetric; icon: React.ReactNode; label: string }[] = [
  { value: "firstToken", icon: <Timer size={16} />, label: "First token" },
  { value: "outputSpeed", icon: <Gauge size={16} />, label: "Output speed" },
  { value: "totalResponse", icon: <Pulse size={16} />, label: "Total response" }
];

type SortMode = "ranked" | "model";

function SamplingStatus({
  items,
  tier,
  metric
}: {
  items: CompareItem[];
  tier: ContextTier;
  metric: CompareMetric;
}) {
  const counts = comparisonStatusCounts(items, tier, metric);
  if (counts.length === 0) return null;
  return (
    <div className="sampling-status">
      {counts.map(([reason, count]) => (
        <div key={reason}>
          <span>{REASON_LABELS[reason] ?? "Awaiting measurement"}</span>
          <strong>{count}</strong>
        </div>
      ))}
    </div>
  );
}

function EmptyComparison({
  items,
  tier,
  metric
}: {
  items: CompareItem[];
  tier: ContextTier;
  metric: CompareMetric;
}) {
  const latestAttempt = [...items]
    .filter((item) => item.tiers[tier].latest_attempt_at)
    .sort((left, right) =>
      String(right.tiers[tier].latest_attempt_at).localeCompare(
        String(left.tiers[tier].latest_attempt_at)
      )
    )[0];
  const complete = items.filter(
    (item) => item.tiers[tier].complete_fixture_set
  ).length;

  return (
    <div className="comparison-progress">
      <div className="comparison-progress-main">
        <div className="progress-orbit" aria-hidden="true">
          <Pulse size={22} />
        </div>
        <div>
          <span>Models ready</span>
          <strong>
            {complete}/{items.length}
          </strong>
        </div>
      </div>
      <SamplingStatus items={items} tier={tier} metric={metric} />
      <div className="comparison-progress-meta">
        <time dateTime={latestAttempt?.tiers[tier].latest_attempt_at ?? undefined}>
          Last check{" "}
          {formatDateTime(latestAttempt?.tiers[tier].latest_attempt_at)}
        </time>
      </div>
    </div>
  );
}

function formatCompareValue(value: number | null, metric: CompareMetric): string {
  if (value === null) return "—";
  if (metric === "outputSpeed") return formatMetric(value);
  return formatLatency(value);
}

export function SpeedComparison({
  items,
  selectedId,
  hoveredId,
  onSelect,
  onHover
}: {
  items: CompareItem[];
  selectedId: string;
  hoveredId: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const [metric, setMetric] = useState<CompareMetric>("firstToken");
  const [sortMode, setSortMode] = useState<SortMode>("ranked");
  const sharedMaxValue = getSharedMetricMaximum(items, metric);

  const handleRowKeyDown = useCallback(
    (e: React.KeyboardEvent, _tier: ContextTier, modelId: string, columnEl: HTMLElement | null) => {
      if (!columnEl) return;
      const rows = Array.from(
        columnEl.querySelectorAll('[role="button"]')
      );
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

  return (
    <Reveal>
      <section className="page-section comparison-section" id="comparison">
        <SectionHeading title="Compare responses">
          <div className="compare-controls">
            <div className="compare-metric-selector" role="group" aria-label="Metric">
              {METRIC_OPTIONS.map((opt) => (
                <button
                  type="button"
                  key={opt.value}
                  className={metric === opt.value ? "active" : ""}
                  aria-pressed={metric === opt.value}
                  onClick={() => setMetric(opt.value)}
                >
                  {opt.icon}
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="compare-sort-toggle" role="group" aria-label="Sort mode">
              <button
                type="button"
                className={sortMode === "ranked" ? "active" : ""}
                aria-pressed={sortMode === "ranked"}
                onClick={() => setSortMode("ranked")}
              >
                Ranked
              </button>
              <button
                type="button"
                className={sortMode === "model" ? "active" : ""}
                aria-pressed={sortMode === "model"}
                onClick={() => setSortMode("model")}
              >
                Model order
              </button>
            </div>
          </div>
        </SectionHeading>

        <div className="tier-columns">
          {CONTEXT_TIERS.map((tier) => {
            const complete = items.filter(
              (item) => item.tiers[tier].complete_fixture_set
            ).length;

            if (complete === 0) {
              return (
                <div className="tier-column" key={tier}>
                  <div className="tier-column-head">
                    <span>{TIER_LABELS[tier]}</span>
                  </div>
                  <EmptyComparison items={items} tier={tier} metric={metric} />
                </div>
              );
            }

            if (sortMode === "model") {
              const sorted = [...items].sort((a, b) => a.alias.localeCompare(b.alias));
              return (
                <div className="tier-column" key={tier}>
                  <div className="tier-column-head">
                    <span>{TIER_LABELS[tier]}</span>
                    <span className="tier-column-count">{complete} complete</span>
                  </div>
                  <div className="tier-column-rows">
                    {sorted.map((item) => {
                      const td = item.tiers[tier];
                      const value = extractCompareValue(td, metric);
                      const rankable = td.complete_fixture_set && value !== null;
                      const isSelected = item.deployment_id === selectedId;
                      const isHovered = item.deployment_id === hoveredId;

                      return (
                        <div
                          className={`tier-column-row ${isSelected ? "is-selected" : ""} ${isHovered ? "is-hovered" : ""} ${!rankable ? "is-incomplete" : ""}`}
                          key={item.deployment_id}
                          role="button"
                          tabIndex={isSelected ? 0 : -1}
                          aria-pressed={isSelected}
                          onClick={() => onSelect(item.deployment_id)}
                          onKeyDown={(e) => handleRowKeyDown(e, tier, item.deployment_id, e.currentTarget.parentElement)}
                          onMouseEnter={() => onHover(item.deployment_id)}
                          onMouseLeave={() => onHover(null)}
                          onFocus={() => onHover(item.deployment_id)}
                          onBlur={(e) => {
                            if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                              onHover(null);
                            }
                          }}
                        >
                          <div className="tier-row-model">
                            <strong>{item.alias}</strong>
                            <span>
                              2 fixtures
                              {!rankable && (
                                <span className="tier-row-incomplete"> · Pending</span>
                              )}
                            </span>
                          </div>
                          <div className="tier-row-value">
                            <strong>{formatCompareValue(value, metric)}</strong>
                            <span>
                              {rankable
                                ? compareMetricMeta(metric, td.sample_count)
                                : "Pending measurement"}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <SamplingStatus items={items} tier={tier} metric={metric} />
                </div>
              );
            }

            // Ranked mode
            const ranked = getRankedItems(items, tier, metric);
            const isLatency = metric === "firstToken" || metric === "totalResponse";

            return (
              <div className="tier-column" key={tier}>
                <div className="tier-column-head">
                  <span>{TIER_LABELS[tier]}</span>
                  <span className="tier-column-count">{ranked.length} ranked</span>
                </div>
                <div className="tier-column-rows">
                  {ranked.map((item, index) => {
                    const td = item.tiers[tier];
                    const value = extractCompareValue(td, metric) ?? 0;
                    const trackPct = isLatency
                      ? ((sharedMaxValue - value) / sharedMaxValue) * 100
                      : (value / sharedMaxValue) * 100;

                    const isSelected = item.deployment_id === selectedId;
                    const isHovered = item.deployment_id === hoveredId;

                    return (
                      <div
                        className={`tier-column-row ${isSelected ? "is-selected" : ""} ${isHovered ? "is-hovered" : ""}`}
                        key={item.deployment_id}
                        role="button"
                        tabIndex={isSelected ? 0 : -1}
                        aria-pressed={isSelected}
                        onClick={() => onSelect(item.deployment_id)}
                        onKeyDown={(e) => handleRowKeyDown(e, tier, item.deployment_id, e.currentTarget.parentElement)}
                        onMouseEnter={() => onHover(item.deployment_id)}
                        onMouseLeave={() => onHover(null)}
                        onFocus={() => onHover(item.deployment_id)}
                        onBlur={(e) => {
                          if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                            onHover(null);
                          }
                        }}
                      >
                        <span className="tier-row-pos" aria-hidden="true">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <div className="tier-row-model">
                          <strong>{item.alias}</strong>
                          <span>
                            2 fixtures
                          </span>
                        </div>
                        <div className="tier-row-track">
                          <span
                            className="tier-row-track-fill"
                            style={{ width: `${trackPct}%` }}
                          />
                        </div>
                        <div className="tier-row-value">
                          <strong>{formatCompareValue(extractCompareValue(td, metric), metric)}</strong>
                          <span>{compareMetricMeta(metric, td.sample_count)}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
                {items.filter((item) => !ranked.some((r) => r.deployment_id === item.deployment_id)).length > 0 && (
                  <div className="tier-column-pending">
                    <Pulse size={14} aria-hidden="true" />
                    {items.filter((item) => !ranked.some((r) => r.deployment_id === item.deployment_id)).length} pending
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </Reveal>
  );
}
