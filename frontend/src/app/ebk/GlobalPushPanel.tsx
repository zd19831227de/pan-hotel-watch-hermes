import { Play, Plus, Save, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createEbkGlobalPushConfig,
  getEbkGlobalPushConfigDetail,
  getEbkGlobalPushSchedulerStatus,
  listEbkDaySnapshotRuns,
  listEbkGlobalPushConfigs,
  listEbkGlobalPushRuns,
  patchEbkGlobalPushConfig,
  runEbkGlobalPushNow,
  type EbkDaySnapshotRun,
  type EbkGlobalPushConfig,
  type EbkGlobalPushPlatformTarget,
  type EbkGlobalPushRun,
} from "../../services/ebkGlobalPushApi";
import { type Shop } from "../../services/shopsApi";
import { type DataCenterWatchGroup } from "../../services/ctripEbookingWatchApi";
import {
  GLOBAL_PUSH_PLATFORM_ICONS,
  GLOBAL_PUSH_PLATFORM_LABELS,
  buildRecentRunRows,
  globalPushFormFromConfig,
  globalPushNextRunLabel,
  globalPushPatchFromForm,
  globalPushScheduleSummary,
  globalPushStatusLabel,
  parseGlobalPushTimesInput,
  type GlobalPushForm,
  type GlobalPushPlatform,
} from "./globalPushModel";

const PLATFORMS: GlobalPushPlatform[] = ["ctrip", "meituan", "fliggy"];

type SchedulerStatus = {
  name: string;
  enabled: boolean;
  status: string;
  last_heartbeat_at: string | null;
};

