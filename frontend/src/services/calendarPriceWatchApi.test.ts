import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(new URL("./calendarPriceWatchApi.ts", import.meta.url), "utf8");

test("calendar price watch service uses standalone API endpoints", () => {
  assert.match(apiSource, /\/api\/v1\/calendar-price-watch\/\$\{groupKey\}\/config/);
  assert.match(apiSource, /\/api\/v1\/calendar-price-watch\/\$\{groupKey\}\/run-now/);
  assert.match(apiSource, /\/api\/v1\/calendar-price-watch\/\$\{groupKey\}\/matrix-brief\/push-now/);
  assert.match(apiSource, /\/api\/v1\/calendar-price-watch\/\$\{groupKey\}\/latest/);
  assert.match(apiSource, /future_days:\s*number/);
  assert.match(apiSource, /high_gap_threshold_yuan:\s*number/);
  assert.match(apiSource, /low_gap_threshold_yuan:\s*number/);
  assert.match(apiSource, /schedule_start_time:\s*string/);
  assert.match(apiSource, /schedule_end_time:\s*string/);
  assert.match(apiSource, /schedule_source:\s*"competitor_watch_group"/);
  assert.match(apiSource, /browser_binding_source:\s*"calendar_price_watch_config"/);
  assert.match(apiSource, /browser_session_configured:\s*boolean/);
});
