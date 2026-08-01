import { describe, expect, it } from "vitest";
import type { CompareItem, ContextTier, TierCompareData } from "../types";
import { CONTEXT_TIERS, TIER_LABELS } from "../types";
import {
  comparisonStatusCounts,
  getRankedItems,
  getSharedMetricMaximum
} from "./comparison";
import {
  compareMetricMeta,
  extractCompareValue,
  extractOverviewCompareValue,
  fleetModelSummary,
  isTierComplete,
  sharedYDomain,
  tierStatusLabel
} from "./format";

function makeTierCompareData(metrics: {
  first_token_p50?: number | null;
  output_speed_p50?: number | null;
  total_response_p50?: number | null;
}, complete = true): TierCompareData {
  const hasAll = metrics.first_token_p50 != null && metrics.output_speed_p50 != null && metrics.total_response_p50 != null;
  return {
    first_token_p50: metrics.first_token_p50 ?? null,
    output_speed_p50: metrics.output_speed_p50 ?? null,
    total_response_p50: metrics.total_response_p50 ?? null,
    quality: hasAll ? "exact" : "unavailable",
    sample_count: hasAll ? 6 : 0,
    fixture_count: hasAll ? 2 : 0,
    complete_fixture_set: complete && hasAll,
    measured_at: hasAll ? "2026-07-29T00:00:00Z" : null,
    latest_attempt_outcome: hasAll ? "success" : "skipped",
    latest_attempt_reason: hasAll ? null : "first_check_scheduled",
    latest_attempt_at: "2026-07-29T00:00:00Z",
    reason: null
  };
}

function mkItem(id: string, tiers: Record<ContextTier, TierCompareData>): CompareItem {
  return {
    deployment_id: id,
    alias: id,
    tiers,
    source_kind: "streaming_request",
    observation_scope: "observatory_vantage",
    profile_id: "response-v5",
    response_state: "current",
    definition_version: "5",
    suite_version: "response-suite-v5",
    vantage_id: "observatory-primary",
    collection_mode: "rapid"
  };
}

describe("tier helpers", () => {
  it("tier labels are canonical", () => {
    expect(TIER_LABELS["1k"]).toBe("1K");
    expect(TIER_LABELS["16k"]).toBe("16K");
    expect(TIER_LABELS["64k"]).toBe("64K");
    expect(CONTEXT_TIERS).toEqual(["1k", "16k", "64k"]);
  });

  it("extractCompareValue resolves from TierCompareData fields", () => {
    const td = makeTierCompareData({
      first_token_p50: 0.5,
      output_speed_p50: 42,
      total_response_p50: 3.2
    });
    expect(extractCompareValue(td, "firstToken")).toBe(0.5);
    expect(extractCompareValue(td, "outputSpeed")).toBe(42);
    expect(extractCompareValue(td, "totalResponse")).toBe(3.2);
  });

  it("formats compare metadata without repeating latency units", () => {
    expect(compareMetricMeta("firstToken", 6)).toBe("n=6");
    expect(compareMetricMeta("totalResponse", 6)).toBe("n=6");
    expect(compareMetricMeta("outputSpeed", 6)).toBe("tok/s · n=6");
  });

  it("extractOverviewCompareValue resolves from TierExperience fields", () => {
    const tier = {
      first_token_p50: 0.5,
      output_speed_p50: 42,
      total_response_p50: 3.2
    };
    expect(extractOverviewCompareValue(tier as any, "firstToken")).toBe(0.5);
    expect(extractOverviewCompareValue(tier as any, "outputSpeed")).toBe(42);
    expect(extractOverviewCompareValue(tier as any, "totalResponse")).toBe(3.2);
  });

  it("isTierComplete checks fixture set and state", () => {
    expect(isTierComplete({ complete_fixture_set: true, response_state: "current" } as any)).toBe(true);
    expect(isTierComplete({ complete_fixture_set: false, response_state: "current" } as any)).toBe(false);
    expect(isTierComplete({ complete_fixture_set: true, response_state: "collecting" } as any)).toBe(false);
  });

  it("sharedYDomain computes unified range", () => {
    const series = {
      deployment_id: "test",
      tiers: {
        "1k": { testMetric: [{ value: 1, quality: "exact" as const }, { value: 5, quality: "exact" as const }] },
        "16k": { testMetric: [{ value: 3, quality: "exact" as const }, { value: 8, quality: "exact" as const }] },
        "64k": { testMetric: [{ value: 0, quality: "exact" as const }] }
      }
    } as any;
    const [min, max] = sharedYDomain(series, "testMetric");
    expect(min).toBeLessThanOrEqual(0);
    expect(max).toBeGreaterThanOrEqual(8);
    expect(max).toBeGreaterThan(8);
  });

  it("sharedYDomain handles empty input", () => {
    const series = { deployment_id: "test", tiers: { "1k": {}, "16k": {}, "64k": {} } } as any;
    const [min, max] = sharedYDomain(series, "nothing");
    expect(min).toBe(0);
    expect(max).toBe(1);
  });

  it("tierStatusLabel maps all states", () => {
    expect(tierStatusLabel({ response_state: "current", complete_fixture_set: true } as any)).toBe("Complete");
    expect(tierStatusLabel({ response_state: "collecting", complete_fixture_set: false } as any)).toBe("Collecting");
    expect(tierStatusLabel({ response_state: "unavailable", complete_fixture_set: false } as any)).toBe("Unavailable");
  });

  it("fleetModelSummary handles all cases", () => {
    const tiersAllUnavailable = {
      "1k": { response_state: "unavailable" as const, complete_fixture_set: false },
      "16k": { response_state: "unavailable" as const, complete_fixture_set: false },
      "64k": { response_state: "unavailable" as const, complete_fixture_set: false }
    };
    expect(fleetModelSummary(tiersAllUnavailable as any)).toEqual({
      isUnavailable: true,
      isComplete: false,
      summaryState: "unavailable"
    });

    const tiersOneComplete = {
      "1k": { response_state: "current" as const, complete_fixture_set: true },
      "16k": { response_state: "collecting" as const, complete_fixture_set: false },
      "64k": { response_state: "collecting" as const, complete_fixture_set: false }
    };
    expect(fleetModelSummary(tiersOneComplete as any)).toEqual({
      isUnavailable: false,
      isComplete: true,
      summaryState: "current"
    });

    const tiersAllCollecting = {
      "1k": { response_state: "collecting" as const, complete_fixture_set: false },
      "16k": { response_state: "collecting" as const, complete_fixture_set: false },
      "64k": { response_state: "collecting" as const, complete_fixture_set: false }
    };
    expect(fleetModelSummary(tiersAllCollecting as any)).toEqual({
      isUnavailable: false,
      isComplete: false,
      summaryState: "collecting"
    });

    const tiersAllDelayed = {
      "1k": { response_state: "delayed" as const, complete_fixture_set: true },
      "16k": { response_state: "delayed" as const, complete_fixture_set: true },
      "64k": { response_state: "delayed" as const, complete_fixture_set: true }
    };
    expect(fleetModelSummary(tiersAllDelayed as any)).toEqual({
      isUnavailable: false,
      isComplete: false,
      summaryState: "delayed"
    });
  });
});

