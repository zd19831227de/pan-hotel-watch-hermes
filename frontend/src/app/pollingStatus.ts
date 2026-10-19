export type SchedulerStatus = {
  status?: string | null;
  process_confirmed?: boolean | null;
  updated_at?: string | null;
  last_heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  last_due_group_count?: number | null;
  last_result_count?: number | null;
  last_success_count?: number | null;
  last_failed_count?: number | null;
};

export type PollingSummaryInput = {
  enabled?: boolean | null;
  auto_poll_enabled?: boolean | null;
  scheduler_status?: SchedulerStatus | null;
  // 展示用组名（聚合状态条列出组名时使用，单一组状态文案不依赖它）。
  name?: string | null;
};

export function pollingStatusText(input: PollingSummaryInput | null | undefined): string {
  if (!input) {
    return "未创建";
  }
  if (!input.enabled) {
    return "已停用";
  }
  if (!input.auto_poll_enabled) {
    return "自动轮询已暂停";
  }
  const scheduler = input.scheduler_status;
  if (scheduler?.process_confirmed || scheduler?.status === "healthy") {
    return "自动轮询已开启";
  }
  if (scheduler?.status === "stale") {
    return "自动轮询开启，后台心跳过期";
  }
  if (scheduler?.status === "error") {
    return "自动轮询异常";
  }
  return "自动轮询开启，后台未确认";
}

export function schedulerDetailText(scheduler: SchedulerStatus | null | undefined): string {
  if (!scheduler) {
    return "暂无轮询心跳";
  }
  const lastSeen = formatTime(scheduler.last_heartbeat_at ?? scheduler.updated_at);
  if (scheduler.process_confirmed || scheduler.status === "healthy") {
    return `后台已确认，最近心跳 ${lastSeen}`;
  }
  if (scheduler.status === "stale") {
    return `最近心跳 ${lastSeen}，已过期`;
  }
  if (scheduler.status === "error") {
    return "后台轮询异常";
  }
  return "后台轮询未确认";
}

export function manualCompetitorRunButtonText(): string {
  return "读取并推送";
}

export function manualEbkRunButtonText(): string {
  return "读取并推送";
}

export function startPollingButtonText(): string {
  return "开启自动轮询";
}

export function stopPollingButtonText(): string {
  return "暂停自动轮询";
}

export function canStartPolling(input: PollingSummaryInput | null | undefined): boolean {
  return Boolean(input?.enabled && !input.auto_poll_enabled);
}

export function canStopPolling(input: PollingSummaryInput | null | undefined): boolean {
  return Boolean(input?.enabled && input.auto_poll_enabled);
}

export type EbkWatchGroupScheduleInput = {
  group_key: string;
  name: string;
  enabled?: boolean | null;
  auto_poll_enabled?: boolean | null;
  daily_brief_enabled?: boolean | null;
  daily_brief_push_time?: string | null;
};

export type EbkScheduleTask = {
  groupKey: string;
  groupName: string;
  kind: "auto_poll" | "daily_brief";
  label: string;
};

export function listActiveEbkScheduleTasks(
  groups: EbkWatchGroupScheduleInput[] | null | undefined,
): EbkScheduleTask[] {
  const tasks: EbkScheduleTask[] = [];
  for (const group of groups ?? []) {
    if (!group.enabled) {
      continue;
    }
    if (group.auto_poll_enabled) {
      tasks.push({
        groupKey: group.group_key,
        groupName: group.name,
        kind: "auto_poll",
        label: "数据中心轮询",
      });
    }
    if (group.daily_brief_enabled) {
      tasks.push({
        groupKey: group.group_key,
        groupName: group.name,
        kind: "daily_brief",
        label: `每日简报 ${group.daily_brief_push_time ?? "08:30"}`,
      });
    }
  }
  return tasks;
}

export function ebkStatusStripText(groups: EbkWatchGroupScheduleInput[] | null | undefined): string {
  const tasks = listActiveEbkScheduleTasks(groups);
  if (!groups?.length) {
    return "未创建";
  }
  if (!tasks.length) {
    return "全部定时已关闭";
  }
  if (tasks.length === 1) {
    const task = tasks[0];
    return `${task.groupName} · ${task.label}`;
  }
  return `${tasks.length} 个定时任务运行中`;
}

export function ebkStatusStripDetail(groups: EbkWatchGroupScheduleInput[] | null | undefined): string {
  const tasks = listActiveEbkScheduleTasks(groups);
  if (!tasks.length) {
    return "可在 EBK 操作台分别管理数据中心轮询与每日简报";
  }
  return tasks.map((task) => `${task.groupName}：${task.label}`).join("；");
}

export function dailyBriefStatusText(group: EbkWatchGroupScheduleInput | null | undefined): string {
  if (!group) {
    return "未配置";
  }
  if (!group.enabled) {
    return "已停用";
  }
  if (!group.daily_brief_enabled) {
    return "未启用";
  }
  return `每日 ${group.daily_brief_push_time ?? "08:30"} 推送`;
}

