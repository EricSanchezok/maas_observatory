import { Clock, Gauge, Pulse, ShieldCheck } from "@phosphor-icons/react";
import {
  comparisonMode,
  comparisonStatusCounts
} from "../lib/comparison";
import { formatDateTime, formatMetric } from "../lib/format";
import type { CompareItem } from "../types";
import { DataFootnote, Reveal, SectionHeading } from "./common";

const REASON_LABELS: Record<string, string> = {
  busy: "Busy",
  telemetry_pending: "Telemetry pending",
  recently_active: "Recently active",
  budget_deferred: "Budget deferred",
  scheduled_interval: "Scheduled",
  maintenance: "Maintenance",
  deferred: "Deferred",
  attempt_failed: "Attempt failed",
  awaiting_turn: "Awaiting turn"
};

function SamplingStatus({ items }: { items: CompareItem[] }) {
  const counts = comparisonStatusCounts(items);
  return (
    <div className="sampling-status">
      {counts.map(([reason, count]) => (
        <div key={reason}>
          <span>{REASON_LABELS[reason] ?? reason}</span>
          <strong>{count}</strong>
        </div>
      ))}
    </div>
  );
}

function EmptyComparison({ items }: { items: CompareItem[] }) {
  const latestAttempt = [...items]
    .filter((item) => item.latest_attempt_at)
    .sort((left, right) =>
      String(right.latest_attempt_at).localeCompare(String(left.latest_attempt_at))
    )[0];

  return (
    <div className="comparison-progress">
      <div className="comparison-progress-main">
        <div className="progress-orbit" aria-hidden="true">
          <Pulse size={22} />
        </div>
        <div>
          <span>MEASUREMENT PROGRESS</span>
          <strong>0/{items.length}</strong>
          <p>Valid observer-path experience samples</p>
        </div>
      </div>
      <SamplingStatus items={items} />
      <div className="comparison-progress-meta">
        <ShieldCheck size={17} />
        <span>Load gates remain active</span>
        <time dateTime={latestAttempt?.latest_attempt_at ?? undefined}>
          Last attempt {formatDateTime(latestAttempt?.latest_attempt_at)}
        </time>
      </div>
    </div>
  );
}

function SingleResult({
  result,
  allItems
}: {
  result: CompareItem;
  allItems: CompareItem[];
}) {
  return (
    <div className="single-result">
      <div className="single-result-value">
        <Gauge size={21} />
        <span>FIRST VALID SAMPLE</span>
        <strong>{formatMetric(result.value)}</strong>
        <small>{result.unit}</small>
      </div>
      <div className="single-result-model">
        <strong>{result.alias}</strong>
        <span>{result.profile_id ?? "profile unavailable"}</span>
        <time dateTime={result.measured_at ?? undefined}>
          {formatDateTime(result.measured_at)}
        </time>
      </div>
      <div className="single-result-pending">
        <Clock size={18} />
        <strong>{allItems.length - 1}</strong>
        <span>deployments pending</span>
      </div>
      <SamplingStatus items={allItems} />
    </div>
  );
}

function RankedResults({
  valid,
  pendingCount
}: {
  valid: CompareItem[];
  pendingCount: number;
}) {
  const sorted = [...valid].sort(
    (left, right) => (right.value ?? 0) - (left.value ?? 0)
  );
  const maximum = Math.max(...sorted.map((item) => item.value ?? 0), 1);

  return (
    <div className="ranked-results">
      {sorted.map((item, index) => (
        <div className="ranked-row" key={item.deployment_id}>
          <span className="ranked-position">
            {String(index + 1).padStart(2, "0")}
          </span>
          <div className="ranked-model">
            <strong>{item.alias}</strong>
            <span>{item.profile_id ?? "profile unavailable"}</span>
          </div>
          <div className="ranked-track">
            <span style={{ width: `${((item.value ?? 0) / maximum) * 100}%` }} />
          </div>
          <div className="ranked-value">
            <strong>{formatMetric(item.value)}</strong>
            <span>{item.unit} · n={item.sample_count}</span>
          </div>
        </div>
      ))}
      {pendingCount > 0 && (
        <div className="ranked-pending">
          <Pulse size={16} />
          {pendingCount} deployments pending a valid sample
        </div>
      )}
    </div>
  );
}

export function SpeedComparison({ items }: { items: CompareItem[] }) {
  const valid = items.filter((item) => item.value !== null);
  const mode = comparisonMode(items);

  return (
    <Reveal>
      <section className="page-section comparison-section" id="comparison">
        <SectionHeading
          index="03"
          title={mode === "ranked" ? "Experience comparison" : "Experience sampling"}
          meta="Same profile, definition, vantage and time window"
        >
          <div className="probe-profile">INTERACTIVE-SHORT-V1</div>
        </SectionHeading>
        {mode === "empty" && <EmptyComparison items={items} />}
        {mode === "single" && (
          <SingleResult result={valid[0]} allItems={items} />
        )}
        {mode === "ranked" && (
          <RankedResults
            valid={valid}
            pendingCount={items.length - valid.length}
          />
        )}
        <DataFootnote>
          <span>Global active-probe concurrency: 1</span>
          <span>Observer-path measurements, not benchmark scores</span>
          <span>Profile, vantage and sample count retained per deployment</span>
        </DataFootnote>
      </section>
    </Reveal>
  );
}
