import { describe, expect, it } from "vitest";
import type { CompareItem } from "../types";
import {
  comparisonMode,
  comparisonStatusCounts
} from "./comparison";

function item(
  deploymentId: string,
  value: number | null,
  reason: CompareItem["latest_attempt_reason"] = null
): CompareItem {
  return {
    deployment_id: deploymentId,
    alias: deploymentId,
    value,
    unit: "tokens/s",
    source_kind: "experience_probe",
    observation_scope: "observer_path",
    quality: value === null ? "unavailable" : "exact",
    sample_count: value === null ? 0 : 1,
    profile_id: value === null ? null : "operational",
    definition_version: "1",
    vantage_id: "observatory-primary",
    measured_at: value === null ? null : "2026-07-29T00:00:00Z",
    latest_attempt_outcome: value === null ? "skipped" : "success",
    latest_attempt_reason: reason,
    latest_attempt_at: "2026-07-29T00:00:00Z",
    reason: value === null ? "no_valid_microprobe" : null
  };
}

describe("speed comparison adaptation", () => {
  it("adapts at zero, one, and two valid samples", () => {
    const pending = item("pending", null, "busy");
    const first = item("first", 12.5);
    const second = item("second", 18.5);

    expect(comparisonMode([pending])).toBe("empty");
    expect(comparisonMode([pending, first])).toBe("single");
    expect(comparisonMode([pending, first, second])).toBe("ranked");
  });

  it("excludes valid samples from deterministic pending status counts", () => {
    expect(
      comparisonStatusCounts([
        item("valid", 12.5),
        item("busy-1", null, "busy"),
        item("busy-2", null, "busy"),
        item("new", null)
      ])
    ).toEqual([
      ["busy", 2],
      ["awaiting_turn", 1]
    ]);
  });
});
