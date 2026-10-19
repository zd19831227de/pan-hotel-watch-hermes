import { useEffect, useState } from "react";
import { MessageSquare, RefreshCw, Save, Store, Trash2, Upload, Wand2 } from "lucide-react";

import {
  autoReplyModeHint,
  categoryLabel,
  defaultReviewConfigForm,
  emptyTemplateForm,
  platformLabel,
  platformMountLabel,
  reviewConfigFormFromConfig,
  templateFormFromTemplate,
  templatePayloadFromForm,
  templateSourceLabel,
  type AutoReplyMode,
  type ReviewCategory,
  type ReviewConfigForm,
  type ReviewPlatform,
  type TemplateForm,
} from "./reviewManagementModel";
import { EmptyState } from "./ui/opsPrimitives";
import type {
  ReplyTemplate,
  ReviewAutoConfig,
  ReviewCatalog,
  ReviewGroup,
  ReviewTask,
} from "../services/reviewsApi";

const PLATFORMS: ReviewPlatform[] = ["ctrip", "meituan", "fliggy"];
const CATEGORIES: ReviewCategory[] = ["good", "medium", "bad"];

export function ReviewManagementPanel(props: {
  platform: ReviewPlatform;
  config: ReviewAutoConfig | null;
  catalog: ReviewCatalog | null;
  templates: ReplyTemplate[];
  tasks: ReviewTask[];
  busy: string | null;
  groups: ReviewGroup[];
  groupKey: string;
  onGroupChange: (groupKey: string) => void;
  onPlatformChange: (platform: ReviewPlatform) => void;
  onConfigSave: (form: ReviewConfigForm) => void;
  onFetch: () => void;
  onImportHistory: () => void;
  onAutoUpdateTemplates: () => void;
  onProcessPending: () => void;
  onTemplateSave: (form: TemplateForm) => void;
  onTemplateDelete: (templateId: number) => void;
}) {
  const [configForm, setConfigForm] = useState<ReviewConfigForm>(
    reviewConfigFormFromConfig(props.config),
  );
  useEffect(() => {
    setConfigForm(reviewConfigFormFromConfig(props.config));
  }, [props.config]);

  const [templateCategory, setTemplateCategory] = useState<ReviewCategory>("good");
  const [templateForm, setTemplateForm] = useState<TemplateForm>(
    emptyTemplateForm(props.platform, "good"),
  );
  useEffect(() => {
    // 平台切换：正在编辑的模板属于旧平台，改写 platform 会在保存时撞后端
    // 「模板平台不一致」守卫（422），这里直接重置为空白表单。
    setTemplateForm(emptyTemplateForm(props.platform, templateCategory));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.platform]);

  const adapterReady = props.catalog?.adapter_ready_platforms?.includes(props.platform) ?? false;
  // 一级=店铺（shop_key 非空）+未分组；未分组=未归属店铺的旧携程组，可继续选用。
  const shopGroups = props.groups.filter((g) => g.shop_key);
  const legacyGroups = props.groups.filter((g) => !g.shop_key);
  const selectedGroup = props.groups.find((g) => g.group_key === props.groupKey);
  const inLegacy = legacyGroups.some((g) => g.group_key === props.groupKey);
  const platformMount = selectedGroup?.platforms?.[props.platform];
  const mounted = platformMount?.mounted ?? false;
  const feishuConfigured = selectedGroup?.feishu_chat_id_configured ?? false;
  const categoryTemplates = props.templates.filter(
    (template) => template.category === templateCategory,
  );

  return (
    <section className="workspace" aria-label="评价管理">
      {!props.groups.length ? (
        <div className="panel">
          <EmptyState text="暂无店铺，请先在「账号与浏览器」新建店铺并绑定账号" />
        </div>
      ) : (
        <>
          <div className="ebk-ops-platform-tabs" role="tablist" aria-label="评价店铺切换">
            {shopGroups.map((group) => (
              <button
                key={group.group_key}
                type="button"
                role="tab"
                aria-selected={props.groupKey === group.group_key}
                className={
                  props.groupKey === group.group_key
                    ? "ebk-ops-platform-tab active"
                    : "ebk-ops-platform-tab"
                }
                onClick={() => props.onGroupChange(group.group_key)}
              >
                <Store size={14} /> {group.name}
              </button>
            ))}
            {legacyGroups.length ? (
              <button
                type="button"
                role="tab"
                aria-selected={inLegacy}
                className={inLegacy ? "ebk-ops-platform-tab active" : "ebk-ops-platform-tab"}
                onClick={() => props.onGroupChange(legacyGroups[0].group_key)}
              >
                未分组监控组({legacyGroups.length})
              </button>
            ) : null}
          </div>

          {inLegacy ? (
            <div className="panel">
              <div className="panel-title-row">
                <div>
                  <h3>未分组监控组</h3>
                  <p className="field-hint">
                    以下旧携程评价组未归属店铺；在组配置里补绑归属店铺后会自动归入对应店铺。为兼容旧配置，仍可选中使用。
                  </p>
                </div>
              </div>
              <div className="table-list">
                {legacyGroups.map((group) => (
                  <div className="account-row" key={group.group_key}>
                    <div>
                      <strong>{group.name}</strong>
                      <span className="field-hint">{group.group_key}</span>
                    </div>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={props.groupKey === group.group_key}
                      onClick={() => props.onGroupChange(group.group_key)}
                    >
                      {props.groupKey === group.group_key ? "当前使用中" : "使用该组"}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </>
      )}

      <div className="ebk-ops-platform-tabs ebk-ops-sublevel-tabs" role="tablist" aria-label="评价平台切换">
        {PLATFORMS.map((platform) => (
          <button
            key={platform}
            type="button"
            role="tab"
            aria-selected={props.platform === platform}
            className={
              props.platform === platform ? "ebk-ops-platform-tab active" : "ebk-ops-platform-tab"
            }
            onClick={() => props.onPlatformChange(platform)}
          >
            {platformLabel(platform)}评价
            <span className="status-chip">
              {selectedGroup?.platforms?.[platform]?.mounted ? "已绑定" : "未绑定"}
            </span>
          </button>
        ))}
      </div>

      <div className="panel">
        <div className="panel-title-row">
          <h3>自动回复设置</h3>
          <MessageSquare size={18} />
        </div>
        <p className="field-hint">
          接口状态：{adapterReady ? "已接入" : "评价接口未接入（等待逆向），抓取与提交暂不可用"}
          {props.config?.last_fetched_at ? ` · 上次抓取 ${props.config.last_fetched_at}` : ""}
        </p>
        <div className="form-grid">
          <label>
            当前子平台
            <span className="field-hint">{platformMountLabel(mounted, props.platform)}</span>
          </label>
        </div>
        <div className="form-grid">
          <label>
            自动回复模式
            <select
              value={configForm.auto_reply_mode}
              onChange={(event) =>
                setConfigForm({ ...configForm, auto_reply_mode: event.target.value as AutoReplyMode })
              }
            >
              <option value="off">关闭自动回复</option>
              <option value="semi_auto" disabled={!feishuConfigured}>
                半自动（数量提醒）{!feishuConfigured ? "·店铺未配飞书群" : ""}
              </option>
              <option value="full_auto">全自动回复</option>
            </select>
            <span className="field-hint">{autoReplyModeHint(configForm.auto_reply_mode)}</span>
          </label>
          <label>
            推送飞书群（提醒/统计卡）
            <span className="field-hint">
              {feishuConfigured
                ? "从店铺的携程配置继承，无需手填（请在店铺的携程配置里设置该群）。半自动到点推「N 条待回复」提醒卡（含好/中/差明细）；全自动到点推「自动回复完成」统计卡。"
                : "该店铺的携程配置未设置飞书群，半自动提醒卡与全自动统计卡无法推送。"}
            </span>
          </label>
          <label>
            定时处理时间（每日）
            <input
              type="time"
              value={configForm.auto_process_time}
              onChange={(event) =>
                setConfigForm({ ...configForm, auto_process_time: event.target.value })
              }
            />
            <span className="field-hint">
              每日到点自动抓取并按当前模式处理（设置方式同 EBK 定时推送时间）；错过到点 30 分钟内会重试，超时不补跑。
            </span>
          </label>
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={configForm.auto_fetch_enabled}
              onChange={(event) =>
                setConfigForm({ ...configForm, auto_fetch_enabled: event.target.checked })
              }
            />
            启用定时处理（关闭后不自动运行，仍可手动抓取/处理）
          </label>
        </div>
        <div className="action-strip">
          <button
            className="primary-button"
            type="button"
            disabled={props.busy === "review-save-config" || !mounted}
            onClick={() => props.onConfigSave(configForm)}
          >
            <Save size={16} /> 保存设置
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={props.busy === "review-fetch" || !mounted}
            onClick={props.onFetch}
          >
            <RefreshCw size={16} /> 立即抓取
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={props.busy === "review-process-pending" || !mounted}
            onClick={props.onProcessPending}
          >
            处理待回复（按当前模式）
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title-row">
          <h3>回复模板</h3>
        </div>
        <div className="action-strip">
          {CATEGORIES.map((category) => (
            <button
              key={category}
              type="button"
              className={
                templateCategory === category ? "ebk-ops-platform-tab active" : "ebk-ops-platform-tab"
              }
              onClick={() => {
                setTemplateCategory(category);
                setTemplateForm(emptyTemplateForm(props.platform, category));
              }}
            >
              {categoryLabel(category)}
            </button>
          ))}
          <button
            className="secondary-button"
            type="button"
            disabled={props.busy === "review-import-history" || !mounted}
            onClick={props.onImportHistory}
          >
            <Upload size={16} /> 导入历史回复生成模板
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={props.busy === "review-auto-update" || !mounted}
            title={
              mounted
                ? "抓取该店铺本子平台已回复评价（好/中/差），AI 整理后覆盖更新模板"
                : "请先把该平台账号归入店铺"
            }
            onClick={props.onAutoUpdateTemplates}
          >
            <Wand2 size={16} /> 自动更新评价
          </button>
        </div>
        <p className="field-hint">
          模板按评分归类（好/中/差），内容支持 {"{user_nickname}"} 占位符；带分数区间的模板优先于通用模板。
          模板归属当前店铺与本子平台。
        </p>
        <ul className="table-list">
          {categoryTemplates.length === 0 ? (
            <li>
              <p className="empty">
                暂无{categoryLabel(templateCategory)}模板。可点击「导入历史回复生成模板」从已回复评价自动整理。
              </p>
            </li>
          ) : (
            categoryTemplates.map((template) => (
              <li key={template.id} className="review-item">
                <div className="review-item-head">
                  <strong>{template.name}</strong>
                  <span className="status-chip">
                    {template.score_range?.min !== undefined && template.score_range?.min !== null
                      ? `${template.score_range.min}-${template.score_range?.max ?? 5} 分`
                      : "通用"}
                  </span>
                  <span className="status-chip">{template.enabled ? "启用" : "停用"}</span>
                  {!template.browser_session_id ? (
                    <span className="status-chip">平台通用</span>
                  ) : null}
                  <span className="field-hint">
                    {templateSourceLabel(template.source)} · 优先级 {template.priority}
                  </span>
                  <span className="inline-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => setTemplateForm(templateFormFromTemplate(template))}
                    >
                      编辑
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={props.busy === `review-template-delete-${template.id}`}
                      onClick={() => props.onTemplateDelete(template.id)}
                    >
                      <Trash2 size={14} /> 删除
                    </button>
                  </span>
                </div>
                <p className="review-item-content">{template.content}</p>
              </li>
            ))
          )}
        </ul>

        <div className="panel-subsection">
          <div className="panel-title-row">
            <h4>{templateForm.id === null ? "新增模板" : `编辑模板 #${templateForm.id}`}</h4>
          </div>
          <div className="form-grid">
            <label>
              分类
              <select
                value={templateForm.category}
                onChange={(event) =>
                  setTemplateForm({ ...templateForm, category: event.target.value as ReviewCategory })
                }
              >
                {CATEGORIES.map((category) => (
                  <option key={category} value={category}>
                    {categoryLabel(category)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              模板名称
              <input
                value={templateForm.name}
                onChange={(event) => setTemplateForm({ ...templateForm, name: event.target.value })}
              />
            </label>
            <label>
              优先级（小者优先）
              <input
                value={templateForm.priority}
                inputMode="numeric"
                onChange={(event) =>
                  setTemplateForm({ ...templateForm, priority: event.target.value })
                }
              />
            </label>
            <label>
              分数区间下限（可空）
              <input
                value={templateForm.score_min}
                inputMode="decimal"
                placeholder="如 4"
                onChange={(event) =>
                  setTemplateForm({ ...templateForm, score_min: event.target.value })
                }
              />
            </label>
            <label>
              分数区间上限（可空）
              <input
                value={templateForm.score_max}
                inputMode="decimal"
                placeholder="如 5"
                onChange={(event) =>
                  setTemplateForm({ ...templateForm, score_max: event.target.value })
                }
              />
            </label>
          </div>
          <div className="form-field-block">
            <textarea
              rows={3}
              value={templateForm.content}
              placeholder="模板内容，支持 {user_nickname} 占位符"
              onChange={(event) => setTemplateForm({ ...templateForm, content: event.target.value })}
            />
          </div>
          <div className="action-strip">
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={templateForm.enabled}
                onChange={(event) =>
                  setTemplateForm({ ...templateForm, enabled: event.target.checked })
                }
              />
              启用模板
            </label>
            <button
              className="primary-button"
              type="button"
              disabled={props.busy === "review-template-save"}
              onClick={() => props.onTemplateSave(templateForm)}
            >
              <Save size={16} /> 保存模板
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => setTemplateForm(emptyTemplateForm(props.platform, templateCategory))}
            >
              清空重填
            </button>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title-row">
          <h3>任务记录</h3>
        </div>
        {props.tasks.length === 0 ? (
          <p className="empty">暂无任务记录。</p>
        ) : (
          <ul className="table-list">
            {props.tasks.slice(0, 5).map((task) => (
              <li key={task.id} className="review-item-head">
                <span>
                  {task.task_type === "fetch" ? "抓取" : task.task_type === "import" ? "导入" : "生成"}
                  {" · "}
                  {task.status === "completed" ? "完成" : task.status === "failed" ? "失败" : "进行中"}
                </span>
                <span className="field-hint">{task.error_msg || task.created_at || ""}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export { defaultReviewConfigForm, templatePayloadFromForm };
