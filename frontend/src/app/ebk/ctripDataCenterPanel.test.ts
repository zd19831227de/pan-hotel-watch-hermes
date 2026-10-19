import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./CtripDataCenterPanel.tsx", import.meta.url), "utf8");

test("EBK numeric blur fields use a bounded fallback instead of Number(empty)", () => {
  assert.match(source, /boundedNumber/);
  assert.doesNotMatch(source, /Number\(event\.target\.value\)/);
  assert.match(source, /query_interval_minutes:\s*boundedNumber/);
  assert.match(source, /retention_days:\s*boundedNumber/);
});

test("data center panel marks auto polling as taken over by global push", () => {
  // 设计 §10.2：旧自动轮询开关标注接管（未迁移门店仍可用，故不隐藏按钮）
  assert.ok(source.includes("已由「EBK 操作台 · 全局定时推送」接管"));
});
