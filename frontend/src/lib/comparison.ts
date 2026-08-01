import { CONTEXT_TIERS } from "../types";
import type { CompareItem, ContextTier } from "../types";
import type { CompareMetric } from "./format";
import {
  compareDirection,
  extractCompareValue,
  isCompareRankable
} from "./format";

export function comparisonStatusCounts(
  items: CompareItem[],
  tier: ContextTier,
  metric: CompareMetric
): Array<[string, number]> {
  const metricKey = (
    metric === "firstToken" ? "first_token_p50"
    : metric === "outputSpeed" ? "output_speed_p50"
    : "total_response_p50"
  ) as "first_token_p50" | "output_speed_p50" | "total_response_p50";
  const counts = new Map<string, number>();
  for (const item of items) {
    const td = item.tiers[tier];
    if (isCompareRankable(td, metricKey)) continue;
    const reason = td.latest_attempt_reason ?? "first_check_scheduled";
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return [...counts.entries()].sort(
    (left, right) => right[1] - left[1] || left[0].localeCompare(right[0])
  );
}

export function getSharedMetricMaximum(
  items: CompareItem[],
  metric: CompareMetric
): number {
  const values = CONTEXT_TIERS.flatMap((tier) =>
    items
      .map((item) => extractCompareValue(item.tiers[tier], metric))
      .filter((value): value is number => value !== null)
  );
  return Math.max(1, ...values);
}

export function getRankedItems(
  items: CompareItem[],
  tier: ContextTier,
  metric: CompareMetric
): CompareItem[] {
  const metricKey = (
    metric === "firstToken" ? "first_token_p50"
    : metric === "outputSpeed" ? "output_speed_p50"
    : "total_response_p50"
  ) as "first_token_p50" | "output_speed_p50" | "total_response_p50";

  const direction = compareDirection(metric);

  return [...items]
    .filter((item) => isCompareRankable(item.tiers[tier], metricKey))
    .sort((a, b) => {
      const aVal = a.tiers[tier][metricKey] ?? 0;
      const bVal = b.tiers[tier][metricKey] ?? 0;
      return direction === "desc" ? bVal - aVal : aVal - bVal;
    });
}
