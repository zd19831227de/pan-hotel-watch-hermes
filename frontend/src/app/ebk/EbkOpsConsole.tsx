import { useEffect, useState, type ComponentProps } from "react";
import { Plus, Save, Store } from "lucide-react";

import { CtripEbkDashboard } from "./CtripEbkDashboard";
import { FliggyEbkDashboard } from "./FliggyEbkDashboard";
import { GlobalPushPanel } from "./GlobalPushPanel";
import { MeituanEbkDashboard } from "./MeituanEbkDashboard";
import { type Shop } from "../../services/shopsApi";
import {
  patchDataCenterGroup,
  type DataCenterWatchGroup,
} from "../../services/ctripEbookingWatchApi";
import {
  createEbkPlatformWatchApi,
  type EbkPlatformWatchGroup,
} from "../../services/ebkPlatformWatchApi";
import { EmptyState } from "../ui/opsPrimitives";

type ShopPlatform = "ctrip" | "meituan" | "fliggy";

type Branch =
  | { kind: "shop"; shopKey: string; platform: ShopPlatform }
  | { kind: "global" }
  | { kind: "unassigned" };

const SHOP_PLATFORMS: { key: ShopPlatform; label: string }[] = [
  { key: "ctrip", label: "携程 EBK" },
  { key: "meituan", label: "美团 EBK" },
  { key: "fliggy", label: "飞猪 EBK" },
];

const PLATFORM_LABELS: Record<ShopPlatform, string> = {
  ctrip: "携程",
  meituan: "美团",
  fliggy: "飞猪",
};

