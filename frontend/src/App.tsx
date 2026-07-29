import {
  ArrowClockwise,
  Database,
  Pulse,
  WarningCircle
} from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { BrandMark } from "./components/common";
import { EventsSection } from "./components/EventsSection";
import { FleetOverview } from "./components/FleetOverview";
import { ModelDetail } from "./components/ModelDetail";
import { SpeedComparison } from "./components/SpeedComparison";
import {
  useDeploymentSeries,
  useExperienceSeries,
  useObservatoryData
} from "./hooks/useObservatoryData";
import { formatAge } from "./lib/format";
import type { WindowOption } from "./types";

function App() {
  const [dataWindow, setDataWindow] = useState<WindowOption>("1h");
  const [selectedId, setSelectedId] = useState("");
  const { data, loading, error, refresh, reloadToken } =
    useObservatoryData(dataWindow);
  const experienceModels = data?.experience.data ?? [];
  const telemetryModels = data?.overview.data ?? [];

  useEffect(() => {
    if (experienceModels.length === 0) return;
    if (!experienceModels.some((model) => model.deployment_id === selectedId)) {
      setSelectedId(experienceModels[0].deployment_id);
    }
  }, [experienceModels, selectedId]);

  const selected =
    experienceModels.find((model) => model.deployment_id === selectedId) ??
    experienceModels[0];
  const selectedTelemetry = telemetryModels.find(
    (model) => model.deployment_id === selectedId
  );
  const selectedContext = data?.contextExperience.data.find(
    (model) => model.deployment_id === selectedId
  );
  const { series, loading: seriesLoading } = useDeploymentSeries(
    selected?.deployment_id ?? "",
    dataWindow,
    reloadToken
  );
  const {
    shortSeries,
    contextSeries,
    loading: experienceSeriesLoading
  } = useExperienceSeries(
    selected?.deployment_id ?? "",
    dataWindow,
    reloadToken
  );
  const operationalCount = telemetryModels.filter(
    (model) => model.service_state === "operational"
  ).length;
  const attentionCount = telemetryModels.filter((model) =>
    ["slow", "degraded", "unavailable"].includes(model.service_state)
  ).length;
  const freshExperienceCount = experienceModels.filter(
    (model) => model.experience_state === "experience_fresh"
  ).length;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a href="#overview" className="brand" aria-label="MaaS Observatory home">
          <BrandMark />
          <span>MaaS Observatory</span>
        </a>
        <nav aria-label="Primary navigation">
          <a href="#fleet">Fleet</a>
          <a href="#models">Model detail</a>
          <a href="#comparison">Experience</a>
          <a href="#events">Events</a>
        </nav>
        <div className="topbar-meta">
          <span className={`live-signal ${error ? "is-error" : ""}`}>
            <i aria-hidden="true" />
            {error ? "DISCONNECTED" : "LIVE"}
          </span>
          <span className="updated">
            Updated {formatAge(data?.overview.freshness_seconds ?? null)}
          </span>
          <button
            type="button"
            className={`icon-button ${loading ? "is-loading" : ""}`}
            onClick={refresh}
            aria-label="Refresh data"
          >
            <ArrowClockwise size={17} />
          </button>
        </div>
      </header>

      <main>
        <section className="masthead" id="overview">
          <div className="masthead-grid" aria-hidden="true" />
          <div className="masthead-title">
            <span className="masthead-label">ACADEMY MODEL SERVING</span>
            <h1>
              MaaS <span>Observatory</span>
            </h1>
          </div>
          <div className="status-rail">
            <div>
              <span>Deployments</span>
              <strong>{experienceModels.length || "—"}</strong>
            </div>
            <div>
              <span>Operational</span>
              <strong>{telemetryModels.length ? operationalCount : "—"}</strong>
            </div>
            <div>
              <span>Need attention</span>
              <strong className={attentionCount > 0 ? "attention" : ""}>
                {telemetryModels.length ? attentionCount : "—"}
              </strong>
            </div>
            <div>
              <span>Fresh experience</span>
              <strong>
                {experienceModels.length
                  ? `${freshExperienceCount}/${experienceModels.length}`
                  : "—"}
              </strong>
            </div>
          </div>
          {error && (
            <div className="connection-banner" role="status">
              <WarningCircle size={18} />
              <span>API connection unavailable</span>
              <code>{error}</code>
            </div>
          )}
        </section>

        <FleetOverview models={experienceModels} />

        {selected ? (
          <ModelDetail
            models={experienceModels}
            selected={selected}
            telemetry={selectedTelemetry}
            context={selectedContext}
            selectedId={selectedId}
            onSelectedIdChange={setSelectedId}
            dataWindow={dataWindow}
            onDataWindowChange={setDataWindow}
            experienceSeries={shortSeries}
            contextSeries={contextSeries}
            telemetrySeries={series}
            loading={seriesLoading || experienceSeriesLoading}
          />
        ) : (
          <section className="page-section loading-section" id="models">
            <div className="loading-block" />
            <div className="loading-block" />
          </section>
        )}

        <SpeedComparison items={data?.compare.data ?? []} />
        <EventsSection
          events={data?.events.data ?? []}
          models={telemetryModels}
        />
      </main>

      <footer>
        <div className="footer-brand">
          <BrandMark />
          <strong>MaaS Observatory</strong>
        </div>
        <div className="footer-meta">
          <span><Database size={14} /> SQLite WAL</span>
          <span><Pulse size={14} /> Observer path + telemetry</span>
          <span>Schema v{data?.overview.schema_version ?? "2"}</span>
        </div>
        <p>
          Operational measurements retain source, window, sample count, quality
          and profile metadata.
        </p>
      </footer>
    </div>
  );
}

export default App;