describe("tiered comparison", () => {
  it("comparisonStatusCounts groups pending by reason", () => {
    const items = [
      mkItem("valid", {
        "1k": makeTierCompareData({ first_token_p50: 12, output_speed_p50: 50, total_response_p50: 5 }),
        "16k": makeTierCompareData({}, false),
        "64k": makeTierCompareData({}, false)
      }),
      mkItem("failed", {
        "1k": makeTierCompareData({}, false),
        "16k": makeTierCompareData({}, false),
        "64k": makeTierCompareData({}, false)
      })
    ];
    items[1].tiers["1k"].latest_attempt_reason = "request_failed";

    const pending = comparisonStatusCounts([items[1]], "1k", "firstToken");
    expect(pending[0]).toEqual(["request_failed", 1]);

    items[1].tiers["1k"].latest_attempt_reason = "measurement_limited";
    expect(comparisonStatusCounts([items[1]], "1k", "firstToken")[0]).toEqual([
      "measurement_limited",
      1
    ]);
  });

  it("uses one metric scale across all three tiers", () => {
    const items = [
      mkItem("a", {
        "1k": makeTierCompareData({ first_token_p50: 1, output_speed_p50: 25, total_response_p50: 4 }),
        "16k": makeTierCompareData({ first_token_p50: 4, output_speed_p50: 50, total_response_p50: 8 }),
        "64k": makeTierCompareData({ first_token_p50: 12, output_speed_p50: 100, total_response_p50: 20 })
      })
    ];

    expect(getSharedMetricMaximum(items, "firstToken")).toBe(12);
    expect(getSharedMetricMaximum(items, "outputSpeed")).toBe(100);
    expect(getSharedMetricMaximum(items, "totalResponse")).toBe(20);
  });

  it("getRankedItems sorts by metric direction", () => {
    const items = [
      mkItem("a", { "1k": makeTierCompareData({ first_token_p50: 0.3, output_speed_p50: 100, total_response_p50: 4 }), "16k": makeTierCompareData({}, false), "64k": makeTierCompareData({}, false) }),
      mkItem("b", { "1k": makeTierCompareData({ first_token_p50: 0.1, output_speed_p50: 200, total_response_p50: 6 }), "16k": makeTierCompareData({}, false), "64k": makeTierCompareData({}, false) }),
      mkItem("c", { "1k": makeTierCompareData({ first_token_p50: 0.5, output_speed_p50: 50, total_response_p50: 3 }), "16k": makeTierCompareData({}, false), "64k": makeTierCompareData({}, false) })
    ];

    // For outputSpeed, higher is better: b(200) > a(100) > c(50)
    const speedRanked = getRankedItems(items, "1k", "outputSpeed");
    expect(speedRanked.map(i => i.deployment_id)).toEqual(["b", "a", "c"]);

    // For firstToken, lower is better: b(0.1) < a(0.3) < c(0.5)
    const latencyRanked = getRankedItems(items, "1k", "firstToken");
    expect(latencyRanked.map(i => i.deployment_id)).toEqual(["b", "a", "c"]);
  });

  it("getRankedItems excludes non-rankable items", () => {
    const items = [
      mkItem("a", { "1k": makeTierCompareData({ first_token_p50: 0.3, output_speed_p50: 100, total_response_p50: 4 }), "16k": makeTierCompareData({}, false), "64k": makeTierCompareData({}, false) }),
      mkItem("b", { "1k": makeTierCompareData({ first_token_p50: null, output_speed_p50: null, total_response_p50: null }, false), "16k": makeTierCompareData({}, false), "64k": makeTierCompareData({}, false) }),
      mkItem("c", { "1k": makeTierCompareData({ first_token_p50: 0.5, output_speed_p50: 50, total_response_p50: 3 }), "16k": makeTierCompareData({}, false), "64k": makeTierCompareData({}, false) })
    ];

    const ranked = getRankedItems(items, "1k", "firstToken");
    expect(ranked.length).toBe(2);
    expect(ranked.map(i => i.deployment_id)).toEqual(["a", "c"]);
  });
});
