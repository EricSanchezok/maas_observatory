import type { CompareItem } from "../types";

export type ComparisonMode = "empty" | "single" | "ranked";

export function comparisonMode(items: CompareItem[]): ComparisonMode {
  const validCount = items.filter((item) => item.value !== null).length;
  if (validCount === 0) return "empty";
  if (validCount === 1) return "single";
  return "ranked";
}

export function comparisonStatusCounts(
  items: CompareItem[]
): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const item of items) {
    if (item.value !== null) continue;
    const reason = item.latest_attempt_reason ?? "first_check_scheduled";
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return [...counts.entries()].sort(
    (left, right) => right[1] - left[1] || left[0].localeCompare(right[0])
  );
}
