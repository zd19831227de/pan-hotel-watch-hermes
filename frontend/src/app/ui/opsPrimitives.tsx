import type { BrowserConnectionMode } from "../../services/browserSessionsApi";

export function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small className="muted">{detail}</small> : null}
    </div>
  );
}

export function EmptyState({ text }: { text: string }) {
  return <p className="empty">{text}</p>;
}

export function ResultNotice({
  status,
  summary,
  detail,
}: {
  status: string;
  summary: string;
  detail?: string;
}) {
  const tone =
    status === "success" ? "success" : status === "warning" ? "warning" : "error";
  return (
    <div className={`connection-result ${tone}`} role="status">
      <strong>{summary}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    success: "成功",
    fresh: "正常",
    ready: "已就绪",
    enabled: "已启用",
    disabled: "已停用",
    ok: "正常",
    stale: "可能过期",
    missing: "缺失",
    config_required: "需要配置",
    human_required: "需要人工处理",
    logged_out: "未登录",
    logged_in: "已登录",
    needs_check: "待确认",
    needs_review: "需要复核",
    page_structure_changed: "页面结构变化",
    browser_busy: "浏览器被占用",
    failed: "失败",
    partial: "部分成功",
    "": "未知",
  };
  return labels[status] ?? status;
}

export function formatTime(value?: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function connectionModeLabel(value?: BrowserConnectionMode | string | null): string {
  if (value === "non_direct") {
    return "非直连账号";
  }
  if (value === "direct") {
    return "直连账号";
  }
  return "账号类型未确认";
}

export function operatorReason(value: string, status?: string | null): string {
  const text = String(value || "").trim();
  const lower = text.toLowerCase();
  if (lower.includes("expected business fields were not found")) {
    return "页面结构变化，缺少可确认业务字段。";
  }
  if (lower.includes("dataset page changed")) {
    return "页面结构变化，需要复核。";
  }
  if (lower.includes("browser session did not respond")) {
    return "浏览器读取超时，请确认本机 Chrome 已打开且 EBK 已登录。";
  }
  if (lower.includes("no browser session")) {
    return "未绑定可用的 EBK 浏览器账号。";
  }
  if (status === "page_structure_changed") {
    return "页面结构变化，需要复核。";
  }
  return text;
}

export function operatorAction(value: string): string {
  const text = String(value || "").trim();
  const lower = text.toLowerCase();
  if (lower.includes("ctrip public competitor read found trusted dom room-rate prices")) {
    return "竞品价格读取到可信房价。";
  }
  if (lower.includes("ctrip public competitor read found no trusted dom room-rate prices")) {
    return "竞品价格未读取到可信房价，需要人工复核。";
  }
  if (lower.includes("human") || lower.includes("verification")) {
    return "需要人工处理。";
  }
  if (lower.includes("login")) {
    return "需要确认登录状态。";
  }
  return text || "暂无操作记录";
}
