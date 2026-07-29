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
  const models = data?.experience.data ?? [];

  useEffect(() => {
    if (models.length === 0) return;
    if (!models.some((model) => model.deployment_id === selectedId)) {
      setSelectedId(models[0].deployment_id);
    }
  }, [models, selectedId]);

  const selected =
    models.find((model) => model.deployment_id === selectedId) ?? models[0];
  const {
    responseSeries,
    loading: seriesLoading
  } = useExperienceSeries(
    selected?.deployment_id ?? "",
    dataWindow,
    reloadToken
  );
  const currentCount = models.filter(
    (model) => model.response_state === "current"
  ).length;
  const checkedCount = models.filter(
    (model) => model.latest_attempt_at !== null
  ).length;
  const collectionMode = models[0]?.collection_mode ?? "standard";
  const freshness = data?.experience.freshness_seconds ?? null;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a href="#overview" className="brand" aria-label="MaaS Observatory home">
          <BrandMark />
          <span>MaaS Observatory</span>
        </a>
        <nav aria-label="Primary navigation">
          <a href="#fleet">Overview</a>
          <a href="#models">Model</a>
          <a href="#comparison">Compare</a>
          <a href="#events">Activity</a>
        </nav>
        <div className="topbar-meta">
          <span className={`live-signal ${error ? "is-error" : ""}`}>
            <i aria-hidden="true" />
            {error ? "DISCONNECTED" : "LIVE"}
          </span>
          <span className="updated">Updated {formatAge(freshness)}</span>
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
          <div className="masthead-title">
            <span className="masthead-label">ACADEMY MODEL RESPONSE</span>
            <h1>
              MaaS <span>Observatory</span>
            </h1>
          </div>
          <div className="status-rail">
            <div>
              <span>Models</span>
              <strong>{models.length || "—"}</strong>
            </div>
            <div>
              <span>Checked recently</span>
              <strong>{models.length ? `${checkedCount}/${models.length}` : "—"}</strong>
            </div>
            <div>
              <span>Available</span>
              <strong>{models.length ? `${currentCount}/${models.length}` : "—"}</strong>
            </div>
            <div>
              <span>Collection mode</span>
              <strong className={collectionMode === "rapid" ? "attention" : ""}>
                {collectionMode === "rapid" ? "Rapid" : "Standard"}
              </strong>
            </div>
          </div>
          {collectionMode === "rapid" && (
            <div className="rapid-note" role="status">
              Rapid collection is active. Switch back to Standard manually after
              this session.
            </div>
          )}
          {error && (
            <div className="connection-banner" role="status">
              <WarningCircle size={18} />
              <span>API connection unavailable</span>
              <code>{error}</code>
            </div>
          )}
        </section>

        <FleetOverview models={models} />

        {selected ? (
          <ModelDetail
            models={models}
            selected={selected}
            selectedId={selectedId}
            onSelectedIdChange={setSelectedId}
            dataWindow={dataWindow}
            onDataWindowChange={setDataWindow}
            responseSeries={responseSeries}
            loading={seriesLoading}
          />
        ) : (
          <section className="page-section loading-section" id="models">
            <div className="loading-block" />
            <div className="loading-block" />
          </section>
        )}

        <SpeedComparison items={data?.compare.data ?? []} />
        <EventsSection events={data?.events.data ?? []} />
      </main>

      <footer>
        <div className="footer-brand">
          <BrandMark />
          <strong>MaaS Observatory</strong>
        </div>
        <div className="footer-meta">
          <span><Database size={14} /> SQLite WAL</span>
          <span><Pulse size={14} /> Scheduled streaming checks</span>
          <span>API schema v{data?.experience.schema_version ?? "4"}</span>
        </div>
        <p>Response measurements from one documented server location.</p>
      </footer>
    </div>
  );
}

export default App;
