import assert from "node:assert/strict";
import test from "node:test";

import { dataCenterRunNowTimeoutMs } from "./dataCenterRunControl.ts";

// run-now 是串行多数据集长请求：超时按「启用数据集数 × 单集 60s 上限 + 余量」计算，
// 不写死统一 180s（会误杀多数据集）也不无限等待（单集卡死时按钮永久转圈）。
test("run-now 超时 = 启用数据集数 × 60s + 30s 余量", () => {
  assert.equal(dataCenterRunNowTimeoutMs(5), 5 * 60_000 + 30_000);
  assert.equal(dataCenterRunNowTimeoutMs(10), 10 * 60_000 + 30_000);
});

test("run-now 超时下限 90s（0/1 个启用数据集与非法输入不缩短）", () => {
  assert.equal(dataCenterRunNowTimeoutMs(0), 90_000);
  assert.equal(dataCenterRunNowTimeoutMs(1), 90_000);
  assert.equal(dataCenterRunNowTimeoutMs(-3), 90_000);
  assert.equal(dataCenterRunNowTimeoutMs(Number.NaN), 90_000);
});