export function EbkOpsConsole(props: {
  ctrip: ComponentProps<typeof CtripEbkDashboard>;
  meituan: ComponentProps<typeof MeituanEbkDashboard>;
  fliggy: ComponentProps<typeof FliggyEbkDashboard>;
  shops: Shop[];
  dataCenterGroups: DataCenterWatchGroup[];
  onCreateCtripGroupForShop?: (shop: Shop) => void;
  onRefreshDataCenterGroups?: () => void;
}) {
  // 一级分支默认第一个店铺的携程；无店铺时全局。branchOverride 仅在操作员点击后生效。
  const defaultBranch: Branch = props.shops.length
    ? { kind: "shop", shopKey: props.shops[0].shop_key, platform: "ctrip" }
    : { kind: "global" };
  const [branchOverride, setBranchOverride] = useState<Branch | null>(null);
  const branch = branchOverride ?? defaultBranch;
  const activeShop = branch.kind === "shop"
    ? props.shops.find((shop) => shop.shop_key === branch.shopKey) ?? null
    : null;

  const selectShop = (shopKey: string) => {
    const platform = branch.kind === "shop" ? branch.platform : "ctrip";
    setBranchOverride({ kind: "shop", shopKey, platform });
  };

  // 携程：店内只看本店监控组；App 选中键优先，否则店内第一个。
  const scopedCtripGroups = branch.kind === "shop"
    ? props.dataCenterGroups.filter((group) => group.shop_key === branch.shopKey)
    : [];
  const scopedCtripGroup =
    scopedCtripGroups.find((group) => group.group_key === props.ctrip.selectedKey)
    ?? scopedCtripGroups[0]
    ?? null;

  // App 的 latest/datasets 跟随其 selectedKey 加载；店内解析出的组若不同步，
  // 走既有 onSelectGroup 让 App 换装该组数据，防止展示他店数据。
  const ctripSyncKey = branch.kind === "shop" && branch.platform === "ctrip" && scopedCtripGroup
    && scopedCtripGroup.group_key !== props.ctrip.selectedKey
    ? scopedCtripGroup.group_key
    : null;
  useEffect(() => {
    if (ctripSyncKey) {
      props.ctrip.onSelectGroup(ctripSyncKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctripSyncKey]);

  const scopedMeituanSessions = branch.kind === "shop"
    ? props.meituan.sessions.filter(
        (session) => session.platform === "meituan"
          && session.intended_use === "ebk"
          && session.shop_key === branch.shopKey,
      )
    : [];
  const scopedMeituanAccountId = scopedMeituanSessions.some(
    (session) => session.platform_account_id === props.meituan.selectedAccountId,
  )
    ? props.meituan.selectedAccountId
    : "";

  const scopedFliggySessions = branch.kind === "shop"
    ? props.fliggy.sessions.filter(
        (session) => session.platform === "fliggy"
          && session.intended_use === "ebk"
          && session.shop_key === branch.shopKey,
      )
    : [];
  const scopedFliggyAccountId = scopedFliggySessions.some(
    (session) => session.platform_account_id === props.fliggy.selectedAccountId,
  )
    ? props.fliggy.selectedAccountId
    : "";

  // 未分组分支：美团/飞猪组在 console 内拉取；携程组复用 props.dataCenterGroups。
  const [meituanGroups, setMeituanGroups] = useState<EbkPlatformWatchGroup[]>([]);
  const [fliggyGroups, setFliggyGroups] = useState<EbkPlatformWatchGroup[]>([]);
  const [unassignedReloadKey, setUnassignedReloadKey] = useState(0);
  const [unassignedBusy, setUnassignedBusy] = useState<string | null>(null);
  const [unassignedHint, setUnassignedHint] = useState<string | null>(null);
  const [bindShopKeys, setBindShopKeys] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [meituan, fliggy] = await Promise.all([
        createEbkPlatformWatchApi("meituan").listWatchGroups().catch(() => [] as EbkPlatformWatchGroup[]),
        createEbkPlatformWatchApi("fliggy").listWatchGroups().catch(() => [] as EbkPlatformWatchGroup[]),
      ]);
      if (!cancelled) {
        setMeituanGroups(meituan);
        setFliggyGroups(fliggy);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [unassignedReloadKey]);

  const unassignedRows: { platform: ShopPlatform; groupKey: string; name: string }[] = [
    ...props.dataCenterGroups
      .filter((group) => !group.shop_key)
      .map((group) => ({ platform: "ctrip" as const, groupKey: group.group_key, name: group.name })),
    ...meituanGroups
      .filter((group) => !group.shop_key)
      .map((group) => ({ platform: "meituan" as const, groupKey: group.group_key, name: group.name })),
    ...fliggyGroups
      .filter((group) => !group.shop_key)
      .map((group) => ({ platform: "fliggy" as const, groupKey: group.group_key, name: group.name })),
  ];

  const bindGroupToShop = async (platform: ShopPlatform, groupKey: string) => {
    const shopKey = bindShopKeys[groupKey] ?? "";
    if (!shopKey) {
      setUnassignedHint("请先选择归属店铺。");
      return;
    }
    setUnassignedBusy(groupKey);
    setUnassignedHint(null);
    try {
      if (platform === "ctrip") {
        await patchDataCenterGroup(groupKey, { shop_key: shopKey });
        props.onRefreshDataCenterGroups?.();
      } else {
        await createEbkPlatformWatchApi(platform).patchWatchGroup(groupKey, { shop_key: shopKey });
      }
      setBindShopKeys((current) => {
        const next = { ...current };
        delete next[groupKey];
        return next;
      });
      setUnassignedReloadKey((current) => current + 1);
      setUnassignedHint("已归属店铺；该组已移入对应店铺分支。");
    } catch (error) {
      setUnassignedHint(error instanceof Error ? error.message : "归属店铺失败。");
    } finally {
      setUnassignedBusy(null);
    }
  };

  return (
    <section className="workspace ebk-ops-console">
      <div className="section-head">
        <div>
          <h2>EBK 操作台</h2>
          <p>先选店铺，再看店内各平台登录与读取状态；全局与未分组单独成支。前端不提供写价入口。</p>
        </div>
      </div>

      <div className="ebk-ops-platform-tabs" role="tablist" aria-label="店铺切换">
        {props.shops.map((shop) => (
          <button
            key={shop.shop_key}
            type="button"
            role="tab"
            aria-selected={branch.kind === "shop" && branch.shopKey === shop.shop_key}
            className={
              branch.kind === "shop" && branch.shopKey === shop.shop_key
                ? "ebk-ops-platform-tab active"
                : "ebk-ops-platform-tab"
            }
            onClick={() => selectShop(shop.shop_key)}
          >
            <Store size={14} /> {shop.name}
          </button>
        ))}
        <button
          type="button"
          role="tab"
          aria-selected={branch.kind === "global"}
          className={branch.kind === "global" ? "ebk-ops-platform-tab active" : "ebk-ops-platform-tab"}
          onClick={() => setBranchOverride({ kind: "global" })}
        >
          全局定时推送
        </button>
        {unassignedRows.length ? (
          <button
            type="button"
            role="tab"
            aria-selected={branch.kind === "unassigned"}
            className={
              branch.kind === "unassigned"
                ? "ebk-ops-platform-tab active"
                : "ebk-ops-platform-tab"
            }
            onClick={() => setBranchOverride({ kind: "unassigned" })}
          >
            未分组监控组({unassignedRows.length})
          </button>
        ) : null}
      </div>

      {branch.kind === "shop" ? (
        <div className="ebk-ops-platform-tabs ebk-ops-sublevel-tabs" role="tablist" aria-label="平台切换">
          {SHOP_PLATFORMS.map((platform) => (
            <button
              key={platform.key}
              type="button"
              role="tab"
              aria-selected={branch.platform === platform.key}
              className={
                branch.platform === platform.key
                  ? "ebk-ops-platform-tab active"
                  : "ebk-ops-platform-tab"
              }
              onClick={() =>
                setBranchOverride({ kind: "shop", shopKey: branch.shopKey, platform: platform.key })
              }
            >
              {platform.label}
            </button>
          ))}
        </div>
      ) : null}

      {branch.kind === "shop" && branch.platform === "ctrip" ? (
        scopedCtripGroups.length ? (
          ctripSyncKey ? (
            // App 数据仍属他店组（含换装失败滞留），渲染占位不透传，堵跨店窗口
            <div className="panel ebk-ops-placeholder">
              <EmptyState text="正在载入该店铺的携程数据…" />
            </div>
          ) : (
            <CtripEbkDashboard
              {...props.ctrip}
              groups={scopedCtripGroups}
              group={scopedCtripGroup}
              selectedKey={scopedCtripGroup?.group_key ?? ""}
            />
          )
        ) : (
          <div className="panel ebk-ops-placeholder">
            <EmptyState
              text={`该店铺尚未接入携程平台${activeShop ? `（${activeShop.name}）` : ""}；接入后即可配置读取与推送。`}
            />
            <div className="action-strip">
              <button
                className="primary-button"
                type="button"
                disabled={!props.onCreateCtripGroupForShop}
                title={props.onCreateCtripGroupForShop ? undefined : "请在「账号与浏览器」完成店铺配置后接入"}
                onClick={() => activeShop && props.onCreateCtripGroupForShop?.(activeShop)}
              >
                <Plus size={16} /> 接入携程
              </button>
            </div>
          </div>
        )
      ) : null}

      {branch.kind === "shop" && branch.platform === "meituan" ? (
        <MeituanEbkDashboard
          {...props.meituan}
          sessions={scopedMeituanSessions}
          selectedAccountId={scopedMeituanAccountId}
          snapshot={scopedMeituanAccountId ? props.meituan.snapshot : null}
          shopKey={branch.shopKey}
        />
      ) : null}

      {branch.kind === "shop" && branch.platform === "fliggy" ? (
        <FliggyEbkDashboard
          {...props.fliggy}
          sessions={scopedFliggySessions}
          selectedAccountId={scopedFliggyAccountId}
          snapshot={scopedFliggyAccountId ? props.fliggy.snapshot : null}
          shopKey={branch.shopKey}
        />
      ) : null}

      {branch.kind === "global" ? (
        <GlobalPushPanel shops={props.shops} dataCenterGroups={props.dataCenterGroups} />
      ) : null}

      {branch.kind === "unassigned" ? (
        <div className="panel ebk-ops-unassigned">
          <div className="panel-title-row">
            <div>
              <h3>未分组监控组</h3>
              <p className="field-hint">
                三平台尚未归属店铺的旧监控组；选择归属店铺保存后，组会自动移入对应店铺分支。
              </p>
            </div>
          </div>
          {!unassignedRows.length ? (
            <EmptyState text="暂无未分组监控组。" />
          ) : (
            <div className="table-list">
              {unassignedRows.map((row) => (
                <div className="account-row" key={`${row.platform}-${row.groupKey}`}>
                  <div>
                    <span className="status-chip">{PLATFORM_LABELS[row.platform]}</span>
                    <strong>{row.name}</strong>
                    <span className="field-hint">{row.groupKey}</span>
                  </div>
                  <div className="inline-edit-row">
                    <select
                      value={bindShopKeys[row.groupKey] ?? ""}
                      onChange={(event) =>
                        setBindShopKeys((current) => ({
                          ...current,
                          [row.groupKey]: event.target.value,
                        }))
                      }
                      disabled={unassignedBusy !== null}
                    >
                      <option value="">选择归属店铺…</option>
                      {props.shops.map((shop) => (
                        <option key={shop.shop_key} value={shop.shop_key}>{shop.name}</option>
                      ))}
                    </select>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={unassignedBusy !== null}
                      onClick={() => void bindGroupToShop(row.platform, row.groupKey)}
                    >
                      <Save size={16} /> 归入店铺
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {unassignedHint ? <p className="field-hint">{unassignedHint}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
