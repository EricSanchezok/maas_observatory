import {
  ArrowClockwise,
  WarningCircle
} from "@phosphor-icons/react";
import { BrandMark, WindowControl } from "./components/common";
import { EventsSection } from "./components/EventsSection";
import { FleetOverview } from "./components/FleetOverview";
import { ModelDetail } from "./components/ModelDetail";
import { SpeedComparison } from "./components/SpeedComparison";
import { useObservatoryModel } from "./hooks/useObservatoryModel";
import {
  useExperienceSeries,
  useObservatoryData
} from "./hooks/useObservatoryData";
import { formatAge } from "./lib/format";
import type { WindowOption } from "./types";
import { useState } from "react";

function App() {
  const [dataWindow, setDataWindow] = useState<WindowOption>("1h");
  const { data, loading, error, refresh, reloadToken } =
    useObservatoryData(dataWindow);
  const models = data?.experience.data ?? [];
  const availability = data?.availability.data ?? [];

  const {
    selectedId,
    selected,
    hoveredId,
    setSelectedId,
    setHoveredId
  } = useObservatoryModel(models);

  const { tieredSeries, loading: seriesLoading } = useExperienceSeries(
    selected?.deployment_id ?? "",
    dataWindow,
    reloadToken
  );

  const currentCount = models.filter((model) =>
    Object.values(model.tiers).some(
      (t) => t.response_state === "current" && t.complete_fixture_set
    )
  ).length;

  const checkedCount = models.filter((model) =>
    Object.values(model.tiers).some(
      (t) => t.latest_attempt_at !== null
    )
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
          <div className="masthead-top">
            <div className="masthead-title">
              <h1>
                MaaS <span>Observatory</span>
              </h1>
            </div>
            <div className="masthead-window">
              <span className="masthead-window-label">DATA WINDOW</span>
              <WindowControl value={dataWindow} onChange={setDataWindow} />
            </div>
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
          selectedId={selectedId}
          hoveredId={hoveredId}
          onSelect={setSelectedId}
          onHover={setHoveredId}
        />

        {selected ? (
          <ModelDetail
            models={models}
            selected={selected}
            selectedId={selectedId}
            onSelectedIdChange={setSelectedId}
            dataWindow={dataWindow}
            tieredSeries={tieredSeries}
            loading={seriesLoading}
          />
        ) : (
          <section className="page-section loading-section" id="models">
            <div className="loading-block" />
            <div className="loading-block" />
          </section>
        )}

        <SpeedComparison
          items={data?.compare.data ?? []}
          selectedId={selectedId}
          hoveredId={hoveredId}
          onSelect={setSelectedId}
          onHover={setHoveredId}
        />
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
