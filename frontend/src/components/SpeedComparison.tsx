import { Clock, Gauge, Pulse } from "@phosphor-icons/react";
import { comparisonMode, comparisonStatusCounts } from "../lib/comparison";
import { formatDateTime, formatMetric } from "../lib/format";
import type { CompareItem } from "../types";
import { Reveal, SectionHeading } from "./common";

const REASON_LABELS: Record<string, string> = {
  first_check_scheduled: "First check scheduled",
  scheduled_later: "Scheduled later",
  maintenance: "Maintenance",
  request_failed: "Request failed"
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
  const complete = items.filter((item) => item.complete_fixture_set).length;

  return (
    <div className="comparison-progress">
      <div className="comparison-progress-main">
        <div className="progress-orbit" aria-hidden="true">
          <Pulse size={22} />
        </div>
        <div>
          <span>Models ready</span>
          <strong>{complete}/{items.length}</strong>
        </div>
      </div>
      <SamplingStatus items={items} />
      <div className="comparison-progress-meta">
        <time dateTime={latestAttempt?.latest_attempt_at ?? undefined}>
          Last check {formatDateTime(latestAttempt?.latest_attempt_at)}
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
        <span>First result</span>
        <strong>{formatMetric(result.value)}</strong>
        <small>{result.unit}</small>
      </div>
      <div className="single-result-model">
        <strong>{result.alias}</strong>
        <span>6/6 fixtures · n={result.sample_count}</span>
        <time dateTime={result.measured_at ?? undefined}>
          {formatDateTime(result.measured_at)}
        </time>
      </div>
      <div className="single-result-pending">
        <Clock size={18} />
        <strong>{allItems.length - 1}</strong>
        <span>models still collecting</span>
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
            <span>6/6 fixtures</span>
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
          {pendingCount} models are still completing the fixture set
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
        <SectionHeading title="Compare responses" />
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
      </section>
    </Reveal>
  );
}
