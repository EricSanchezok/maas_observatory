import { useCallback, useEffect, useState } from "react";
import { fetchExperienceSeries, fetchOverview } from "../api";
import type {
  CatalogItem,
  CompareItem,
  Envelope,
  ExperienceItem,
  MetricSeries,
  ObservatoryEvent,
  WindowOption
} from "../types";

export interface DashboardData {
  catalog: Envelope<CatalogItem[]>;
  compare: Envelope<CompareItem[]>;
  experience: Envelope<ExperienceItem[]>;
  events: Envelope<ObservatoryEvent[]>;
}

const EMPTY_SERIES: MetricSeries = {};

export function useExperienceSeries(
  deploymentId: string,
  dataWindow: WindowOption,
  reloadToken: number
) {
  const [responseSeries, setResponseSeries] =
    useState<MetricSeries>(EMPTY_SERIES);
  const [loading, setLoading] = useState(false);

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
        if (alive) {
          setResponseSeries(result);
        }
      } catch {
        if (alive && !controller.signal.aborted) {
          setResponseSeries(EMPTY_SERIES);
        }
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

  return { responseSeries, loading };
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
        const result = await fetchOverview(dataWindow, controller.signal);
        if (!alive) return;
        setData(result);
        setError(null);
      } catch (loadError) {
        if (!alive || controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : "Request failed");
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
