import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const dataCenterApiSource = readFileSync(
  new URL("../services/ctripEbookingWatchApi.ts", import.meta.url),
  "utf8",
);
const competitorApiSource = readFileSync(
  new URL("../services/competitorWatchApi.ts", import.meta.url),
  "utf8",
);
const priceAlertApiSource = readFileSync(
  new URL("../services/priceChangeAlertsApi.ts", import.meta.url),
  "utf8",
);
const calendarApiSource = readFileSync(
  new URL("../services/calendarPriceWatchApi.ts", import.meta.url),
  "utf8",
);

test("group-scoped read services accept and forward AbortSignal", () => {
  assert.match(dataCenterApiSource, /listDataCenterDatasets\([\s\S]*?signal\?: AbortSignal/);
  assert.match(dataCenterApiSource, /getLatestDataCenterRun\([\s\S]*?signal\?: AbortSignal/);
  assert.match(dataCenterApiSource, /datasets`,\s*\{ signal \}/);
  assert.match(dataCenterApiSource, /latest`,\s*\{ signal \}/);

  assert.match(competitorApiSource, /listCompetitorHotels\([\s\S]*?signal\?: AbortSignal/);
  assert.match(competitorApiSource, /getLatestCompetitorSnapshot\([\s\S]*?signal\?: AbortSignal/);
  assert.match(competitorApiSource, /getCompetitorPriceTrend\([\s\S]*?signal\?: AbortSignal/);
  assert.equal(competitorApiSource.match(/\{ signal \}/g)?.length, 3);

  assert.match(priceAlertApiSource, /getLatestPriceChangeAlert\([\s\S]*?signal\?: AbortSignal/);
  assert.match(priceAlertApiSource, /watch_group_key=\$\{groupKey\}`,\s*\{ signal \}/);

  assert.match(calendarApiSource, /getCalendarPriceConfig\([\s\S]*?signal\?: AbortSignal/);
  assert.match(calendarApiSource, /getLatestCalendarPriceMatrix\([\s\S]*?signal\?: AbortSignal/);
  assert.match(calendarApiSource, /listCalendarPriceAlerts\([\s\S]*?signal\?: AbortSignal/);
  assert.match(calendarApiSource, /getCalendarSchedulerStatus\([\s\S]*?signal\?: AbortSignal/);
  assert.match(calendarApiSource, /listCalendarConfigSummaries\([\s\S]*?signal\?: AbortSignal/);
  assert.equal(calendarApiSource.match(/\{ signal \}/g)?.length, 5);
});

test("each group refresh chain aborts its previous request and forwards the new signal", () => {
  for (const refName of [
    "dataCenterRequestRef",
    "competitorRequestRef",
    "calendarStripRequestRef",
    "calendarGroupRequestRef",
  ]) {
    assert.match(appSource, new RegExp("const " + refName + " = useRef<AbortController \\| null>\\(null\\)"));
    assert.match(appSource, new RegExp(refName + "\\.current\\?\\.abort\\(\\)"));
    assert.match(appSource, new RegExp(refName + "\\.current = controller"));
  }

  assert.match(appSource, /listDataCenterDatasets\(groupKey, controller\.signal\)/);
  assert.match(appSource, /listCompetitorHotels\(groupKey, \{ includeDisabled, signal: controller\.signal \}\)/);
  assert.match(appSource, /getCalendarPriceConfig\(groupKey, controller\.signal\)/);
  assert.match(appSource, /getLatestCalendarPriceMatrix\(groupKey, controller\.signal\)/);
});

test("aborted group refreshes are silent and all controllers abort on unmount", () => {
  assert.match(appSource, /function isAbortError\(error: unknown\): boolean/);
  assert.match(appSource, /if \(isAbortError\(error\)\) \{\s*return;\s*\}/);
  assert.match(
    appSource,
    /useEffect\(\(\) => \{\s*return \(\) => \{[\s\S]*?dataCenterRequestRef\.current\?\.abort\(\)[\s\S]*?competitorRequestRef\.current\?\.abort\(\)[\s\S]*?calendarStripRequestRef\.current\?\.abort\(\)[\s\S]*?calendarGroupRequestRef\.current\?\.abort\(\)[\s\S]*?\};\s*\}, \[\]\)/,
  );
});
