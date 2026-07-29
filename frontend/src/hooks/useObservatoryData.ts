import { useCallback, useEffect, useState } from "react";
import { fetchExperienceSeries, fetchOverview, fetchSeries } from "../api";
import type {
  CatalogItem,
  CompareItem,
  Envelope,
  ExperienceItem,
  MetricSeries,
  ObservatoryEvent,
  OverviewItem,
  WindowOption
} from "../types";

export interface DashboardData {
  catalog: Envelope<CatalogItem[]>;
  overview: Envelope<OverviewItem[]>;
  compare: Envelope<CompareItem[]>;
  experience: Envelope<ExperienceItem[]>;
  contextExperience: Envelope<ExperienceItem[]>;
  events: Envelope<ObservatoryEvent[]>;
}

export function useExperienceSeries(
  deploymentId: string,
  dataWindow: WindowOption,
  reloadToken: number
) {
  const [shortSeries, setShortSeries] = useState<MetricSeries>(EMPTY_SERIES);
  const [contextSeries, setContextSeries] = useState<MetricSeries>(EMPTY_SERIES);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!deploymentId) return;
    const controller = new AbortController();
    let alive = true;
    const load = async () => {
      setLoading(true);
      try {
        const [shortResult, contextResult] = await Promise.all([
          fetchExperienceSeries(
            deploymentId,
            "interactive-short-v1",
            dataWindow,
            controller.signal
          ),
          fetchExperienceSeries(
            deploymentId,
            "context-16k-v1",
            dataWindow,
            controller.signal
          )
        ]);
        if (alive) {
          setShortSeries(shortResult);
          setContextSeries(contextResult);
        }
      } catch {
        if (alive && !controller.signal.aborted) {
          setShortSeries(EMPTY_SERIES);
          setContextSeries(EMPTY_SERIES);
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

  return { shortSeries, contextSeries, loading };
}

const EMPTY_SERIES: MetricSeries = {};

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

export function useDeploymentSeries(
  deploymentId: string,
  dataWindow: WindowOption,
  reloadToken: number
) {
  const [series, setSeries] = useState<MetricSeries>(EMPTY_SERIES);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!deploymentId) return;
    const controller = new AbortController();
    let alive = true;
    const load = async () => {
      setLoading(true);
      try {
        const result = await fetchSeries(deploymentId, dataWindow, controller.signal);
        if (alive) setSeries(result);
      } catch {
        if (alive && !controller.signal.aborted) setSeries(EMPTY_SERIES);
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
  }, [dataWindow, deploymentId, reloadToken]);

  return { series, loading };
}
