import { Play, Plus, Save, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createEbkPlatformWatchApi,
  type EbkPlatformWatchGroup,
  type EbkPlatformSchedulerStatus,
} from "../../services/ebkPlatformWatchApi";
import { type BrowserSession } from "../../services/browserSessionsApi";
import { listShops, type Shop } from "../../services/shopsApi";
import {
  pollingStatusText,
  schedulerDetailText,
  startPollingButtonText,
  stopPollingButtonText,
} from "../pollingStatus";
import { EmptyState, formatTime } from "../ui/opsPrimitives";

const SCHEDULE_MODE_OPTIONS = [
  { value: "fixed_slots", label: "固定时刻" },
  { value: "last_run_interval", label: "上次运行后间隔" },
];

function defaultGroupKey(platform: "meituan" | "fliggy") {
  return `${platform}_ebooking_watch`;
}

export function EbkWatchSchedulePanel(props: {
  platform: "meituan" | "fliggy";
  platformLabel: string;
  sessions: BrowserSession[];
  shopKey?: string;
}) {
  const api = useMemo(() => createEbkPlatformWatchApi(props.platform), [props.platform]);
  const platformSessions = useMemo(
    () => props.sessions.filter(
      (session) => session.platform === props.platform && session.intended_use === "ebk",
    ),
    [props.platform, props.sessions],
  );
  const [groups, setGroups] = useState<EbkPlatformWatchGroup[]>([]);
  const [shops, setShops] = useState<Shop[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>("");
  // 店铺分支传入 shopKey 时，组选择/空态/编辑只看该店，防止跨店误操作。
  const scopedGroups = useMemo(
    () => (props.shopKey
      ? groups.filter((item) => item.shop_key === props.shopKey)
      : groups),
    [groups, props.shopKey],
  );
  const group = scopedGroups.find((item) => item.group_key === selectedKey) ?? scopedGroups[0] ?? null;
  const scheduler = useSchedulerStatus();
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionHint, setActionHint] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newGroupKey, setNewGroupKey] = useState(defaultGroupKey(props.platform));
  const [newGroupName, setNewGroupName] = useState(`${props.platformLabel} EBK 监控组`);
  const [newSessionId, setNewSessionId] = useState("");
  const [newShopKey, setNewShopKey] = useState("");
  const [newWebhookUrl, setNewWebhookUrl] = useState("");

  const [groupName, setGroupName] = useState("");
  const [briefTitle, setBriefTitle] = useState("");
  const [browserSessionId, setBrowserSessionId] = useState("");
  const [shopKey, setShopKey] = useState("");
  const [webhookDraft, setWebhookDraft] = useState("");
  const [scheduleMode, setScheduleMode] = useState("fixed_slots");
  const [startTime, setStartTime] = useState("06:00");
  const [endTime, setEndTime] = useState("01:00");
  const [intervalMinutes, setIntervalMinutes] = useState(120);

  const scopedShopName = props.shopKey
    ? shops.find((shop) => shop.shop_key === props.shopKey)?.name
    : undefined;
  // 店内接入语义：配置名默认带店名；组标识对操作员不可见（创建时自动生成）。
  const defaultConfigName = scopedShopName
    ? `${scopedShopName}${props.platformLabel}定时推送`
    : `${props.platformLabel} EBK 监控组`;

  useEffect(() => {
    setNewGroupKey(defaultGroupKey(props.platform));
    setNewGroupName(defaultConfigName);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.platform, props.platformLabel, scopedShopName]);

  useEffect(() => {
    if (!group) {
      setGroupName("");
      setBriefTitle("");
      setBrowserSessionId("");
      setShopKey("");
      setWebhookDraft("");
      setScheduleMode("fixed_slots");
      setStartTime("06:00");
      setEndTime("01:00");
      setIntervalMinutes(120);
      return;
    }
    setGroupName(group.name);
    setBriefTitle(group.brief_title ?? "");
    setBrowserSessionId(group.browser_session_id ?? "");
    setShopKey(group.shop_key ?? "");
    setWebhookDraft("");
    setScheduleMode(group.schedule_mode);
    setStartTime(group.schedule_start_time);
    setEndTime(group.schedule_end_time);
    setIntervalMinutes(group.schedule_interval_minutes);
  }, [group?.group_key, group?.name, group?.brief_title, group?.browser_session_id,
    group?.shop_key, group?.schedule_mode, group?.schedule_start_time,
    group?.schedule_end_time, group?.schedule_interval_minutes]);

  const reload = useCallback(async () => {
    setBusy((current) => current ?? "schedule-load");
    try {
      const [nextGroups, status, nextShops] = await Promise.all([
        api.listWatchGroups(),
        api.schedulerStatus().catch(() => null),
        listShops().catch(() => [] as Shop[]),
      ]);
      setGroups(nextGroups);
      const nextScoped = props.shopKey
        ? nextGroups.filter((item) => item.shop_key === props.shopKey)
        : nextGroups;
      setSelectedKey((current) =>
        nextScoped.some((item) => item.group_key === current)
          ? current
          : nextScoped[0]?.group_key ?? "",
      );
      scheduler.setStatus(status);
      setShops(nextShops);
      setNewShopKey((current) => current || nextShops[0]?.shop_key || "");
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "读取定时配置失败。");
    } finally {
      setBusy(null);
    }
  }, [api, props.shopKey, scheduler.setStatus]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const createGroup = async () => {
    const session = platformSessions.find((item) => item.browser_session_id === newSessionId)
      ?? platformSessions[0];
    const effectiveShopKey = props.shopKey || newShopKey;
    if (!effectiveShopKey) {
      setActionHint("请先选择归属店铺（v2 店铺制：定时监控直接归属店铺）。");
      return;
    }
    if (!session) {
      setActionHint(`请先在“账号与浏览器”中配置${props.platformLabel} EBK 账号。`);
      return;
    }
    setBusy("group-create");
    setActionHint(null);
    try {
      const created = await api.createWatchGroup({
        group_key: props.shopKey
          ? `${props.platform}_ebk_${Date.now()}`
          : newGroupKey.trim(),
        name: newGroupName.trim() || defaultConfigName,
        shop_key: effectiveShopKey,
        feishu_webhook_url: newWebhookUrl.trim() || undefined,
        browser_session_id: session.browser_session_id,
      });
      setGroups((current) => [...current, created]);
      setSelectedKey(created.group_key);
      setShowCreate(false);
      setActionHint(
        created.feishu_webhook_url_configured
          ? "定时监控已创建；推送将发送到本组的飞书机器人。"
          : "定时监控已创建；请在本面板配置飞书机器人地址后再开启推送。",
      );
    } catch (error) {
      setActionHint(error instanceof Error ? error.message : "创建定时监控失败。");
    } finally {
      setBusy(null);
    }
  };

  const saveGroup = async () => {
    if (!group) return;
    setBusy("group-save");
    setActionHint(null);
    try {
      const updated = await api.patchWatchGroup(group.group_key, {
        name: groupName.trim(),
        brief_title: briefTitle.trim() || null,
        browser_session_id: browserSessionId,
        shop_key: props.shopKey || shopKey,
        schedule_mode: scheduleMode,
        schedule_start_time: startTime,
        schedule_end_time: endTime,
        schedule_interval_minutes: Number(intervalMinutes) || 120,
      });
      setGroups((current) => current.map((item) => item.group_key === updated.group_key ? updated : item));
      setActionHint("定时监控配置已保存。");
    } catch (error) {
      setActionHint(error instanceof Error ? error.message : "保存定时监控配置失败。");
    } finally {
      setBusy(null);
    }
  };

  const patchGroup = async (patch: Parameters<typeof api.patchWatchGroup>[1], hint: string) => {
    if (!group) return;
    setBusy("group-patch");
    setActionHint(null);
    try {
      const updated = await api.patchWatchGroup(group.group_key, patch);
      setGroups((current) => current.map((item) => item.group_key === updated.group_key ? updated : item));
      setActionHint(hint);
    } catch (error) {
      setActionHint(error instanceof Error ? error.message : "更新定时监控失败。");
    } finally {
      setBusy(null);
    }
  };

  const deleteGroup = async () => {
    if (!group || !window.confirm(`确认删除定时监控“${group.name}”？`)) return;
    setBusy("group-delete");
    setActionHint(null);
    try {
      await api.deleteWatchGroup(group.group_key);
      const remaining = groups.filter((item) => item.group_key !== group.group_key);
      setGroups(remaining);
      setSelectedKey(remaining[0]?.group_key ?? "");
      setActionHint("定时监控已删除。");
    } catch (error) {
      setActionHint(error instanceof Error ? error.message : "删除定时监控失败。");
    } finally {
      setBusy(null);
    }
  };

  const toggleAutoPoll = () => group && void patchGroup(
    { auto_poll_enabled: !group.auto_poll_enabled },
    group.auto_poll_enabled ? "自动轮询已暂停。" : "自动轮询已开启。",
  );

  const runNow = async () => {
    if (!group) return;
    setBusy("schedule-run-now");
    setActionHint(null);
    try {
      const result = await api.runWatchGroupNow(group.group_key);
      setActionHint(`${formatTime(new Date().toISOString())} ${result.summary || result.status}`);
      await reload();
    } catch (error) {
      setActionHint(error instanceof Error ? error.message : "立即读取失败。");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel ebk-watch-schedule-panel">
      <div className="panel-title-row">
        <div>
          <h3>{props.platformLabel} EBK 定时监控</h3>
          <p className="field-hint">定时监控直接归属店铺（每店至多一个）；推送用本组自己的飞书机器人地址，不再依赖携程监控组。</p>
        </div>
        <div className="inline-actions">
          <button type="button" onClick={() => setShowCreate((current) => !current)} title={props.shopKey ? "接入定时推送" : "新建定时监控"}><Plus size={16} /></button>
          {group ? <button type="button" onClick={() => void deleteGroup()} disabled={busy !== null} title="删除当前定时配置"><Trash2 size={16} /></button> : null}
        </div>
      </div>

      {showCreate ? (
        <div className="form-grid ebk-watch-group-create">
          <label>归属店铺
            {props.shopKey ? (
              <select value={props.shopKey} disabled title="店铺分支内新建，归属锁定本店">
                <option value={props.shopKey}>
                  {shops.find((shop) => shop.shop_key === props.shopKey)?.name ?? props.shopKey}（本店）
                </option>
              </select>
            ) : (
              <select value={newShopKey} onChange={(event) => setNewShopKey(event.target.value)} disabled={!shops.length}>
                {!shops.length ? <option value="">暂无店铺，请先在「账号与浏览器」新建</option> : <option value="">选择店铺…</option>}
                {shops.map((shop) => <option key={shop.shop_key} value={shop.shop_key}>{shop.name}</option>)}
              </select>
            )}
          </label>
          <label>飞书机器人地址
            <input value={newWebhookUrl} placeholder="https://open.feishu.cn/…" onChange={(event) => setNewWebhookUrl(event.target.value)} />
            <span className="field-hint">推送卡片发到本组机器人；留空则暂不推送，可稍后配置。</span>
          </label>
          {!props.shopKey ? (
            <label>监控组标识<input value={newGroupKey} onChange={(event) => setNewGroupKey(event.target.value)} /></label>
          ) : null}
          <label>{props.shopKey ? "配置名称" : "监控组名称"}<input value={newGroupName} onChange={(event) => setNewGroupName(event.target.value)} /></label>
          <label>绑定{props.platformLabel}账号
            <select value={newSessionId || platformSessions[0]?.browser_session_id || ""} onChange={(event) => setNewSessionId(event.target.value)} disabled={!platformSessions.length}>
              {!platformSessions.length ? <option value="">暂无对应平台 EBK 账号</option> : null}
              {platformSessions.map((session) => <option key={session.browser_session_id} value={session.browser_session_id}>{session.display_name}</option>)}
            </select>
          </label>
          <div className="action-strip"><button className="primary-button" type="button" disabled={busy !== null || !platformSessions.length || !(props.shopKey || newShopKey)} onClick={() => void createGroup()}><Save size={16} /> {props.shopKey ? "接入定时推送" : "创建定时监控"}</button></div>
        </div>
      ) : null}

      {loadError && !group ? <p className="field-hint">{loadError}</p> : null}
      {!scopedGroups.length && !showCreate ? (
        <EmptyState
          text={props.shopKey
            ? `该店铺尚未接入${props.platformLabel}定时推送，点击“接入”为本店开通。`
            : `尚未创建${props.platformLabel}定时监控，请点击“新建”并选择归属店铺。`}
        />
      ) : null}
      {scopedGroups.length ? (
        <>
          {!props.shopKey ? (
            <label>当前定时监控
              <select value={group?.group_key ?? ""} onChange={(event) => setSelectedKey(event.target.value)}>
                {scopedGroups.map((item) => <option key={item.group_key} value={item.group_key}>{item.name}</option>)}
              </select>
            </label>
          ) : null}

          {group ? <>
            <p className="field-hint">
              {pollingStatusText({ enabled: group.enabled, auto_poll_enabled: group.auto_poll_enabled, scheduler_status: scheduler.status ?? undefined })} · {schedulerDetailText(scheduler.status)}
              {group.last_polled_at ? ` · 上次读取 ${formatTime(group.last_polled_at)}` : ""}
            </p>
            {!group.shop_key ? (
              <p className="field-hint">该配置尚未归属店铺，请在下方选择归属店铺后保存。</p>
            ) : null}
            <div className="form-grid">
              <label>名称<input value={groupName} onChange={(event) => setGroupName(event.target.value)} /></label>
              <label>推送标题<input value={briefTitle} placeholder={groupName || group.name} onChange={(event) => setBriefTitle(event.target.value)} /></label>
              <label>归属店铺
                {props.shopKey ? (
                  <select value={props.shopKey} disabled title="店铺分支内编辑，归属锁定本店">
                    <option value={props.shopKey}>
                      {shops.find((shop) => shop.shop_key === props.shopKey)?.name ?? props.shopKey}（本店）
                    </option>
                  </select>
                ) : (
                  <select value={shopKey} onChange={(event) => setShopKey(event.target.value)} disabled={!shops.length}>
                    <option value="">未归属（待补绑）</option>
                    {shops.map((shop) => <option key={shop.shop_key} value={shop.shop_key}>{shop.name}</option>)}
                  </select>
                )}
              </label>
              <label>浏览器账号
                <select value={browserSessionId} onChange={(event) => setBrowserSessionId(event.target.value)} disabled={!platformSessions.length}>
                  <option value="">未绑定账号</option>
                  {platformSessions.map((session) => <option key={session.browser_session_id} value={session.browser_session_id}>{session.display_name}</option>)}
                </select>
              </label>
              <label>调度方式<select value={scheduleMode} onChange={(event) => setScheduleMode(event.target.value)}>{SCHEDULE_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
              <label>开始时间<input type="time" value={startTime} onChange={(event) => setStartTime(event.target.value)} /></label>
              <label>结束时间<input type="time" value={endTime} onChange={(event) => setEndTime(event.target.value)} /></label>
              <label>间隔（分钟）<input type="number" min={1} max={1440} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>
            </div>
            <div className="inline-edit-row">
              <input
                value={webhookDraft}
                placeholder={group.feishu_webhook_url_configured ? "粘贴新的飞书机器人地址覆盖" : "https://open.feishu.cn/… 推送卡片发到本组机器人"}
                onChange={(event) => setWebhookDraft(event.target.value)}
              />
              <button
                className="secondary-button"
                type="button"
                disabled={busy !== null || !webhookDraft.trim()}
                onClick={() => {
                  const url = webhookDraft.trim();
                  setWebhookDraft("");
                  void patchGroup({ feishu_webhook_url: url }, "飞书机器人地址已保存。");
                }}
              >
                <Save size={16} /> 保存机器人地址
              </button>
            </div>
            <p className="field-hint">
              {group.feishu_webhook_url_configured
                ? "投递：本组自己的飞书机器人。"
                : group.ctrip_watch_group_key
                  ? "投递：暂未配置本组机器人，将回落到旧挂载携程组的机器人（迁移期兜底）。"
                  : "投递：尚未配置本组机器人地址，读取结果只落库不推送。"}
            </p>
            <div className="toggle-row">
              <label><input type="checkbox" checked={group.enabled} onChange={(event) => void patchGroup({ enabled: event.target.checked }, event.target.checked ? "定时监控已启用。" : "定时监控已停用。")} /> 启用定时监控</label>
            </div>
            <p className="field-hint">
              自动轮询已由「EBK 操作台 · 全局定时推送」接管：迁移到全局推送的门店请保持下方自动轮询开关关闭；未迁移门店仍可使用本面板定时监控。
            </p>
            {actionHint ? <p className="field-hint">{actionHint}</p> : null}
            {loadError ? <p className="field-hint">{loadError}</p> : null}
            <div className="action-strip">
              <button className="secondary-button" type="button" disabled={busy !== null} onClick={() => void saveGroup()}><Save size={16} /> 保存定时配置</button>
              <button className="secondary-button" type="button" disabled={busy !== null} onClick={toggleAutoPoll}>{group.auto_poll_enabled ? <X size={16} /> : <Play size={16} />} {group.auto_poll_enabled ? stopPollingButtonText() : startPollingButtonText()}</button>
              <button className="primary-button" type="button" disabled={busy !== null || !group.enabled} onClick={() => void runNow()}><Play size={16} /> 立即执行读取</button>
            </div>
          </> : null}
        </>
      ) : null}
    </section>
  );
}

function useSchedulerStatus() {
  const [status, setStatus] = useState<EbkPlatformSchedulerStatus | null>(null);
  const update = useCallback((next: EbkPlatformSchedulerStatus | null) => {
    setStatus(next);
  }, []);
  return { status, setStatus: update };
}
