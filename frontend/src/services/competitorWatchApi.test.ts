import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(new URL("./competitorWatchApi.ts", import.meta.url), "utf8");

test("deleteCompetitorGroup calls the watch-group DELETE endpoint", () => {
  assert.match(apiSource, /export async function deleteCompetitorGroup/);
  assert.match(
    apiSource,
    /\/api\/v1\/ctrip-ebooking\/competitor-watch-groups\/\$\{groupKey\}/,
  );
  assert.match(apiSource, /method:\s*"DELETE"/);
  assert.match(apiSource, /watch_group:\s*CompetitorWatchGroup/);
});

test("getCompetitorPriceTrend only requests the latest one day for readable charts", () => {
  assert.match(apiSource, /export async function getCompetitorPriceTrend/);
  assert.match(apiSource, /price-trend\?days=\$\{days\}&granularity=\$\{granularity\}/);
  assert.match(apiSource, /days\?: number/);
  assert.match(apiSource, /granularity\?: "run" \| "hour" \| "day"/);
});