export function GlobalPushPanel(props: {
  shops: Shop[];
  dataCenterGroups: DataCenterWatchGroup[];
}) {
  const [configs, setConfigs] = useState<EbkGlobalPushConfig[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [form, setForm] = useState<GlobalPushForm>(globalPushFormFromConfig(null));
  const [runs, setRuns] = useState<EbkGlobalPushRun[]>([]);
  const [dayRuns, setDayRuns] = useState<EbkDaySnapshotRun[]>([]);
  const [platformTargets, setPlatformTargets] = useState<EbkGlobalPushPlatformTarget[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [runningNow, setRunningNow] = useState(false);

  const selected = configs.find((item) => item.id === selectedId) ?? null;

  // 店铺直连视角：每店至多一个携程组（=推送锚点）；配置下拉用店铺名展示。
  const groupByShopKey = useMemo(() => {
    const map = new Map<string, DataCenterWatchGroup>();
    for (const group of props.dataCenterGroups) {
      if (group.shop_key) {
        map.set(group.shop_key, group);
      }
    }
    return map;
  }, [props.dataCenterGroups]);
  const shopByGroupKey = useMemo(() => {
    const map = new Map<string, Shop>();
    for (const shop of props.shops) {
      const group = groupByShopKey.get(shop.shop_key);
      if (group) {
        map.set(group.group_key, shop);
      }
    }
    return map;
  }, [props.shops, groupByShopKey]);
  const unassignedGroups = props.dataCenterGroups.filter((group) => !group.shop_key);
  const shopsWithoutCtrip = props.shops.filter((shop) => !groupByShopKey.has(shop.shop_key));

  const configOptionLabel = (item: EbkGlobalPushConfig) => {
    const shop = shopByGroupKey.get(item.ctrip_watch_group_key);
    const prefix = shop?.name ?? item.ctrip_watch_group_key;
    return `${prefix} · ${item.name}${item.enabled ? "" : "［停用］"}`;
  };

  const reload = useCallback(async () => {
    try {
      const [configList, schedulerStatus] = await Promise.all([
        listEbkGlobalPushConfigs(),
        getEbkGlobalPushSchedulerStatus(),
      ]);
      setConfigs(configList);
      setScheduler(schedulerStatus);
      // 快照轮运行日志独立容错：后端未重启（端点 404）或查询失败时置空，
      // 不拖垮推送配置/状态的主面板加载。
      try {
        setDayRuns(await listEbkDaySnapshotRuns(10));
      } catch {
        setDayRuns([]);
      }
      const current = configList.find((item) => item.id === selectedId) ?? configList[0] ?? null;
      setSelectedId(current?.id ?? null);
      setForm(globalPushFormFromConfig(current));
      if (current) {
        const [runList, detail] = await Promise.all([
          listEbkGlobalPushRuns(current.id),
          getEbkGlobalPushConfigDetail(current.id),
        ]);
        setRuns(runList);
        setPlatformTargets(detail.platform_targets ?? []);
      } else {
        setRuns([]);
        setPlatformTargets([]);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败。");
    }
  }, [selectedId]);

  useEffect(() => {
    void reload();
    // 仅首次挂载加载；后续操作显式刷新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runAction = useCallback(
    async (action: () => Promise<string>) => {
      setBusy(true);
      setMessage("");
      try {
        setMessage(await action());
        await reload();
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "操作失败。");
      } finally {
        setBusy(false);
      }
    },
    [reload],
  );

  const onCreate = () =>
    void runAction(async () => {
      const { patch, error } = globalPushPatchFromForm(form);
      if (error) throw new Error(error);
      const created = await createEbkGlobalPushConfig({
        ...patch,
        ctrip_watch_group_key: form.ctrip_watch_group_key,
        enabled: false,
      });
      setSelectedId(created.id);
      return "配置已创建（默认停用；确认旧排程关闭后再启用）。";
    });

  const onSave = () =>
    void runAction(async () => {
      if (!selected) throw new Error("请先选择一条配置。");
      const { patch, error } = globalPushPatchFromForm(form);
      if (error) throw new Error(error);
      await patchEbkGlobalPushConfig(selected.id, patch);
      return "配置已保存。";
    });

  const onToggleEnabled = (enabled: boolean) =>
    void runAction(async () => {
      if (!selected) throw new Error("请先选择一条配置。");
      await patchEbkGlobalPushConfig(selected.id, { enabled });
      return enabled
        ? "已启用。若旧自动排程未关闭，服务端会拒绝并提示 legacy_schedule_still_enabled。"
        : "已暂停（软停用，保留运行历史）。";
    });

  const onRunNow = () => {
    setRunningNow(true);
    void runAction(async () => {
      if (!selected) throw new Error("请先选择一条配置。");
      const result = await runEbkGlobalPushNow(selected.id);
      return `读取并推送完成：${globalPushStatusLabel(result.status)}（${result.summary}）`;
    }).finally(() => setRunningNow(false));
  };

  const onSelectConfig = (id: number) => {
    setSelectedId(id);
    const current = configs.find((item) => item.id === id) ?? null;
    setForm(globalPushFormFromConfig(current));
    setPlatformTargets([]);
    if (current) {
      void Promise.all([
        listEbkGlobalPushRuns(current.id),
        getEbkGlobalPushConfigDetail(current.id),
      ])
        .then(([runList, detail]) => {
          setRuns(runList);
          setPlatformTargets(detail.platform_targets ?? []);
        })
        .catch(() => {
          setRuns([]);
          setPlatformTargets([]);
        });
    } else {
      setRuns([]);
    }
  };

  const timesPreview = parseGlobalPushTimesInput(form.push_times_text);
  const runRows = buildRecentRunRows(runs, dayRuns);

  return (
    <section className="workspace ebk-global-push-panel">
      <div className="section-head">
        <div>
          <h3>全局定时推送</h3>
          <p>一次读取携程、美团、飞猪，发送经营/活动/广告三张分类卡。旧平台自动排程需先关闭。</p>
        </div>
      </div>

      <div className="panel ebk-global-push-status">
        <strong>调度器：</strong>
        {scheduler
          ? `${scheduler.enabled ? `已开启（${scheduler.status}）` : "未开启（EBK_GLOBAL_PUSH_SCHEDULER_ENABLED=false）"}`
          : "加载中…"}
        {selected ? `｜下次执行：${globalPushNextRunLabel(selected)}` : ""}
        {selected ? `｜${globalPushScheduleSummary(selected)}` : ""}
      </div>

      <div className="panel ebk-global-push-configs">
        <label>
          配置：
          <select
            value={selectedId ?? ""}
            onChange={(event) => onSelectConfig(Number(event.target.value))}
          >
            <option value="">＋ 新建配置</option>
            {configs.map((item) => (
              <option key={item.id} value={item.id}>
                {configOptionLabel(item)}
              </option>
            ))}
          </select>
        </label>
        {selected ? (
          <>
            <button className="secondary-button" type="button" disabled={busy} onClick={() => onToggleEnabled(!selected.enabled)}>
              {selected.enabled ? <X size={16} /> : <Play size={16} />} {selected.enabled ? "暂停推送" : "启用推送"}
            </button>
            <button className="primary-button" type="button" disabled={busy} onClick={onRunNow}>
              <Play size={16} />{" "}
              {runningNow ? "读取中…携程→美团→飞猪（约数分钟，请勿离开）" : "立即读取并推送"}
            </button>
          </>
        ) : null}
      </div>

      {selected && platformTargets.length ? (
        <div className="panel ebk-global-push-targets">
          <strong>已解析平台账号：</strong>
          {platformTargets.map((target) => {
            const key = target.platform as GlobalPushPlatform;
            return (
              <span key={target.platform} className="ebk-global-push-target">
                {GLOBAL_PUSH_PLATFORM_ICONS[key] ?? ""}{" "}
                {GLOBAL_PUSH_PLATFORM_LABELS[key] ?? target.platform}：
                {target.status === "resolved" || target.status === "success"
                  ? target.account_name || "已绑定"
                  : `${globalPushStatusLabel(target.status)}（${target.summary}）`}
              </span>
            );
          })}
          <span className="hint-text">账号绑定入口在各平台模块；此处仅展示解析结果。</span>
        </div>
      ) : null}

      <div className="panel ebk-global-push-form"><div className="form-grid single">
        <label>
          推送店铺（读取账号与飞书群来自该店的携程配置）：
          <select
            value={form.ctrip_watch_group_key}
            onChange={(event) => {
              const groupKey = event.target.value;
              const shop = shopByGroupKey.get(groupKey);
              // 名称未手填时按店铺预填，与既有「<店铺>综合系统推送」命名一致
              const nextName = !form.name.trim() && shop
                ? `${shop.name}综合系统推送`
                : form.name;
              setForm({ ...form, ctrip_watch_group_key: groupKey, name: nextName });
            }}
          >
            <option value="">请选择店铺</option>
            {props.shops.map((shop) => {
              const group = groupByShopKey.get(shop.shop_key);
              return group ? (
                <option key={shop.shop_key} value={group.group_key}>
                  {shop.name}
                </option>
              ) : (
                <option key={shop.shop_key} value="" disabled>
                  {shop.name}（未接入携程）
                </option>
              );
            })}
            {unassignedGroups.map((group) => (
              <option key={group.group_key} value={group.group_key}>
                未归属店铺：{group.name}
              </option>
            ))}
          </select>
        </label>
        {shopsWithoutCtrip.length ? (
          <p className="field-hint">
            {shopsWithoutCtrip.map((shop) => shop.name).join("、")}尚未接入携程：
            请先在「EBK 操作台」该店铺的携程分支点击「接入携程」，再回到这里创建推送配置。
          </p>
        ) : null}
        <label>
          配置名称：
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="如：新街口店全局推送"
          />
        </label>
        <fieldset>
          <legend>启用平台（账号在各平台模块绑定，此处只选择）</legend>
          {PLATFORMS.map((platform) => (
            <label key={platform} className="ebk-global-push-platform">
              <input
                type="checkbox"
                checked={form.enabled_platforms.includes(platform)}
                onChange={(event) => {
                  const next = event.target.checked
                    ? [...form.enabled_platforms, platform]
                    : form.enabled_platforms.filter((item) => item !== platform);
                  setForm({ ...form, enabled_platforms: next });
                }}
              />
              {GLOBAL_PUSH_PLATFORM_ICONS[platform]} {GLOBAL_PUSH_PLATFORM_LABELS[platform]}
            </label>
          ))}
        </fieldset>
        <label>
          调度模式：
          <select
            value={form.schedule_mode}
            onChange={(event) =>
              setForm({ ...form, schedule_mode: event.target.value as GlobalPushForm["schedule_mode"] })
            }
          >
            <option value="fixed_slots">固定时间点（多选）</option>
            <option value="last_run_interval">按间隔推送</option>
          </select>
        </label>
        {form.schedule_mode === "fixed_slots" ? (
          <label>
            推送时间点（顿号/逗号分隔，如 08:00、12:00、20:00）：
            <input
              value={form.push_times_text}
              onChange={(event) => setForm({ ...form, push_times_text: event.target.value })}
            />
            {timesPreview.error ? (
              <span className="error-text">{timesPreview.error}</span>
            ) : (
              <span className="hint-text">保存顺序：{timesPreview.times.join("、")}</span>
            )}
          </label>
        ) : (
          <div className="ebk-global-push-interval">
            <label>
              间隔分钟（30-1440）：
              <input
                type="number"
                min={30}
                max={1440}
                value={form.interval_minutes}
                onChange={(event) => setForm({ ...form, interval_minutes: event.target.value })}
              />
            </label>
            <label>
              开始时间：
              <input
                type="time"
                value={form.window_start_time}
                onChange={(event) => setForm({ ...form, window_start_time: event.target.value })}
              />
            </label>
            <label>
              结束时间：
              <input
                type="time"
                value={form.window_end_time}
                onChange={(event) => setForm({ ...form, window_end_time: event.target.value })}
              />
            </label>
          </div>
        )}
        <label>
          经营卡标题前缀（可选，固定评分后缀自动追加）：
          <input
            value={form.card_title_business}
            placeholder="默认：经营数据"
            onChange={(event) => setForm({ ...form, card_title_business: event.target.value })}
          />
        </label>
        <label>
          活动卡标题（可选）：
          <input
            value={form.card_title_promotion}
            placeholder="默认：活动促销"
            onChange={(event) => setForm({ ...form, card_title_promotion: event.target.value })}
          />
        </label>
        <label>
          广告卡标题（可选）：
          <input
            value={form.card_title_advertising}
            placeholder="默认：广告营销"
            onChange={(event) => setForm({ ...form, card_title_advertising: event.target.value })}
          />
        </label>
        </div>
        <div className="action-strip ebk-global-push-actions">
          <button className="primary-button" type="button" disabled={busy} onClick={selected ? onSave : onCreate}>
            {selected ? <Save size={16} /> : <Plus size={16} />} {selected ? "保存修改" : "创建配置"}
          </button>
        </div>
        {message ? (
          <p
            className={
              message.startsWith("legacy_schedule_still_enabled")
                ? "ebk-global-push-message warning"
                : "ebk-global-push-message"
            }
          >
            {message.startsWith("legacy_schedule_still_enabled")
              ? `⚠️ ${message}（请到「携程 EBK / 美团 EBK / 飞猪 EBK」面板暂停对应「自动轮询」后再启用全局推送；立即读取并推送无需启用即可测试。）`
              : message}
          </p>
        ) : null}
      </div>

      <div className="panel ebk-global-push-runs">
        <h4>最新运行日志</h4>
        <p className="hint-text">含定时推送、手动推送与每日 09:30 昨日快照轮次（读取前一日经营+排名落库）。</p>
        {runRows.length === 0 ? (
          <p className="hint-text">暂无运行记录。</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>触发</th>
                <th>状态</th>
                <th>平台结果</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {runRows.map((row) => (
                <tr key={row.key}>
                  <td>{row.time}</td>
                  <td>{row.triggerLabel}</td>
                  <td>{row.statusLabel}</td>
                  <td>{row.platformResultsText}</td>
                  <td>{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
