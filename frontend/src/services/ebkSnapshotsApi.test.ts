import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(new URL("./ebkSnapshotsApi.ts", import.meta.url), "utf8");

test("ebk snapshots service uses read-all and latest endpoints", () => {
  assert.match(apiSource, /\/api\/v1\/ebk-snapshots\/read-all/);
  assert.match(apiSource, /\/api\/v1\/ebk-snapshots\/latest/);
  assert.match(apiSource, /ctrip_platform_account_id/);
  assert.match(apiSource, /meituan_platform_account_id/);
  assert.match(apiSource, /platform_account_id/);
  assert.match(apiSource, /payload_json/);
  assert.match(apiSource, /dataset_count/);
  assert.match(apiSource, /EbkPlatform/);
  assert.ok(!apiSource.includes("dry-run"));
  assert.ok(!apiSource.includes("execute"));
  assert.ok(!apiSource.includes("调价"));
});
