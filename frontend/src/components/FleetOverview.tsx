import { Gauge, Hourglass, Timer } from "@phosphor-icons/react";
import { formatAge, formatMetric } from "../lib/format";
import type { ExperienceItem } from "../types";
import { Reveal, SectionHeading, StatePill } from "./common";

function latency(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(2)} s`;
}

function value(summary: number | null, latest: number | null | undefined) {
  return summary ?? latest ?? null;
}

function pendingLabel(model: ExperienceItem) {
  if (model.latest) {
    return formatAge(
      (Date.now() - Date.parse(model.latest.measured_at)) / 1000
    );
  }
  if (model.latest_attempt_reason === "request_failed") return "Request failed";
  if (model.latest_attempt_reason === "maintenance") return "Maintenance";
  return "First check scheduled";
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
            const first = value(
              model.first_response_p50,
              model.latest?.first_response_seconds
            );
            const speed = value(
              model.output_speed_p50,
              model.latest?.output_speed_tps
            );
            const total = value(
              model.total_time_p50,
              model.latest?.total_time_seconds
            );
            return (
              <article className="experience-card" key={model.deployment_id}>
                <div className="experience-card-head">
                  <div>
                    <strong>{model.alias}</strong>
                    <span>{model.precision}</span>
                  </div>
                  <StatePill state={model.response_state} compact />
                </div>
                <div className="experience-triad">
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
                  <div>
                    <Hourglass size={15} />
                    <span>Total time</span>
                    <strong>{latency(total)}</strong>
                  </div>
                </div>
                <div className="experience-card-foot">
                  <span>
                    {model.fixture_count}/3 checks · n={model.sample_count}
                  </span>
                  <span>{pendingLabel(model)}</span>
                </div>
              </article>
            );
          })}
        </div>
        <details className="method-details">
          <summary>How this is measured</summary>
          <p>
            Each result comes from a scheduled streaming request sent by MaaS
            Observatory. First response ends at the first visible answer text;
            output speed uses provider-reported completion tokens; total time ends
            when the stream completes.
          </p>
        </details>
      </section>
    </Reveal>
  );
}
