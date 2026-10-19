import type { BrowserRotationMode, CompetitorHotel, CompetitorWatchGroup } from "../services/competitorWatchApi.ts";
import type { BrowserSession } from "../services/browserSessionsApi.ts";

export type CompetitorHotelForm = {
  role: "own" | "competitor";
  alias: string;
  short_alias: string;
  detail_url: string;
  sort_order: string;
  enabled: boolean;
};

export type CompetitorGroupForm = {
  name: string;
  brief_title: string;
  browser_session_id: string;
  browser_rotation_mode: BrowserRotationMode;
  pool_member_browser_session_ids: string[];
  pool_failover_enabled: boolean;
  pool_cooldown_minutes: string;
  pricing_browser_session_id: string;
  schedule_mode: "fixed_slots" | "last_run_interval";
  schedule_start_time: string;
  schedule_end_time: string;
  schedule_interval_minutes: string;
  feishu_card_layout: "mobile_blocks";
  push_date_scope: "today_only" | "today_tomorrow";
  feishu_chat_id: string;
  enabled: boolean;
  auto_poll_enabled: boolean;
};

export const defaultHotelForm: CompetitorHotelForm = {
  role: "competitor",
  alias: "",
  short_alias: "",
  detail_url: "",
  sort_order: "10",
  enabled: true,
};

export function groupFormFromWatchGroup(group: CompetitorWatchGroup | null): CompetitorGroupForm {
  return {
    name: group?.name ?? "",
    brief_title: group?.brief_title ?? "",
    browser_session_id: group?.browser_session_id ?? "",
    browser_rotation_mode: group?.browser_rotation_mode ?? "fixed",
    pool_member_browser_session_ids:
      group?.browser_pool?.members
        ?.filter((member) => member.enabled)
        .map((member) => member.browser_session_id) ?? [],
    pool_failover_enabled: group?.pool_failover_enabled ?? group?.browser_pool?.failover_enabled ?? true,
    pool_cooldown_minutes: String(group?.pool_cooldown_minutes ?? group?.browser_pool?.cooldown_minutes ?? 60),
    pricing_browser_session_id: group?.pricing_browser_session_id ?? "",
    schedule_mode: group?.schedule_mode ?? "fixed_slots",
    schedule_start_time: group?.schedule_start_time ?? "06:00",
    schedule_end_time: group?.schedule_end_time ?? "01:00",
    schedule_interval_minutes: String(group?.schedule_interval_minutes ?? group?.query_interval_minutes ?? 120),
    feishu_card_layout: "mobile_blocks",
    push_date_scope: group?.push_date_scope ?? "today_tomorrow",
    feishu_chat_id: "",
    enabled: group?.enabled ?? true,
    auto_poll_enabled: group?.auto_poll_enabled ?? false,
  };
}

export function hotelFormFromHotel(hotel: CompetitorHotel): CompetitorHotelForm {
  return {
    role: hotel.role,
    alias: hotel.alias,
    short_alias: hotel.short_alias ?? "",
    detail_url: hotel.detail_url,
    sort_order: String(hotel.sort_order),
    enabled: hotel.enabled,
  };
}

export function resetHotelFormForGroup(): CompetitorHotelForm {
  return { ...defaultHotelForm };
}

export function competitorGroupPatchFromForm(
  form: CompetitorGroupForm,
  sessions: BrowserSession[],
): Partial<CompetitorWatchGroup> & {
  clear_binding?: boolean;
  clear_pricing_binding?: boolean;
  pool_member_browser_session_ids?: string[];
} {
  const selectedSession = sessions.find(
    (session) =>
      session.browser_session_id === form.browser_session_id &&
      session.intended_use === "competitor_price",
  );
  const selectedPricingSession = sessions.find(
    (session) =>
      session.browser_session_id === form.pricing_browser_session_id &&
      session.intended_use === "ebk",
  );
  const interval = Number(form.schedule_interval_minutes);
  const poolCooldownMinutes = Number(form.pool_cooldown_minutes);
  const browserRotationMode = form.browser_rotation_mode ?? "fixed";
  const poolMemberBrowserSessionIds = (form.pool_member_browser_session_ids ?? []).filter((id) =>
    sessions.some((session) => session.browser_session_id === id && session.intended_use === "competitor_price"),
  );
  const patch: Partial<CompetitorWatchGroup> & {
    clear_binding?: boolean;
    clear_pricing_binding?: boolean;
    pool_member_browser_session_ids?: string[];
  } = {
    name: form.name.trim(),
    brief_title: form.brief_title.trim(),
    browser_rotation_mode: browserRotationMode,
    pool_failover_enabled: form.pool_failover_enabled,
    pool_cooldown_minutes: Number.isFinite(poolCooldownMinutes) ? poolCooldownMinutes : 60,
    browser_session_id: selectedSession?.browser_session_id,
    platform_account_id: selectedSession?.platform_account_id,
    clear_binding: browserRotationMode === "fixed" && !form.browser_session_id,
    pricing_browser_session_id: selectedPricingSession?.browser_session_id,
    pricing_platform_account_id: selectedPricingSession?.platform_account_id,
    clear_pricing_binding: !form.pricing_browser_session_id,
    schedule_mode: form.schedule_mode,
    schedule_start_time: form.schedule_start_time,
    schedule_end_time: form.schedule_end_time,
    schedule_interval_minutes: Number.isFinite(interval) ? interval : 120,
    query_interval_minutes: Number.isFinite(interval) ? interval : 120,
    feishu_card_layout: "mobile_blocks",
    push_date_scope: form.push_date_scope,
    enabled: form.enabled,
    auto_poll_enabled: form.auto_poll_enabled,
    pool_member_browser_session_ids: poolMemberBrowserSessionIds,
  };
  if (form.feishu_chat_id.trim()) {
    patch.feishu_chat_id = form.feishu_chat_id.trim();
  }
  return patch;
}

export function nextGroupKeyAfterDelete(
  groups: Array<Pick<CompetitorWatchGroup, "group_key">>,
  deletedGroupKey: string,
): string {
  const deletedIndex = groups.findIndex((group) => group.group_key === deletedGroupKey);
  const remaining = groups.filter((group) => group.group_key !== deletedGroupKey);
  if (!remaining.length) {
    return "";
  }
  if (deletedIndex < 0) {
    return remaining[0].group_key;
  }
  return remaining[Math.min(deletedIndex, remaining.length - 1)].group_key;
}
