import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(new URL("./ReviewManagementPanel.tsx", import.meta.url), "utf8");

test("review management panel keeps operator-facing copy", () => {
  assert.match(panelSource, /自动回复设置/);
  assert.match(panelSource, /半自动（数量提醒）/);
  assert.match(panelSource, /全自动回复/);
  assert.match(panelSource, /关闭自动回复/);
  assert.match(panelSource, /导入历史回复生成模板/);
  assert.match(panelSource, /自动更新评价/);
  assert.match(panelSource, /评价接口未接入/);
  assert.match(panelSource, /回复模板/);
  // 评价列表已移除：待回复查看/回复走 OTA 后台或飞书审批卡，不在控制台堆长列表。
  assert.doesNotMatch(panelSource, /按模板生成回复草案/);
  assert.doesNotMatch(panelSource, /<h3>评价列表<\/h3>/);
});

test("review management panel exposes template and review config actions only", () => {
  assert.match(panelSource, /onTemplateSave/);
  assert.match(panelSource, /onTemplateDelete/);
  assert.match(panelSource, /onProcessPending/);
  // 评价回复属于 OTA 写操作，但不含任何自动执行按钮文案（全自动由后端开关驱动）。
  assert.doesNotMatch(panelSource, /立即调价|写入携程|自动执行/);
  // 手动回复/状态走 OTA 后台，控制台不再暴露单条回复入口。
  assert.doesNotMatch(panelSource, /onReply\b/);
});