export function canStopDailyBrief(group: EbkWatchGroupScheduleInput | null | undefined): boolean {
  return Boolean(group?.enabled && group.daily_brief_enabled);
}

export function otherActiveEbkScheduleSummary(
  groups: EbkWatchGroupScheduleInput[] | null | undefined,
  selectedGroupKey: string | null | undefined,
): string | null {
  const tasks = listActiveEbkScheduleTasks(groups).filter(
    (task) => task.groupKey !== selectedGroupKey,
  );
  if (!tasks.length) {
    return null;
  }
  return `另有 ${tasks.length} 个定时任务在其他监控组：${tasks
    .map((task) => `${task.groupName}（${task.label}）`)
    .join("、")}`;
}

export function stopAllEbkSchedulesButtonText(): string {
  return "关闭全部定时";
}

export function stopDailyBriefButtonText(): string {
  return "关闭每日简报";
}

export type CalendarPriceStatusInput = {
  groupEnabled?: boolean | null;
  auto_poll_enabled?: boolean | null;
  configLoaded?: boolean;
  scheduler_status?: SchedulerStatus | null;
};

export function calendarPriceStatusText(input: CalendarPriceStatusInput | null | undefined): string {
  if (!input) {
    return "未创建";
  }
  if (input.groupEnabled === false) {
    return "已停用";
  }
  if (input.configLoaded === false) {
    return "未配置";
  }
  if (input.configLoaded !== true) {
    return "检查中";
  }
  return pollingStatusText({
    enabled: true,
    auto_poll_enabled: input.auto_poll_enabled,
    scheduler_status: input.scheduler_status,
  });
}

export type PriceChangeAlertStatusInput = {
  strategyEnabled?: boolean | null;
  watchGroup?: PollingSummaryInput | null;
};

export function priceChangeAlertStatusText(input: PriceChangeAlertStatusInput | null | undefined): string {
  if (!input?.watchGroup) {
    return "未创建";
  }
  if (!input.strategyEnabled) {
    return "提醒已停用";
  }
  if (!input.watchGroup.enabled) {
    return "已停用";
  }
  if (!input.watchGroup.auto_poll_enabled) {
    return "自动轮询已暂停";
  }
  return pollingStatusText({
    enabled: true,
    auto_poll_enabled: true,
    scheduler_status: input.watchGroup.scheduler_status,
  });
}

