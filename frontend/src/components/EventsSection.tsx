import {
  ArrowUpRight,
  CheckCircle,
  WarningCircle
} from "@phosphor-icons/react";
import { formatDateTime } from "../lib/format";
import type { ObservatoryEvent } from "../types";
import { Reveal, SectionHeading } from "./common";

export function EventsSection({ events }: { events: ObservatoryEvent[] }) {
  const recentEvents = events.slice(0, 8);

  return (
    <Reveal>
      <section className="page-section events-section" id="events">
        <SectionHeading
          title="Recent activity"
          meta={`${recentEvents.length} response changes in this window`}
        >
          <a className="text-link" href="/docs" target="_blank" rel="noreferrer">
            Measurement API <ArrowUpRight size={15} />
          </a>
        </SectionHeading>
        <article className="event-list event-list-wide">
          <header>
            <span>RESPONSE CHANGES</span>
            <span>LOCAL TIME</span>
          </header>
          {recentEvents.length > 0 ? (
            recentEvents.map((event) => (
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
                  <span>{event.alias}</span>
                </div>
                <span className="event-state">{event.state}</span>
                <time dateTime={event.started_at}>
                  {formatDateTime(event.started_at)}
                </time>
              </div>
            ))
          ) : (
            <div className="event-empty">
              <CheckCircle size={22} weight="light" />
              <span>No response changes in this window</span>
            </div>
          )}
        </article>
      </section>
    </Reveal>
  );
}
