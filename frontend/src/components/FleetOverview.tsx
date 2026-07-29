import { Gauge, Hourglass, Timer } from "@phosphor-icons/react";
import { formatAge, formatMetric } from "../lib/format";
import type { ExperienceItem } from "../types";
import { Reveal, SectionHeading } from "./common";

function latency(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(2)} s`;
}

function experienceValue(
  summary: number | null,
  latest: number | null | undefined
) {
  return summary ?? latest ?? null;
}

export function FleetOverview({ models }: { models: ExperienceItem[] }) {
  return (
    <Reveal>
      <section className="page-section fleet-section" id="fleet">
        <SectionHeading
          index="01"
          title="End-user experience"
          meta="Observer-path streaming requests · interactive-short-v1"
        />
        <div className="experience-fleet-grid">
          {models.map((model) => {
            const ttft = experienceValue(
              model.ttft_p50,
              model.latest?.client_ttft_seconds
            );
            const tps = experienceValue(
              model.streaming_tps_p50,
              model.latest?.steady_state_output_tps
            );
            const e2e = experienceValue(
              model.e2e_p50,
              model.latest?.client_e2e_seconds
            );
            return (
              <article className="experience-card" key={model.deployment_id}>
                <div className="experience-card-head">
                  <div>
                    <strong>{model.alias}</strong>
                    <span>{model.precision} · {model.profile_id}</span>
                  </div>
                  <span className={`experience-state ${model.experience_state}`}>
                    {model.experience_state.replace("experience_", "")}
                  </span>
                </div>
                <div className="experience-triad">
                  <div>
                    <Timer size={15} />
                    <span>TTFT</span>
                    <strong>{latency(ttft)}</strong>
                  </div>
                  <div>
                    <Gauge size={15} />
                    <span>Streaming</span>
                    <strong>
                      {formatMetric(tps)}
                      {tps !== null && <small> tok/s</small>}
                    </strong>
                  </div>
                  <div>
                    <Hourglass size={15} />
                    <span>E2E</span>
                    <strong>{latency(e2e)}</strong>
                  </div>
                </div>
                <div className="experience-card-foot">
                  <span>
                    n={model.sample_count} ·{" "}
                    {model.path_success_rate == null
                      ? "success pending"
                      : `${Math.round(model.path_success_rate * 100)}% path success`}
                  </span>
                  <span>
                    {model.latest
                      ? formatAge(
                          (Date.now() - Date.parse(model.latest.measured_at)) / 1000
                        )
                      : model.latest_attempt_reason ?? "awaiting turn"}
                  </span>
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </Reveal>
  );
}