export function priceChangeAlertDetailText(
  watchGroup: PollingSummaryInput | null | undefined,
  lastEvaluatedAt?: string | null,
): string {
  const base = schedulerDetailText(watchGroup?.scheduler_status);
  if (!lastEvaluatedAt) {
    return base;
  }
  return `${base}，最近评估 ${formatTime(lastEvaluatedAt)}`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

export type EbkPushSourceOptions = {
  globalPushEnabled?: boolean | null;
};

// 智能确梎 EBK 推送方：全局定时推送接管时优先显示全局，否则回落到平台轮询文案。
// 不改动 pollingStatusText 语义（13 个既有能用方行为不变）。
export function ebkPushSourceText(
  input: PollingSummaryInput | null | undefined,
  options?: EbkPushSourceOptions,
): string {
  if (options?.globalPushEnabled) {
    return "全局定时推送接管中";
  }
  return pollingStatusText(input);
}

// —— 顶部状态条跨组聚合（2026-09-10）：状态不再只反映“当前选中组”，
// 避免“切到启用的组才显示开启”的误导；后台心跳取所有组里最优的一份
// （心跳文件全局唯一，各组读取结果一致）。

export type AggregatedStripStatus = {
  text: string;
  detail: string;
};

function bestSchedulerStatus(
  groups: PollingSummaryInput[] | null | undefined,
): SchedulerStatus | null | undefined {
  const statuses = (groups ?? [])
    .map((group) => group.scheduler_status)
    .filter((status): status is SchedulerStatus => Boolean(status));
  if (!statuses.length) {
    return null;
  }
  return (
    statuses.find((status) => status.process_confirmed || status.status === "healthy") ??
    statuses.find((status) => status.status === "stale") ??
    statuses.find((status) => status.status === "error") ??
    statuses[0]
  );
}

export function competitorAggregateStatus(
  groups: PollingSummaryInput[] | null | undefined,
): AggregatedStripStatus {
  const all = groups ?? [];
  if (!all.length) {
    return { text: "未创建", detail: "还没有竞品监控组" };
  }
  const enabledGroups = all.filter((group) => group.enabled);
  if (!enabledGroups.length) {
    return { text: "全部已停用", detail: "所有竞品监控组均已停用" };
  }
  const pollingGroups = enabledGroups.filter((group) => group.auto_poll_enabled);
  const heartbeat = bestSchedulerStatus(all);
  const heartbeatSuffix = schedulerDetailText(heartbeat);
  if (!pollingGroups.length) {
    return {
      text: `${enabledGroups.length} 组启用，轮询未开启`,
      detail: `${enabledGroups
        .map((group) => group.name ?? "未命名组")
        .join("、")}；${heartbeatSuffix}`,
    };
  }
  return {
    text: `${pollingGroups.length}/${enabledGroups.length} 组自动轮询中`,
    detail: `自动轮询：${pollingGroups
      .map((group) => group.name ?? "未命名组")
      .join("、")}；${heartbeatSuffix}`,
  };
}

export type CalendarConfigSummary = {
  watch_group_key: string;
  group_name?: string | null;
  group_enabled?: boolean | null;
  auto_poll_enabled?: boolean | null;
  matrix_brief_enabled?: boolean | null;
  config_present?: boolean | null;
};

export function calendarAggregateStatus(
  configs: CalendarConfigSummary[] | null | undefined,
  scheduler: SchedulerStatus | null | undefined,
): AggregatedStripStatus {
  const all = configs ?? [];
  if (!all.length) {
    return { text: "未创建", detail: "还没有竞品监控组" };
  }
  const activeGroups = all.filter((config) => config.group_enabled !== false);
  if (!activeGroups.length) {
    return { text: "全部已停用", detail: "所有竞品监控组均已停用" };
  }
  const polling = activeGroups.filter((config) => config.auto_poll_enabled);
  const heartbeatSuffix = schedulerDetailText(scheduler ?? null);
  if (!polling.length) {
    return {
      text: `${activeGroups.length} 组启用，轮询未开启`,
      detail: `${activeGroups.map((config) => config.group_name ?? "未命名组").join("、")}；${heartbeatSuffix}`,
    };
  }
  return {
    text: `${polling.length}/${activeGroups.length} 组自动轮询中`,
    detail: `自动轮询：${polling.map((config) => config.group_name ?? "未命名组").join("、")}；${heartbeatSuffix}`,
  };
}

export function priceChangeAggregateStatus(
  strategyEnabled: boolean | null | undefined,
  strategyWatchGroupName: string | null | undefined,
  groups: PollingSummaryInput[] | null | undefined,
): AggregatedStripStatus {
  const all = groups ?? [];
  if (!all.length) {
    return { text: "未创建", detail: "还没有竞品监控组" };
  }
  if (!strategyEnabled) {
    return { text: "提醒已停用", detail: "价格变化提醒策略未启用" };
  }
  const targetLabel = strategyWatchGroupName ? `（${strategyWatchGroupName}）` : "";
  const polling = all.filter((group) => group.enabled && group.auto_poll_enabled);
  if (!polling.length) {
    return {
      text: "已启用，等待竞品轮询",
      detail: `提醒跟随竞品轮询运行${targetLabel}，当前没有组开启自动轮询`,
    };
  }
  return {
    text: "跟随竞品轮询运行中",
    detail: `提醒跟随竞品轮询运行${targetLabel}；最近心跳 ${formatTime(
      bestSchedulerStatus(all)?.last_heartbeat_at ?? null,
    )}`,
  };
}

export type GlobalPushSchedulerStatus = {
  enabled?: boolean | null;
  status?: string | null;
  last_heartbeat_at?: string | null;
};

export type GlobalPushConfigSummary = {
  id: number;
  name: string;
  enabled?: boolean | null;
};

export function globalPushStripStatus(
  configs: GlobalPushConfigSummary[] | null | undefined,
  scheduler: GlobalPushSchedulerStatus | null | undefined,
): AggregatedStripStatus {
  const all = configs ?? [];
  const enabled = all.filter((config) => config.enabled);
  if (!all.length) {
    return { text: "未配置", detail: "EBK 操作台 → 全局定时推送 里创建推送配置" };
  }
  if (!enabled.length) {
    return { text: "全部已停用", detail: `${all.length} 个推送配置均已停用` };
  }
  const heartbeat = scheduler?.last_heartbeat_at ? `，心跳 ${formatTime(scheduler.last_heartbeat_at)}` : "";
  if (scheduler && !scheduler.enabled) {
    return {
      text: `${enabled.length} 个推送，调度未启用`,
      detail: "全局推送调度器未启用（settings.ebk_global_push_scheduler_enabled）",
    };
  }
  if (scheduler?.status === "healthy" || scheduler?.status === "ok") {
    return {
      text: `${enabled.length} 个推送运行中`,
      detail: enabled.map((config) => config.name).join("、") + heartbeat,
    };
  }
  return {
    text: `${enabled.length} 个推送，心跳未确认`,
    detail: enabled.map((config) => config.name).join("、") + (heartbeat || "，暂无心跳"),
  };
}
