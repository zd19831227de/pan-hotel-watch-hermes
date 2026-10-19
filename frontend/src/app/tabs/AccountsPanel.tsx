import { Building2, ChevronDown, ChevronRight, Eye, Plus, Save, ShieldCheck, Store, Trash2, X } from "lucide-react";
import { useState } from "react";

import {
  canBindToCtripCompetitorWatch,
  canBindToCtripDataCenter,
  defaultAccountFormForPlatform,
  isLlmChatPlatform,
  isReadonlyEbkPlatform,
  platformLabel,
  type AccountPlatform,
} from "../accountPlatformModel.ts";
import { buildAccountTree, type ShopPlatform } from "../accountTreeModel.ts";
import {
  type BrowserConnectionMode,
  type BrowserIntendedUse,
  type BrowserSession,
} from "../../services/browserSessionsApi";
import type { DataCenterWatchGroup } from "../../services/ctripEbookingWatchApi";
import type { CompetitorWatchGroup } from "../../services/competitorWatchApi";
import type { Shop } from "../../services/shopsApi";
import { connectionModeLabel, EmptyState, operatorAction, statusLabel } from "../ui/opsPrimitives";

export type AccountForm = {
  platform_account_id: string;
  display_name: string;
  browser_session_id: string;
  session_port: string;
  platform: AccountPlatform;
  intended_use: BrowserIntendedUse;
  connection_mode: BrowserConnectionMode;
  shop_key: string;
};

function boundGroupKey(
  groups: { group_key: string; platform_account_id: string; browser_session_id?: string | null }[],
  session: BrowserSession,
): string {
  return (
    groups.find((group) => group.browser_session_id === session.browser_session_id)?.group_key ??
    groups.find((group) => group.platform_account_id === session.platform_account_id)?.group_key ??
    ""
  );
}

