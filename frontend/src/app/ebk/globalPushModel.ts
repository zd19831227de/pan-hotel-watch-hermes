export type GlobalPushPlatform = "ctrip" | "meituan" | "fliggy";

export type GlobalPushConfigView = {
  id: number;
  ctrip_watch_group_key: string;
  name: string;
  enabled: boolean;
  enabled_platforms: string[];
  schedule_mode: "fixed_slots" | "last_run_interval";
  push_times: string[];
  interval_minutes: number;
  window_start_time: string;
  window_end_time: string;
  card_title_business: string | null;
  card_title_promotion: string | null;
  card_title_advertising: string | null;
  last_attempted_at: string | null;
  last_successful_push_at: string | null;
  next_run_at: string | null;
  consecutive_failure_count: number;
};

export type GlobalPushForm = {
  ctrip_watch_group_key: string;
  name: string;
  enabled_platforms: GlobalPushPlatform[];
  schedule_mode: "fixed_slots" | "last_run_interval";
  push_times_text: string;
  interval_minutes: string;
  window_start_time: string;
  window_end_time: string;
  card_title_business: string;
  card_title_promotion: string;
  card_title_advertising: string;
};

export const GLOBAL_PUSH_PLATFORM_LABELS: Record<GlobalPushPlatform, string> = {
  ctrip: "携程",
  meituan: "美团",
  fliggy: "飞猪",
};

export const GLOBAL_PUSH_PLATFORM_ICONS: Record<GlobalPushPlatform, string> = {
  ctrip: "✈️",
  meituan: "🦘",
  fliggy: "🧳",
};

const TIME_OF_DAY_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

export function globalPushStatusLabel(status: string): string {
  const mapping: Record<string, string> = {
    running: "运行中",
    success: "成功",
    partial: "部分成功",
    failed: "失败",
    config_required: "需配置",
    human_required: "需人工处理",
    needs_review: "需复核",
    skipped_overlapping: "互斥跳过",
    missed_slot: "错过槽位",
  };
  return mapping[status] ?? (status || "未知");
}

// 后端 trigger 取值 scheduler|operator|feishu|test（展示层映射，不改后端）。
const PUSH_TRIGGER_LABELS: Record<string, string> = {
  scheduler: "定时推送",
  operator: "手动推送",
  feishu: "飞书触发",
  test: "测试",
};

export function globalPushTriggerLabel(trigger: string): string {
  return PUSH_TRIGGER_LABELS[trigger] ?? (trigger || "未知");
}

function daySnapshotStatusLabel(status: string): string {
  const mapping: Record<string, string> = {
    success: "完成",
    partial: "部分完成",
    failed: "失败",
  };
  return mapping[status] ?? globalPushStatusLabel(status);
}

// 「最新运行日志」统一行模型：合并全局推送 runs 与昨日快照轮次记录。
export type RecentRunSource = {
  id: number;
  trigger?: string;
  status: string;
  push_finished_at?: string | null;
  read_finished_at?: string | null;
  triggered_at?: string | null;
  platform_results?: Record<string, { status?: string; detail?: string; summary?: string }>;
  error_summary?: string | null;
};

export type RecentRunRow = {
  key: string;
  time: string;
  triggerLabel: string;
  statusLabel: string;
  platformResultsText: string;
  note: string;
};

function formatRunTime(value: string | null | undefined): string {
  return (value ?? "").replace("T", " ").slice(0, 16);
}

function platformResultsText(
  run: RecentRunSource,
  statusLabel: (status: string) => string,
): string {
  const entries = Object.entries(run.platform_results ?? {});
  if (!entries.length) return "-";
  return (
    entries
      .map(([platform, result]) => {
        const icon = GLOBAL_PUSH_PLATFORM_ICONS[platform as GlobalPushPlatform] ?? "";
        return `${icon}${statusLabel(result?.status ?? "")}`;
      })
      .join(" / ") || "-"
  );
}

