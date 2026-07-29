import {
  ArrowUpRight,
  CheckCircle,
  WarningCircle
} from "@phosphor-icons/react";
import { formatDateTime } from "../lib/format";
import type { ObservatoryEvent, OverviewItem } from "../types";
import { Reveal, SectionHeading } from "./common";
import { ErrorChart } from "./charts";

export function EventsSection({
  events,
  models
}: {
  events: ObservatoryEvent[];
  models: OverviewItem[];
}) {
  const recentEvents = events.slice(0, 6);

  return (
    <Reveal>
      <section className="page-section events-section" id="events">
        <SectionHeading
          index="04"
          title="Events"
          meta={`${recentEvents.length} recent state transitions`}
        >
          <a className="text-link" href="/docs" target="_blank" rel="noreferrer">
            API contract <ArrowUpRight size={15} />
          </a>
        </SectionHeading>
        <div className="event-grid">
          <article className="event-list">
            <header>
              <span>RECENT EVENTS</span>
              <span>UTC+8</span>
            </header>
            {recentEvents.length > 0 ? (
              recentEvents.map((event) => {
                const model = models.find(
                  (item) => item.deployment_id === event.deployment_id
                );
                return (
                  <div className="event-row" key={event.id}>
                    <span
                      className={`event-icon severity-${event.severity}`}
                      aria-hidden="true"
                    >
                      {event.severity === "info" ? (
                        <CheckCircle size={18} />
                      ) : (
                        <WarningCircle size={18} />
                      )}
                    </span>
                    <div>
                      <strong>{event.title}</strong>
                      <span>{model?.alias ?? event.deployment_id}</span>
                    </div>
                    <span className="event-state">{event.state}</span>
                    <time dateTime={event.started_at}>
                      {formatDateTime(event.started_at)}
                    </time>
                  </div>
                );
              })
            ) : (
              <div className="event-empty">
                <CheckCircle size={22} weight="light" />
                <span>No state transitions in this window</span>
              </div>
            )}
          </article>
          <ErrorChart models={models} />
        </div>
      </section>
    </Reveal>
  );
}
