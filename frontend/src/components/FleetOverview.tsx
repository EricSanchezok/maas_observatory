import { Gauge, Timer, WarningCircle } from "@phosphor-icons/react";
import { formatAge, formatMetric } from "../lib/format";
import type { ExperienceItem } from "../types";
import { Reveal, SectionHeading, StatePill } from "./common";

function latency(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(2)} s`;
}

function pendingLabel(model: ExperienceItem) {
  const labels: Record<string, string> = {
    connect: "Connection failed",
    route_failed: "Connection failed",
    timeout: "Request timed out",
    empty_visible_output: "No visible response",
    stream_stall: "Stream stalled"
  };
  if (model.latest_attempt_outcome === "failed") {
    return labels[model.latest_attempt_error_code ?? ""] ?? "Request failed";
  }
  if (model.response_state === "unavailable") {
    return (
      model.state_reasons
        .map((reason) => labels[reason])
        .find((label) => label !== undefined) ?? "Connection unavailable"
    );
  }
  if (model.latest) {
    return formatAge(
      (Date.now() - Date.parse(model.latest.measured_at)) / 1000
    );
  }
  if (model.latest_attempt_reason === "request_failed") return "Request failed";
  if (model.latest_attempt_reason === "maintenance") return "Maintenance";
  return "First check scheduled";
}

function latestAttemptAge(model: ExperienceItem) {
  if (!model.latest_attempt_at) return "First check scheduled";
  return formatAge(
    (Date.now() - Date.parse(model.latest_attempt_at)) / 1000
  );
}

export function FleetOverview({ models }: { models: ExperienceItem[] }) {
  return (
    <Reveal>
      <section className="page-section fleet-section" id="fleet">
        <SectionHeading
          title="Live response"
          meta="Recent streaming checks from this server"
        />
        <div className="experience-fleet-grid">
          {models.map((model) => {
            const latestIsValid =
              model.response_state === "current" &&
              model.latest_attempt_outcome === "success" &&
              model.latest !== null;
            const liveFailure = model.response_state === "unavailable";
            const first = latestIsValid
              ? model.latest?.first_response_seconds ?? null
              : null;
            const speed = latestIsValid
              ? model.latest?.output_speed_tps ?? null
              : null;
            return (
              <article
                className={`experience-card ${liveFailure ? "is-failed" : ""}`}
                key={model.deployment_id}
              >
                <div className="experience-card-head">
                  <div>
                    <strong>{model.alias}</strong>
                    <span>{model.precision}</span>
                  </div>
                  <StatePill state={model.response_state} compact />
                </div>
                <div className="experience-triad experience-pair">
                  <div>
                    <Timer size={15} />
                    <span>First response</span>
                    <strong>{latency(first)}</strong>
                  </div>
                  <div>
                    <Gauge size={15} />
                    <span>Output speed</span>
                    <strong>
                      {formatMetric(speed)}
                      {speed !== null && <small> tok/s</small>}
                    </strong>
                  </div>
                </div>
                {liveFailure && (
                  <div className="live-failure" role="status">
                    <WarningCircle size={15} />
                    <span>{pendingLabel(model)}</span>
                  </div>
                )}
                <div className="experience-card-foot">
                  <span>
                    {model.fixture_count}/6 checks · n={model.sample_count}
                  </span>
                  <span>{latestAttemptAge(model)}</span>
                </div>
              </article>
            );
          })}
        </div>
        <details className="method-details">
          <summary>How this is measured</summary>
          <p>
            Live cards show the latest scheduled streaming request. A failed
            request clears its values immediately. First response ends at the first
            visible answer text; output speed uses provider-reported completion
            tokens.
          </p>
        </details>
      </section>
    </Reveal>
  );
}
