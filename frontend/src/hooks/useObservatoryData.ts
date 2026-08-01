import { useCallback, useEffect, useRef, useState } from "react";
import { fetchAvailability, fetchExperienceSeries, fetchOverview } from "../api";
import type {
  AvailabilityItem,
  CatalogItem,
  CompareItem,
  Envelope,
  ExperienceItem,
  ObservatoryEvent,
  TieredSeries,
  WindowOption
} from "../types";

export interface DashboardData {
  catalog: Envelope<CatalogItem[]>;
  compare: Envelope<CompareItem[]>;
  experience: Envelope<ExperienceItem[]>;
  events: Envelope<ObservatoryEvent[]>;
  availability: Envelope<AvailabilityItem[]>;
}

export function useExperienceSeries(
  deploymentId: string,
  dataWindow: WindowOption,
  reloadToken: number
) {
  const [tieredSeries, setTieredSeries] = useState<TieredSeries | null>(null);
  const [loading, setLoading] = useState(false);
  const seriesForId = useRef<string | null>(null);

  useEffect(() => {
    if (!deploymentId) return;
    const controller = new AbortController();
    let alive = true;
    const load = async () => {
      setLoading(true);
      try {
        const result = await fetchExperienceSeries(
          deploymentId,
          dataWindow,
          controller.signal
        );
        if (!alive) return;
        setTieredSeries((prev) =>
          result.deployment_id === deploymentId ? result : prev
        );
        seriesForId.current = result.deployment_id;
      } catch {
        if (!alive || controller.signal.aborted) return;
        // Preserve prior data; never clear to null on transient failure
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    return () => {
      alive = false;
      controller.abort();
    };
  }, [dataWindow, deploymentId, reloadToken]);

  return { tieredSeries, loading };
}

function availabilityDays(dataWindow: WindowOption): 7 | 30 {
  return dataWindow === "7d" ? 7 : 30;
}

export function useObservatoryData(dataWindow: WindowOption) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const refresh = useCallback(() => setReloadToken((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    const load = async () => {
      try {
        const [overview, availability] = await Promise.all([
          fetchOverview(dataWindow, controller.signal),
          fetchAvailability(availabilityDays(dataWindow), controller.signal)
        ]);
        if (!alive) return;
        setData({ ...overview, availability });
        setError(null);
      } catch (loadError) {
        if (!alive || controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : "Request failed");
        // Preserve prior data on transient failure
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(load, 15_000);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [dataWindow, reloadToken]);

  return { data, loading, error, refresh, reloadToken };
}
