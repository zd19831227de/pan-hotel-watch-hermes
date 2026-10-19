let runtimeApiKey = "";

export function setRuntimeApiKey(value: string): void {
  runtimeApiKey = value.trim();
}

export function getRuntimeApiKey(): string {
  return runtimeApiKey;
}

export type ApiRequestInit = RequestInit & {
  // 请求超时（毫秒）。超时后以 name="TimeoutError" 的可读错误拒绝；
  // 不传则不限时（保持既有行为）。外部 signal 中止仍抛 AbortError，两者可区分。
  timeoutMs?: number;
};

export async function apiRequest<T>(path: string, init?: ApiRequestInit): Promise<T> {
  const { timeoutMs, ...requestInit } = init ?? {};
  const externalSignal = requestInit.signal ?? null;
  const useTimeout = typeof timeoutMs === "number" && timeoutMs > 0;
  // 不传 timeoutMs / signal 时 fetch init 与旧实现逐字节一致（不注入 signal）。
  let controller: AbortController | null = null;
  if (externalSignal || useTimeout) {
    controller = new AbortController();
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort();
      } else {
        externalSignal.addEventListener("abort", () => controller?.abort(), { once: true });
      }
    }
  }
  let timeoutTimer: ReturnType<typeof setTimeout> | undefined;
  if (useTimeout && controller) {
    timeoutTimer = setTimeout(() => {
      const seconds = Math.ceil(timeoutMs / 1000);
      controller?.abort(
        new DOMException(`请求超时（约 ${seconds} 秒），已自动取消；请稍后重试。`, "TimeoutError"),
      );
    }, timeoutMs);
  }
  let response: Response;
  try {
    const headers = new Headers(requestInit.headers);
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (runtimeApiKey && !headers.has("X-Pan-Hotel-API-Key")) {
      headers.set("X-Pan-Hotel-API-Key", runtimeApiKey);
    }
    const fetchInit: RequestInit = { ...requestInit, headers };
    if (controller) {
      fetchInit.signal = controller.signal;
    }
    response = await fetch(path, fetchInit);
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    if (error instanceof Error && error.name === "TimeoutError") {
      const timeoutError = new Error(error.message || "请求超时，已自动取消。");
      timeoutError.name = "TimeoutError";
      throw timeoutError;
    }
    throw new Error(
      error instanceof TypeError
        ? "后端服务未连接，请先启动 backend 后再操作。"
        : error instanceof Error
          ? error.message
          : "请求失败",
    );
  } finally {
    if (timeoutTimer !== undefined) {
      clearTimeout(timeoutTimer);
    }
  }
  if (!response.ok) {
    const rawDetail = await response.text();
    if (response.status === 401) {
      throw new Error("访问凭据无效或未配置，请在高级维护中输入有效的访问密钥。");
    }
    if (response.status === 403) {
      throw new Error("当前访问凭据权限不足。");
    }
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      throw new Error("后端服务未连接，请检查 backend 是否正在运行。");
    }
    // FastAPI 422/409 等把可读说明放在 JSON body 的 detail 字段；直接抛原文会让
    // 用户看到 {"detail":"..."} 串。解析出人可读的 detail 再抛。
    const detail = extractFastApiDetail(rawDetail);
    throw new Error(detail || `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function extractFastApiDetail(raw: string): string {
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail.trim();
    }
    if (Array.isArray(parsed.detail) && parsed.detail.length) {
      return parsed.detail
        .map((item) =>
          typeof item === "string"
            ? item
            : typeof item?.msg === "string"
              ? item.msg
              : JSON.stringify(item),
        )
        .filter(Boolean)
        .join("；");
    }
  } catch {
    // 非 JSON，按原文本返回
  }
  return raw.trim();
}
