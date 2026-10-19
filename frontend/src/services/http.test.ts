import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { apiRequest, extractFastApiDetail, setRuntimeApiKey } from "./http.ts";
import { getRuntimeHealth } from "./opsHealthApi.ts";

const originalFetch = globalThis.fetch;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  setRuntimeApiKey("");
});

test("apiRequest explains missing or invalid credentials on 401", async () => {
  globalThis.fetch = async () => jsonResponse({ detail: "unauthorized" }, 401);

  await assert.rejects(
    apiRequest("/protected"),
    /访问凭据无效或未配置，请在高级维护中输入有效的访问密钥。/,
  );
});

test("apiRequest explains insufficient permission on 403", async () => {
  globalThis.fetch = async () => jsonResponse({ detail: "forbidden" }, 403);

  await assert.rejects(apiRequest("/supervisor-only"), /当前访问凭据权限不足。/);
});

test("apiRequest injects the runtime API key without losing explicit headers", async () => {
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    capturedInit = init;
    return jsonResponse({ ok: true });
  };
  setRuntimeApiKey("  session-key  ");

  await apiRequest("/protected", { headers: { "X-Trace-ID": "trace-1" } });

  const headers = new Headers(capturedInit?.headers);
  assert.equal(headers.get("X-Pan-Hotel-API-Key"), "session-key");
  assert.equal(headers.get("X-Trace-ID"), "trace-1");
  assert.equal(headers.get("Content-Type"), "application/json");
});

test("an explicit API key takes precedence and clearing runtime state removes injection", async () => {
  const capturedHeaders: Headers[] = [];
  globalThis.fetch = async (_input, init) => {
    capturedHeaders.push(new Headers(init?.headers));
    return jsonResponse({ ok: true });
  };
  setRuntimeApiKey("runtime-key");

  await apiRequest("/explicit", { headers: { "X-Pan-Hotel-API-Key": "explicit-key" } });
  setRuntimeApiKey("");
  await apiRequest("/public");

  assert.equal(capturedHeaders[0]?.get("X-Pan-Hotel-API-Key"), "explicit-key");
  assert.equal(capturedHeaders[1]?.has("X-Pan-Hotel-API-Key"), false);
});

test("runtime health uses the unified request layer and receives its API key", async () => {
  let capturedPath: string | URL | Request = "";
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedPath = input;
    capturedInit = init;
    return jsonResponse({
      checked_at: "2026-08-10T00:00:00Z",
      modules: {},
      summary: { status: "ok", attention_modules: [], stale_or_missing_modules: [] },
      evidence: {},
    });
  };
  setRuntimeApiKey("health-key");

  await getRuntimeHealth();

  assert.equal(capturedPath, "/api/v1/ops/runtime-health");
  assert.equal(new Headers(capturedInit?.headers).get("X-Pan-Hotel-API-Key"), "health-key");
});

test("apiRequest preserves AbortError so callers can silently ignore cancellation", async () => {
  globalThis.fetch = async () => {
    throw new DOMException("The operation was aborted.", "AbortError");
  };

  await assert.rejects(apiRequest("/cancelled"), (error: unknown) => {
    assert.ok(error instanceof Error);
    assert.equal(error.name, "AbortError");
    return true;
  });
});

test("apiRequest timeoutMs 超时抛可判定的 TimeoutError，且不把 timeoutMs 泄漏给 fetch", async () => {
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (_input, init) => {
    capturedInit = init;
    return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const signal = init.signal as AbortSignal & { reason?: unknown };
        reject(
          signal.reason ??
            new DOMException("The operation was aborted.", "AbortError"),
        );
      });
    });
  };

  await assert.rejects(
    apiRequest("/slow", { timeoutMs: 20 }),
    (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.equal(error.name, "TimeoutError");
      assert.match(error.message, /超时/);
      return true;
    },
  );

  assert.ok(capturedInit?.signal instanceof AbortSignal);
  assert.ok(!("timeoutMs" in (capturedInit ?? {})));
});

test("外部 signal 中止仍是 AbortError，与超时错误可区分", async () => {
  const external = new AbortController();
  globalThis.fetch = (_input, init) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        const signal = init.signal as AbortSignal & { reason?: unknown };
        reject(
          signal.reason ??
            new DOMException("The operation was aborted.", "AbortError"),
        );
      });
      setTimeout(() => external.abort(), 10);
    });

  await assert.rejects(
    apiRequest("/cancelled", { signal: external.signal, timeoutMs: 5000 }),
    (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.equal(error.name, "AbortError");
      return true;
    },
  );
});

test("不传 timeoutMs 时行为不变：不注入 signal 也不挂定时器", async () => {
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = async (_input, init) => {
    capturedInit = init;
    return jsonResponse({ ok: true });
  };

  await apiRequest("/plain");

  assert.equal(capturedInit?.signal, undefined);
});

test("App keeps the supervisor key in memory and syncs it to the request layer", () => {
  const appSource = readFileSync(new URL("../app/App.tsx", import.meta.url), "utf8");

  assert.match(appSource, /setRuntimeApiKey\(value\)/);
  assert.doesNotMatch(appSource, /(?:localStorage|sessionStorage).*supervisorApiKey/);
});

test("extractFastApiDetail 解析 FastAPI 字符串 detail（问题 1：422 legacy 提示须人可读）", () => {
  const raw = JSON.stringify({
    detail:
      "legacy_schedule_still_enabled：携程、美团旧自动排程仍开启，请先在对应平台控制台关闭 auto_poll_enabled 后再启用全局推送。",
  });
  assert.equal(
    extractFastApiDetail(raw),
    "legacy_schedule_still_enabled：携程、美团旧自动排程仍开启，请先在对应平台控制台关闭 auto_poll_enabled 后再启用全局推送。",
  );
});

test("extractFastApiDetail 解析校验错误数组 detail", () => {
  const raw = JSON.stringify({
    detail: [
      { type: "missing", loc: ["body", "name"], msg: "Field required" },
      { type: "value_error", loc: ["body", "x"], msg: "x 必须为正" },
    ],
  });
  assert.equal(extractFastApiDetail(raw), "Field required；x 必须为正");
});

test("extractFastApiDetail 非 JSON 文本按原样返回，空串返回空", () => {
  assert.equal(extractFastApiDetail("Internal Server Error"), "Internal Server Error");
  assert.equal(extractFastApiDetail(""), "");
});

test("apiRequest 把 422 的 JSON detail 作为可读错误抛出，而非裸 JSON 串", async () => {
  const detailText =
    "legacy_schedule_still_enabled：携程旧自动排程仍开启，请先关闭 auto_poll_enabled 后再启用。";
  globalThis.fetch = async () => jsonResponse({ detail: detailText }, 422);

  await assert.rejects(apiRequest("/api/v1/ebk-global-push/configs/1"), (error: unknown) => {
    assert.ok(error instanceof Error);
    assert.equal(error.message, detailText);
    assert.doesNotMatch(error.message, /\{.*detail.*\}/);
    return true;
  });
});
