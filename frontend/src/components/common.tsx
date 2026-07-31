import type { ReactNode } from "react";
import type { ResponseState, WindowOption } from "../types";

const WINDOWS: WindowOption[] = ["1h", "6h", "24h", "7d", "30d"];
const STATE_LABELS: Record<ResponseState, string> = {
  current: "Current",
  collecting: "Checking",
  delayed: "Delayed",
  unavailable: "Unavailable",
  maintenance: "Maintenance"
};

export function Reveal({
  children,
  className = ""
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`reveal ${className}`}>{children}</div>
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
  compact = false
}: {
  state: ResponseState;
  compact?: boolean;
}) {
  return (
    <span className={`state-pill state-${state} ${compact ? "is-compact" : ""}`}>
      <i aria-hidden="true" />
      {STATE_LABELS[state]}
    </span>
  );
}

export function SectionHeading({
  title,
  meta,
  children
}: {
  title: string;
  meta?: string;
  children?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div className="section-heading-copy">
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
    <div className="window-control" role="group" aria-label="Data window">
      {WINDOWS.map((window) => (
        <button
          type="button"
          className={value === window ? "active" : ""}
          aria-pressed={value === window}
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
  note?: string;
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
      {note && <p>{note}</p>}
    </article>
  );
}
