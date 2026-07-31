import {
  ArrowClockwise,
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
  const availability = data?.availability.data ?? [];

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
              <span>Checked</span>
              <strong>{models.length ? `${checkedCount}/${models.length}` : "—"}</strong>
            </div>
            <div>
              <span>Available</span>
              <strong>{models.length ? `${currentCount}/${models.length}` : "—"}</strong>
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

        <FleetOverview
          models={models}
          dataWindow={dataWindow}
          availability={availability}
        />

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
        <p>Response status and speed from scheduled checks.</p>
      </footer>
    </div>
  );
}

export default App;