export function buildRecentRunRows(
  pushRuns: RecentRunSource[],
  daySnapshotRuns: RecentRunSource[],
): RecentRunRow[] {
  const rows: RecentRunRow[] = [];
  for (const run of pushRuns) {
    rows.push({
      key: `push-${run.id}`,
      time: formatRunTime(run.push_finished_at ?? run.read_finished_at),
      triggerLabel: globalPushTriggerLabel(run.trigger ?? ""),
      statusLabel: globalPushStatusLabel(run.status),
      platformResultsText: platformResultsText(run, globalPushStatusLabel),
      note: run.error_summary ?? "",
    });
  }
  for (const run of daySnapshotRuns) {
    const unfinishedDetails = Object.entries(run.platform_results ?? {})
      .filter(([, result]) => result?.status && result.status !== "success")
      .map(([platform, result]) => {
        const label = GLOBAL_PUSH_PLATFORM_LABELS[platform as GlobalPushPlatform] ?? platform;
        return `${label}：${result?.detail ?? daySnapshotStatusLabel(result?.status ?? "")}`;
      })
      .join("；");
    rows.push({
      key: `day-${run.id}`,
      time: formatRunTime(run.triggered_at),
      triggerLabel: "昨日快照轮",
      statusLabel: daySnapshotStatusLabel(run.status),
      platformResultsText: platformResultsText(run, daySnapshotStatusLabel),
      note: run.error_summary ?? unfinishedDetails ?? "",
    });
  }
  rows.sort((a, b) => b.time.localeCompare(a.time));
  return rows.slice(0, 20);
}

export function globalPushScheduleSummary(config: GlobalPushConfigView): string {
  if (config.schedule_mode === "fixed_slots") {
    return `固定时刻 ${config.push_times.join("、") || "未配置"}`;
  }
  return `间隔 ${config.interval_minutes} 分钟（${config.window_start_time}-${config.window_end_time}）`;
}

export function globalPushNextRunLabel(config: GlobalPushConfigView): string {
  if (!config.enabled) return "已停用";
  if (!config.next_run_at) return "待启用后计算";
  return config.next_run_at.replace("T", " ").slice(0, 16);
}

export function parseGlobalPushTimesInput(text: string): {
  times: string[];
  error: string | null;
} {
  const rawItems = text
    .split(/[,，、;；\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const times: string[] = [];
  for (const item of rawItems) {
    if (!TIME_OF_DAY_PATTERN.test(item)) {
      return { times: [], error: `时间点 ${item} 格式不合法，应为 HH:MM。` };
    }
    if (!times.includes(item)) times.push(item);
  }
  if (!times.length) return { times: [], error: "至少填写一个时间点。" };
  return { times: [...times].sort(), error: null };
}

export function globalPushFormFromConfig(config: GlobalPushConfigView | null): GlobalPushForm {
  return {
    ctrip_watch_group_key: config?.ctrip_watch_group_key ?? "",
    name: config?.name ?? "",
    enabled_platforms: (config?.enabled_platforms ?? ["ctrip", "meituan", "fliggy"]) as GlobalPushPlatform[],
    schedule_mode: config?.schedule_mode ?? "fixed_slots",
    push_times_text: (config?.push_times ?? ["08:00", "12:00", "20:00"]).join("、"),
    interval_minutes: String(config?.interval_minutes ?? 60),
    window_start_time: config?.window_start_time ?? "06:00",
    window_end_time: config?.window_end_time ?? "23:00",
    card_title_business: config?.card_title_business ?? "",
    card_title_promotion: config?.card_title_promotion ?? "",
    card_title_advertising: config?.card_title_advertising ?? "",
  };
}

export function globalPushPatchFromForm(form: GlobalPushForm): {
  patch: Record<string, unknown>;
  error: string | null;
} {
  if (!form.ctrip_watch_group_key.trim()) {
    return { patch: {}, error: "请先选择携程监控组（锚点）。" };
  }
  if (!form.name.trim()) {
    return { patch: {}, error: "请填写配置名称。" };
  }
  if (!form.enabled_platforms.length) {
    return { patch: {}, error: "至少启用一个平台。" };
  }
  const patch: Record<string, unknown> = {
    name: form.name.trim(),
    enabled_platforms: form.enabled_platforms,
    schedule_mode: form.schedule_mode,
    window_start_time: form.window_start_time,
    window_end_time: form.window_end_time,
    card_title_business: form.card_title_business.trim() || null,
    card_title_promotion: form.card_title_promotion.trim() || null,
    card_title_advertising: form.card_title_advertising.trim() || null,
  };
  if (form.schedule_mode === "fixed_slots") {
    const parsed = parseGlobalPushTimesInput(form.push_times_text);
    if (parsed.error) return { patch: {}, error: parsed.error };
    patch.push_times = parsed.times;
  } else {
    const minutes = Number(form.interval_minutes);
    if (!Number.isFinite(minutes) || minutes < 30 || minutes > 1440) {
      return { patch: {}, error: "间隔分钟需在 30-1440 之间。" };
    }
    patch.interval_minutes = minutes;
  }
  return { patch, error: null };
}
