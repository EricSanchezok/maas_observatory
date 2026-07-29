import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import type { ServiceState, WindowOption } from "../types";

const WINDOWS: WindowOption[] = ["1h", "6h", "24h"];
const STATE_LABELS: Record<ServiceState, string> = {
  operational: "Operational",
  slow: "Slow",
  degraded: "Degraded",
  unavailable: "Unavailable",
  maintenance: "Maintenance",
  unknown: "Unknown"
};

export function Reveal({
  children,
  className = ""
}: {
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "-5% 0px" }
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`reveal ${visible ? "is-visible" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

export function StatePill({
  state,
  telemetry,
  compact = false
}: {
  state: ServiceState;
  telemetry?: string;
  compact?: boolean;
}) {
  return (
    <span className={`state-pill state-${state} ${compact ? "is-compact" : ""}`}>
      <i aria-hidden="true" />
      {STATE_LABELS[state]}
      {telemetry && !compact && (
        <span className="state-telemetry">/ {telemetry}</span>
      )}
    </span>
  );
}

export function SectionHeading({
  index,
  title,
  meta,
  children
}: {
  index: string;
  title: string;
  meta?: string;
  children?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div className="section-heading-copy">
        <span className="section-number">{index}</span>
        <h2>{title}</h2>
        {meta && <p>{meta}</p>}
      </div>
      {children}
    </div>
  );
}

export function WindowControl({
  value,
  onChange
}: {
  value: WindowOption;
  onChange: (value: WindowOption) => void;
}) {
  return (
    <div className="window-control" aria-label="Data window">
      {WINDOWS.map((window) => (
        <button
          type="button"
          className={value === window ? "active" : ""}
          key={window}
          onClick={() => onChange(window)}
        >
          {window}
        </button>
      ))}
    </div>
  );
}

export function MetricTile({
  label,
  value,
  unit,
  note,
  icon
}: {
  label: string;
  value: string;
  unit?: string;
  note: string;
  icon: ReactNode;
}) {
  return (
    <article className="metric-tile">
      <div className="metric-tile-top">
        <span>{label}</span>
        {icon}
      </div>
      <div className="metric-value">
        {value}
        {unit && value !== "—" && <small>{unit}</small>}
      </div>
      <p>{note}</p>
    </article>
  );
}

export function DataFootnote({ children }: { children: ReactNode }) {
  return <div className="data-footnote">{children}</div>;
}