export function AccountsPanel(props: {
  sessions: BrowserSession[];
  shops: Shop[];
  form: AccountForm;
  busy: string | null;
  dataCenterGroups: DataCenterWatchGroup[];
  competitorGroups: CompetitorWatchGroup[];
  onFormChange: (form: AccountForm) => void;
  onCreate: () => void;
  onCreateShop: (name: string) => void;
  onOpenLogin: (session: BrowserSession, loginContext: BrowserIntendedUse) => void;
  onCheckMeituanLogin: (session: BrowserSession) => void;
  onCheckFliggyLogin: (session: BrowserSession) => void;
  onBindDataCenter: (session: BrowserSession, groupKey: string) => void;
  onBindCompetitor: (session: BrowserSession, groupKey: string) => void;
  onPatchSession: (
    session: BrowserSession,
    input: {
      pricing_feishu_chat_id?: string | null;
      connection_mode?: BrowserConnectionMode;
      shop_key?: string;
    },
  ) => void;
  onDelete: (session: BrowserSession) => void;
}) {
  const [pricingChatDrafts, setPricingChatDrafts] = useState<Record<string, string>>({});
  const [connectionModeDrafts, setConnectionModeDrafts] = useState<Record<string, BrowserConnectionMode>>({});
  const [ebkGroupDrafts, setEbkGroupDrafts] = useState<Record<string, string>>({});
  const [competitorGroupDrafts, setCompetitorGroupDrafts] = useState<Record<string, string>>({});
  const [shopAssignDrafts, setShopAssignDrafts] = useState<Record<string, string>>({});
  const [newShopName, setNewShopName] = useState("");
  // 节点折叠态：竞品区默认折叠（§3「折叠；可多个」），店铺/未分组默认展开。
  const [collapsedNodes, setCollapsedNodes] = useState<Record<string, boolean>>({ "node:competitor": true });
  const toggleNode = (nodeKey: string) => {
    setCollapsedNodes((current) => ({ ...current, [nodeKey]: !current[nodeKey] }));
  };
  const nodeCollapsed = (nodeKey: string) => Boolean(collapsedNodes[nodeKey]);
  // 修正1：豆包/千问账号移入知识模块，业务面板只展示业务账号。
  const sessions = props.sessions.filter((session) => !isLlmChatPlatform(session.platform));
  const tree = buildAccountTree(props.shops, sessions);
  const setPricingChatDraft = (sessionId: string, value: string) => {
    setPricingChatDrafts((current) => ({ ...current, [sessionId]: value }));
  };
  const clearPricingChatDraft = (sessionId: string) => {
    setPricingChatDrafts((current) => {
      const next = { ...current };
      delete next[sessionId];
      return next;
    });
  };
  const isCompetitorForm = props.form.intended_use === "competitor_price";
  const createDisabled =
    props.busy === "create-session" || (!isCompetitorForm && !props.form.shop_key);

  const prefillCompetitorAccount = () => {
    const defaults = defaultAccountFormForPlatform("ctrip");
    props.onFormChange({
      ...props.form,
      platform: "ctrip",
      intended_use: "competitor_price",
      connection_mode: defaults.connection_mode,
      session_port: defaults.session_port,
      shop_key: "",
    });
  };
  const prefillShopAccount = (shop: Shop) => {
    props.onFormChange({ ...props.form, shop_key: shop.shop_key });
  };
  const prefillShopPlatform = (shop: Shop, platform: ShopPlatform) => {
    const defaults = defaultAccountFormForPlatform(platform);
    props.onFormChange({
      ...props.form,
      platform: defaults.platform,
      intended_use: "ebk",
      connection_mode: defaults.connection_mode,
      session_port: defaults.session_port,
      shop_key: shop.shop_key,
    });
  };

  const renderAccountRow = (session: BrowserSession, options?: { assignableToShop?: boolean }) => {
    const isMeituan = session.platform === "meituan";
    const isFliggy = session.platform === "fliggy";
    const canBindDataCenter = canBindToCtripDataCenter(session);
    const canBindCompetitor = canBindToCtripCompetitorWatch(session);
    const openLoginTitle = isFliggy
      ? "打开飞猪 EBK 登录"
      : isMeituan
        ? "打开美团 EBK 登录"
        : session.intended_use === "competitor_price"
          ? "打开携程官网登录"
          : "打开 EBK 登录";
    const shopAssignDraft = shopAssignDrafts[session.browser_session_id] ?? "";
    return (
      <div className="account-row" key={session.browser_session_id}>
        <div>
          <strong>{session.display_name}</strong>
          {canBindDataCenter ? (
            <span>手动调价指令群：{session.pricing_feishu_chat_id_configured ? `已绑定 ${session.pricing_feishu_chat_id_masked ?? ""}` : "未绑定"}</span>
          ) : null}
          {canBindCompetitor && session.competitor_pool_usage?.length ? (
            <span>
              用于竞对账号池：
              {session.competitor_pool_usage
                .map((usage) => usage.watch_group_name || usage.watch_group_key)
                .join("、")}
            </span>
          ) : null}
          <span>{session.platform_account_id} · {session.browser_session_id} · 端口 {session.session_port}</span>
          <span>{connectionModeLabel(session.connection_mode)} · {session.can_auto_write_price ? "允许审批后写价能力：是" : "允许审批后写价能力：否"}</span>
          <span>{statusLabel(session.login_state)} · {operatorAction(session.last_action)}</span>
          <div className="inline-edit-row">
            <select
              aria-label={`${session.display_name} 账号类型`}
              value={connectionModeDrafts[session.browser_session_id] ?? session.connection_mode}
              onChange={(event) => {
                const connectionMode = event.target.value as BrowserConnectionMode;
                setConnectionModeDrafts((current) => ({
                  ...current,
                  [session.browser_session_id]: connectionMode,
                }));
              }}
            >
              <option value="unknown">未确认</option>
              <option value="direct">直连</option>
              <option value="non_direct">非直连</option>
            </select>
            <button
              className="secondary-button"
              type="button"
              disabled={
                props.busy === `patch-session-${session.browser_session_id}`
                  || (connectionModeDrafts[session.browser_session_id] ?? session.connection_mode) === session.connection_mode
              }
              onClick={() => {
                const connectionMode = connectionModeDrafts[session.browser_session_id] ?? session.connection_mode;
                if (
                  connectionMode === "non_direct"
                  && !window.confirm("非直连账号在通过飞书审批后才允许自动化改价，请确认该账号真实为非直连")
                ) {
                  return;
                }
                props.onPatchSession(session, { connection_mode: connectionMode });
              }}
            >
              <Save size={16} /> 保存账号类型
            </button>
          </div>
          {canBindDataCenter ? (
            <div className="inline-edit-row">
              <input
                value={pricingChatDrafts[session.browser_session_id] ?? ""}
                placeholder={session.pricing_feishu_chat_id_configured ? "粘贴新的手动调价指令群 ID 覆盖" : "oc_ 开头的飞书群 ID"}
                onChange={(event) => setPricingChatDraft(session.browser_session_id, event.target.value)}
              />
              <button
                className="secondary-button"
                type="button"
                disabled={!pricingChatDrafts[session.browser_session_id]?.trim() || props.busy === `patch-session-${session.browser_session_id}`}
                onClick={() => {
                  props.onPatchSession(session, { pricing_feishu_chat_id: pricingChatDrafts[session.browser_session_id].trim() });
                  clearPricingChatDraft(session.browser_session_id);
                }}
              >
                <Save size={16} /> 保存指令群
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!session.pricing_feishu_chat_id_configured || props.busy === `patch-session-${session.browser_session_id}`}
                onClick={() => {
                  props.onPatchSession(session, { pricing_feishu_chat_id: "" });
                  clearPricingChatDraft(session.browser_session_id);
                }}
              >
                <X size={16} /> 清除
              </button>
            </div>
          ) : null}
          {canBindDataCenter ? (
            <span className="field-hint">
              手动调价指令群只用于解析“把今天某房型调到 420”这类人工指令对应哪个 EBK 调价账号；不要把手动调价指令群当作经营问答绑定。
            </span>
          ) : null}
          {canBindDataCenter ? (
            <div className="inline-edit-row">
              <select
                aria-label={`${session.display_name} EBK 监控组绑定`}
                value={ebkGroupDrafts[session.browser_session_id] ?? boundGroupKey(props.dataCenterGroups, session)}
                onChange={(event) =>
                  setEbkGroupDrafts((current) => ({
                    ...current,
                    [session.browser_session_id]: event.target.value,
                  }))
                }
              >
                <option value="">选择 EBK 监控组…</option>
                {props.dataCenterGroups.map((group) => (
                  <option value={group.group_key} key={group.group_key}>{group.name}</option>
                ))}
              </select>
              <button
                className="secondary-button"
                type="button"
                disabled={
                  !(ebkGroupDrafts[session.browser_session_id] ?? boundGroupKey(props.dataCenterGroups, session)) ||
                  props.busy === `bind-data-center-${session.browser_session_id}`
                }
                onClick={() =>
                  props.onBindDataCenter(
                    session,
                    ebkGroupDrafts[session.browser_session_id] ??
                      boundGroupKey(props.dataCenterGroups, session),
                  )
                }
              >
                <Save size={16} /> 绑定为该组的 EBK 账号
              </button>
            </div>
          ) : null}
          {canBindDataCenter && !props.dataCenterGroups.length ? (
            <span className="field-hint">还没有 EBK 监控组；先到「EBK 操作台」创建监控组，再回来绑定。</span>
          ) : null}
          {canBindCompetitor ? (
            <div className="inline-edit-row">
              <select
                aria-label={`${session.display_name} 竞品监控组绑定`}
                value={competitorGroupDrafts[session.browser_session_id] ?? boundGroupKey(props.competitorGroups, session)}
                onChange={(event) =>
                  setCompetitorGroupDrafts((current) => ({
                    ...current,
                    [session.browser_session_id]: event.target.value,
                  }))
                }
              >
                <option value="">选择竞品监控组…</option>
                {props.competitorGroups.map((group) => (
                  <option value={group.group_key} key={group.group_key}>{group.name}</option>
                ))}
              </select>
              <button
                className="secondary-button"
                type="button"
                disabled={
                  !(competitorGroupDrafts[session.browser_session_id] ?? boundGroupKey(props.competitorGroups, session)) ||
                  props.busy === `bind-competitor-${session.browser_session_id}`
                }
                onClick={() =>
                  props.onBindCompetitor(
                    session,
                    competitorGroupDrafts[session.browser_session_id] ??
                      boundGroupKey(props.competitorGroups, session),
                  )
                }
              >
                <Save size={16} /> 绑定为该组的竞品账号
              </button>
            </div>
          ) : null}
          {canBindCompetitor && !props.competitorGroups.length ? (
            <span className="field-hint">还没有竞品监控组；先到「竞品价格」创建监控组，再回来绑定。</span>
          ) : null}
          {options?.assignableToShop ? (
            props.shops.length ? (
              <div className="inline-edit-row">
                <select
                  aria-label={`${session.display_name} 归入店铺`}
                  value={shopAssignDraft}
                  onChange={(event) =>
                    setShopAssignDrafts((current) => ({
                      ...current,
                      [session.browser_session_id]: event.target.value,
                    }))
                  }
                >
                  <option value="">选择店铺…</option>
                  {props.shops.map((shop) => (
                    <option value={shop.shop_key} key={shop.shop_key}>{shop.name}</option>
                  ))}
                </select>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!shopAssignDraft || props.busy === `patch-session-${session.browser_session_id}`}
                  onClick={() =>
                    props.onPatchSession(session, { shop_key: shopAssignDraft })
                  }
                >
                  <Store size={16} /> 归入店铺
                </button>
              </div>
            ) : (
              <span className="field-hint">暂无店铺可归入；请先在上方新建店铺。</span>
            )
          ) : null}
        </div>
        <div className="row-actions">
          <button type="button" onClick={() => props.onOpenLogin(session, session.intended_use)} title={openLoginTitle}>
            {session.intended_use === "competitor_price" ? <Building2 size={16} /> : <Eye size={16} />}
            {isMeituan ? <span className="sr-only">打开美团 EBK 登录</span> : null}
            {isFliggy ? <span className="sr-only">打开飞猪 EBK 登录</span> : null}
          </button>
          {isMeituan ? (
            <button
              type="button"
              onClick={() => props.onCheckMeituanLogin(session)}
              title="确认登录态"
              disabled={props.busy === `check-meituan-login-${session.browser_session_id}`}
            >
              <ShieldCheck size={16} />
              <span className="sr-only">确认登录态</span>
            </button>
          ) : null}
          {isFliggy ? (
            <button
              type="button"
              onClick={() => props.onCheckFliggyLogin(session)}
              title="确认登录态"
              disabled={props.busy === `check-fliggy-login-${session.browser_session_id}`}
            >
              <ShieldCheck size={16} />
              <span className="sr-only">确认登录态</span>
            </button>
          ) : null}
          <button type="button" onClick={() => props.onDelete(session)} title="删除账号"><Trash2 size={16} /></button>
        </div>
      </div>
    );
  };

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>账号与浏览器</h2>
          <p>按店铺组织登录账号：竞品账号全局共用，店铺下按平台管理 EBK 登录。登录、验证码、短信、二维码、风控都由人工在浏览器里处理。</p>
        </div>
      </div>
      <div className="split-grid">
        <div className="panel">
          <h3>新增账号</h3>
          <div className="form-grid">
            <label>
              平台
              <select
                value={props.form.platform}
                onChange={(event) => {
                  const platform = event.target.value as AccountPlatform;
                  const defaults = defaultAccountFormForPlatform(platform);
                  props.onFormChange({
                    ...props.form,
                    platform: defaults.platform,
                    intended_use: defaults.intended_use,
                    connection_mode: defaults.connection_mode,
                    session_port: defaults.session_port,
                    shop_key: defaults.intended_use === "competitor_price" ? "" : props.form.shop_key,
                  });
                }}
              >
                <option value="ctrip">携程</option>
                <option value="meituan">美团</option>
                <option value="fliggy">飞猪</option>
              </select>
            </label>
            <label>
              店铺归属
              <select
                value={isCompetitorForm ? "" : props.form.shop_key}
                disabled={isCompetitorForm}
                onChange={(event) => props.onFormChange({ ...props.form, shop_key: event.target.value })}
              >
                {isCompetitorForm ? (
                  <option value="">竞品账号（不属店铺）</option>
                ) : (
                  <>
                    <option value="">选择店铺…</option>
                    {props.shops.map((shop) => (
                      <option value={shop.shop_key} key={shop.shop_key}>{shop.name}</option>
                    ))}
                  </>
                )}
              </select>
              {isCompetitorForm ? (
                <span className="field-hint">竞品账号固定归属全局「竞品酒店」区，不选店铺。</span>
              ) : !props.shops.length ? (
                <span className="field-hint">还没有店铺；请先在右侧「已绑定账号」顶部新建店铺。</span>
              ) : !props.form.shop_key ? (
                <span className="field-hint">EBK 账号必须归属一个店铺。</span>
              ) : null}
            </label>
            <label>
              平台账号 ID
              <input
                value={props.form.platform_account_id}
                onChange={(event) => props.onFormChange({ ...props.form, platform_account_id: event.target.value })}
                placeholder={
                  props.form.platform === "meituan"
                    ? "meituan_account_01"
                    : props.form.platform === "fliggy"
                      ? "fliggy_account_01"
                      : "ctrip_account_01"
                }
              />
            </label>
            <label>
              显示名称
              <input
                value={props.form.display_name}
                onChange={(event) => props.onFormChange({ ...props.form, display_name: event.target.value })}
                placeholder={
                  props.form.platform === "meituan"
                    ? "前台美团账号"
                    : props.form.platform === "fliggy"
                      ? "前台飞猪账号"
                      : "前台携程账号"
                }
              />
            </label>
            <label>
              浏览器会话 ID
              <input value={props.form.browser_session_id} onChange={(event) => props.onFormChange({ ...props.form, browser_session_id: event.target.value })} placeholder="留空自动生成" />
            </label>
            <label>
              调试端口
              <input value={props.form.session_port} onChange={(event) => props.onFormChange({ ...props.form, session_port: event.target.value })} inputMode="numeric" />
            </label>
            <label>
              用途
              <select
                value={props.form.intended_use}
                disabled={isReadonlyEbkPlatform(props.form.platform)}
                onChange={(event) => {
                  const intendedUse = event.target.value as BrowserIntendedUse;
                  props.onFormChange({
                    ...props.form,
                    intended_use: intendedUse,
                    shop_key: intendedUse === "competitor_price" ? "" : props.form.shop_key,
                  });
                }}
              >
                <option value="ebk">EBK 后台</option>
                {props.form.platform === "ctrip" ? <option value="competitor_price">竞品价格</option> : null}
              </select>
              {isReadonlyEbkPlatform(props.form.platform) ? (
                <span className="field-hint">
                  {props.form.platform === "fliggy"
                    ? "飞猪本期仅支持 EBK 后台登录态，不参与竞品价格读取。"
                    : "美团本期仅支持 EBK 后台登录态，不参与竞品价格读取。"}
                </span>
              ) : null}
            </label>
            <label>
              账号类型
              <select
                value={props.form.connection_mode}
                onChange={(event) => props.onFormChange({ ...props.form, connection_mode: event.target.value as BrowserConnectionMode })}
              >
                <option value="unknown">未确认：按直连处理，不允许自动改价</option>
                <option value="direct">直连账号：只生成调价建议，不允许自动改价</option>
                <option value="non_direct">非直连账号：经 Feishu 审批后可继续调价流程</option>
              </select>
              <span className="field-hint">
                三平台均允许配置为非直连；实际可写状态以“允许审批后写价能力”为准（平台写价能力按真实账号联机验证进度上线）。
              </span>
            </label>
          </div>
          <button className="primary-button" type="button" disabled={createDisabled} onClick={props.onCreate}>
            <Plus size={16} /> 新增浏览器账号
          </button>
        </div>

        <div className="panel">
          <h3>已绑定账号</h3>
          <div className="inline-edit-row shop-create-row">
            <input
              value={newShopName}
              placeholder="新店铺名称，如：静里公寓"
              onChange={(event) => setNewShopName(event.target.value)}
            />
            <button
              className="secondary-button"
              type="button"
              disabled={!newShopName.trim()}
              onClick={() => {
                props.onCreateShop(newShopName.trim());
                setNewShopName("");
              }}
            >
              <Plus size={16} /> 新建店铺
            </button>
          </div>
          <div className="account-tree">
            <section className="tree-node tree-node-competitor">
              <header className="tree-node-head">
                <button
                  className="tree-node-toggle"
                  type="button"
                  aria-expanded={!nodeCollapsed("node:competitor")}
                  onClick={() => toggleNode("node:competitor")}
                >
                  {nodeCollapsed("node:competitor") ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                  <Building2 size={16} /> 竞品酒店
                  <span className="tree-node-count">{tree.competitorAccounts.length} 个账号</span>
                </button>
                <button className="secondary-button" type="button" onClick={prefillCompetitorAccount}>
                  <Plus size={16} /> 添加竞品账号
                </button>
              </header>
              {!nodeCollapsed("node:competitor") ? (
                <>
                  <p className="tree-node-note">竞品账号全局共用，不归属任何店铺。</p>
                  <div className="tree-children table-list">
                    {tree.competitorAccounts.map((session) => renderAccountRow(session))}
                    {!tree.competitorAccounts.length ? (
                      <EmptyState text="还没有竞品账号。竞品账号只能从这里添加，不属任何店铺。" />
                    ) : null}
                  </div>
                </>
              ) : null}
            </section>

            {tree.shopNodes.map((node) => {
              const shopAccountCount = node.platforms.reduce((sum, slot) => sum + slot.sessions.length, 0);
              const nodeKey = `node:shop:${node.shop.shop_key}`;
              return (
                <section className="tree-node tree-node-shop" key={node.shop.shop_key}>
                  <header className="tree-node-head">
                    <button
                      className="tree-node-toggle"
                      type="button"
                      aria-expanded={!nodeCollapsed(nodeKey)}
                      onClick={() => toggleNode(nodeKey)}
                    >
                      {nodeCollapsed(nodeKey) ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                      <Store size={16} /> {node.shop.name}
                      <span className="tree-node-count">
                        {shopAccountCount} 个账号 · {node.platforms.filter((slot) => slot.sessions.length).length}/3 平台
                      </span>
                    </button>
                    <button className="secondary-button" type="button" onClick={() => prefillShopAccount(node.shop)}>
                      <Plus size={16} /> 添加账号
                    </button>
                  </header>
                  {!nodeCollapsed(nodeKey) ? (
                    <>
                      <div className="tree-group">
                        <h4>登录账号</h4>
                        <div className="tree-platform-list">
                          {node.platforms.map((slot) => (
                            <div className="tree-platform" key={slot.platform}>
                              <span className="tree-platform-label">{platformLabel(slot.platform)} EBK</span>
                              <div className="tree-platform-sessions">
                                {slot.sessions.map((session) => renderAccountRow(session))}
                                {!slot.sessions.length ? (
                                  <button
                                    className="secondary-button tree-platform-add"
                                    type="button"
                                    onClick={() => prefillShopPlatform(node.shop, slot.platform)}
                                  >
                                    <Plus size={16} /> 接入{platformLabel(slot.platform)}
                                  </button>
                                ) : null}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                      <div className="tree-group">
                        <h4>监控接入状态</h4>
                        <span className="tree-watch-hint">监控组店铺归属即将上线，届时在此展示本店各平台监控接入状态。</span>
                      </div>
                    </>
                  ) : null}
                </section>
              );
            })}

            {!props.shops.length ? (
              <EmptyState text="还没有店铺。新建店铺后，即可把未分组账号归入店铺，或在店铺下添加账号。" />
            ) : null}

            {tree.unassignedSessions.length ? (
              <section className="tree-node tree-node-unassigned">
                <header className="tree-node-head">
                  <button
                    className="tree-node-toggle"
                    type="button"
                    aria-expanded={!nodeCollapsed("node:unassigned")}
                    onClick={() => toggleNode("node:unassigned")}
                  >
                    {nodeCollapsed("node:unassigned") ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                    未分组
                    <span className="tree-node-count">{tree.unassignedSessions.length} 个账号待归属</span>
                  </button>
                </header>
                {!nodeCollapsed("node:unassigned") ? (
                  <>
                    <p className="tree-node-note">以下旧账号尚未归属店铺；归入店铺后会自动出现在对应店铺下。</p>
                    <div className="tree-children table-list">
                      {tree.unassignedSessions.map((session) => renderAccountRow(session, { assignableToShop: true }))}
                    </div>
                  </>
                ) : null}
              </section>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
