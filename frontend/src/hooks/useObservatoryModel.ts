import { useCallback, useEffect, useState } from "react";
import type { ExperienceItem } from "../types";

export function useObservatoryModel(models: ExperienceItem[]) {
  const [selectedId, setSelectedId] = useState("");
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  useEffect(() => {
    if (models.length === 0) return;
    if (!models.some((model) => model.deployment_id === selectedId)) {
      setSelectedId(models[0].deployment_id);
    }
  }, [models, selectedId]);

  const selected =
    models.find((model) => model.deployment_id === selectedId) ?? models[0];

  const isSelected = useCallback(
    (id: string) => id === selectedId,
    [selectedId]
  );

  const isHovered = useCallback(
    (id: string) => id === hoveredId,
    [hoveredId]
  );

  return {
    selectedId,
    selected,
    hoveredId,
    setSelectedId,
    setHoveredId,
    isSelected,
    isHovered
  };
}
