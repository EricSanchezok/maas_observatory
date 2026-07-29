import { describe, expect, it } from "vitest";
import type { CompareItem } from "../types";
import { comparisonMode, comparisonStatusCounts } from "./comparison";

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
    source_kind: "streaming_request",
    observation_scope: "observatory_vantage",
    quality: value === null ? "unavailable" : "exact",
    sample_count: value === null ? 0 : 3,
    fixture_count: value === null ? 0 : 3,
    complete_fixture_set: value !== null,
    profile_id: "interactive-short-v2",
    definition_version: "2",
    suite_version: "response-suite-v2",
    vantage_id: "observatory-primary",
    collection_mode: "rapid",
    measured_at: value === null ? null : "2026-07-29T00:00:00Z",
    latest_attempt_outcome: value === null ? "skipped" : "success",
    latest_attempt_reason: reason,
    latest_attempt_at: "2026-07-29T00:00:00Z",
    reason: value === null ? "waiting_for_complete_fixture_set" : null
  };
}

describe("response comparison adaptation", () => {
  it("adapts at zero, one, and two complete fixture sets", () => {
    const pending = item("pending", null, "first_check_scheduled");
    const first = item("first", 12.5);
    const second = item("second", 18.5);

    expect(comparisonMode([pending])).toBe("empty");
    expect(comparisonMode([pending, first])).toBe("single");
    expect(comparisonMode([pending, first, second])).toBe("ranked");
  });

  it("counts only pending models", () => {
    expect(
      comparisonStatusCounts([
        item("valid", 12.5),
        item("failed-1", null, "request_failed"),
        item("failed-2", null, "request_failed"),
        item("new", null)
      ])
    ).toEqual([
      ["request_failed", 2],
      ["first_check_scheduled", 1]
    ]);
  });
});
