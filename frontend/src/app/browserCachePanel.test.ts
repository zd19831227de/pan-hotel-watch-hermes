import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const panelUrl = new URL("./BrowserCachePanel.tsx", import.meta.url);
const panelSource = existsSync(panelUrl) ? readFileSync(panelUrl, "utf8") : "";
const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const configApiSource = readFileSync(new URL("../services/configApi.ts", import.meta.url), "utf8");

test("BrowserCachePanel 渲染审计表与立即清理按钮", () => {
  assert.ok(panelSource.includes("getBrowserCacheAudit"));
  assert.ok(panelSource.includes("cleanupBrowserCache"));
  assert.ok(panelSource.includes("立即清理网页缓存"));
  assert.ok(panelSource.includes("刷新审计"));
  // Audit columns present
  assert.ok(panelSource.includes("可清理占比"));
});

test("BrowserCachePanel 渲染截图块且保留天数可配（不改源码）", () => {
  assert.ok(panelSource.includes("getScreenshotAudit"));
  assert.ok(panelSource.includes("cleanupScreenshots"));
  assert.ok(panelSource.includes("OTA 抓取截图"));
  assert.ok(panelSource.includes("保留天数"));
  assert.ok(panelSource.includes("shotRetention"));
  assert.ok(panelSource.includes("PLAYWRIGHT_SCREENSHOT_RETENTION_DAYS"));
});

test("BrowserCachePanel 标注登录态与模型数据保留", () => {
  assert.ok(panelSource.includes("登录态"));
  assert.ok(panelSource.includes("Cookies"));
  assert.ok(panelSource.includes("Chromium 模型"));
});

test("BrowserCachePanel 使用专用布局而不是共享的 5 列行", () => {
  const globalCssSource = readFileSync(new URL("../styles/global.css", import.meta.url), "utf8");
  assert.match(panelSource, /browser-cache-action-row/);
  assert.match(panelSource, /browser-cache-retention-row/);
  assert.match(globalCssSource, /\.browser-cache-action-row\s*\{[^}]*grid-template-columns:\s*auto\s+minmax\(0,\s*1fr\)/s);
  assert.match(globalCssSource, /\.browser-cache-retention-row\s*\{[^}]*grid-template-columns:\s*auto\s+auto\s+minmax\(0,\s*1fr\)/s);
});

test("configApi 暴露浏览器缓存审计与清理接口", () => {
  assert.ok(configApiSource.includes("getBrowserCacheAudit"));
  assert.ok(configApiSource.includes("cleanupBrowserCache"));
  assert.ok(configApiSource.includes("/api/v1/browser-sessions/cache-audit"));
  assert.ok(configApiSource.includes("/api/v1/browser-sessions/cleanup"));
  assert.ok(configApiSource.includes("BrowserCacheAudit"));
});

test("configApi 暴露截图审计与清理接口（retention 可传）", () => {
  assert.ok(configApiSource.includes("getScreenshotAudit"));
  assert.ok(configApiSource.includes("cleanupScreenshots"));
  assert.ok(configApiSource.includes("/api/v1/browser-sessions/screenshot-audit"));
  assert.ok(configApiSource.includes("/api/v1/browser-sessions/cleanup-screenshots"));
  assert.ok(configApiSource.includes("retention_days"));
  assert.ok(configApiSource.includes("ScreenshotAudit"));
});

test("App.tsx 在高级配置区挂载 BrowserCachePanel", () => {
  assert.ok(appSource.includes("BrowserCachePanel"));
  assert.ok(appSource.includes('apiKey={supervisorApiKey}'));
});

test("BrowserCachePanel 不暴露受控凭据或内部路径", () => {
  assert.doesNotMatch(panelSource, /app_secret|cookie_path|profile_dir|webhook/i);
});
