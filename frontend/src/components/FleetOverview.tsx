import { Gauge, Timer, WarningCircle } from "@phosphor-icons/react";
import {
  formatAge,
  formatLatency,
  formatMetric,
  formatUptime
} from "../lib/format";
import type {
  AvailabilityDaily,
  AvailabilityItem,
  ExperienceItem,
  WindowOption
} from "../types";
import { DailyUptimeBars } from "./charts";
import { Reveal, SectionHeading, StatePill } from "./common";

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

function dailyFor(
  model: ExperienceItem,
  availability: AvailabilityItem[]
): AvailabilityDaily[] {
  return (
    availability.find((item) => item.deployment_id === model.deployment_id)
      ?.daily ?? []
  );
}

function latestAttemptAge(model: ExperienceItem) {
  if (!model.latest_attempt_at) return "First check scheduled";
  return formatAge(
    (Date.now() - Date.parse(model.latest_attempt_at)) / 1000
  );
}

function latestSampleNote(model: ExperienceItem): string {
  if (!model.latest) return "No single-sample measurement yet";
  return [
    "Latest sample",
    formatAge((Date.now() - Date.parse(model.latest.measured_at)) / 1000),
    `first response ${formatLatency(model.latest.first_response_seconds)}`,
    `output speed ${formatMetric(model.latest.output_speed_tps)} tok/s`
  ].join(" · ");
}

export function FleetOverview({
  models,
  dataWindow,
  availability
}: {
  models: ExperienceItem[];
  dataWindow: WindowOption;
  availability: AvailabilityItem[];
}) {
  return (
    <Reveal>
      <section className="page-section fleet-section" id="fleet">
        <SectionHeading title="Live response" />
        <div className="experience-fleet-grid">
          {models.map((model) => {
            const uptime = uptimeForWindow(model, dataWindow);
            const attemptFailed =
              model.latest_attempt_outcome !== null &&
              model.latest_attempt_outcome !== "success";
            return (
              <article
                className={`experience-card ${
                  model.response_state === "unavailable" ? "is-failed" : ""
                }`}
                key={model.deployment_id}
              >
                <div className="experience-card-head">
                  <div>
                    <strong>{model.alias}</strong>
                    <span>{model.precision}</span>
                  </div>
                  <StatePill state={model.response_state} compact />
                </div>
                <div className="experience-uptime">
                  <div className="experience-uptime-head">
                    <span>{uptimeLabel(dataWindow)}</span>
                    <strong>{formatUptime(uptime)}</strong>
                  </div>
                  <DailyUptimeBars daily={dailyFor(model, availability)} />
                </div>
                <div className="experience-triad experience-pair">
                  <div>
                    <Timer size={15} />
                    <span>First response</span>
                    <strong>{formatLatency(model.first_response_p50)}</strong>
                  </div>
                  <div>
                    <Gauge size={15} />
                    <span>Output speed</span>
                    <strong>
                      {formatMetric(model.output_speed_p50)}
                      {model.output_speed_p50 !== null && <small> tok/s</small>}
                    </strong>
                  </div>
                </div>
                <div className="experience-card-note">
                  p50 · n={model.sample_count}
                </div>
                {attemptFailed && (
                  <div className="live-failure" role="status">
                    <WarningCircle size={15} />
                    <span>最近一次生成失败</span>
                  </div>
                )}
                <div className="experience-card-foot">
                  <span title={latestSampleNote(model)}>
                    {latestAttemptAge(model)}
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
