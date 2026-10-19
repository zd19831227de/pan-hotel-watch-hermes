import {
  Activity,
  ArrowLeft,
  Bell,
  BookOpen,
  Building2,
  CalendarDays,
  ChevronRight,
  Clock,
  Database,
  Download,
  FileCode2,
  FileText,
  KeyRound,
  Loader2,
  MonitorCheck,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings,
  ShieldCheck,
  Trash2,
  Upload,
  X,
  BrainCircuit,
  Folder,
  History,
  MessageSquare,
  BarChart3,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CompetitorTrendChart } from "./CompetitorTrendChart";
import { AdvancedAgentConfigPanel, type AdvancedAgentConfigPanelProps } from "./AdvancedAgentConfigPanel";
import { BrowserCachePanel } from "./BrowserCachePanel";
import { buildSkillExportMarkdown } from "./skillExport.ts";
import { prepareRagDocumentFromFile } from "./ragFileImport.ts";
import {
  NEW_FOLDER_CHOICE,
  UNFILED_FOLDER,
  normalizeFolderName,
  resolveFolderChoice,
} from "./ragFolders";
import { LlmConfigConsole } from "./LlmConfigConsole";
import { PriceChangeAlertPanel, strategyPatchFromForm } from "./PriceChangeAlertPanel";
import { ReviewManagementPanel } from "./ReviewManagementPanel";
import {
  autoUpdateResultText,
  autoUpdateTemplatesConfirmText,
  platformLabel,
  reviewConfigFormError,
  reviewConfigPatchFromForm,
  templatePayloadFromForm,
  type ReviewConfigForm,
  type ReviewPlatform,
  type TemplateForm,
} from "./reviewManagementModel";
import { AccountsPanel, type AccountForm } from "./tabs/AccountsPanel";
import { DistillPanel } from "./DistillPanel.tsx";
import { HistoryPanel } from "./HistoryPanel.tsx";
import { BiDailyPanel } from "./bi/BiDailyPanel.tsx";
import { PageHealthPanel } from "./PageHealthPanel.tsx";
import { EbkOpsConsole } from "./ebk/EbkOpsConsole";
import {
  connectionModeLabel,
  EmptyState,
  formatTime,
  Metric,
  ResultNotice,
  statusLabel,
} from "./ui/opsPrimitives";
import { buildCtripBusinessData } from "./ctripBusinessDataModel";
import { dataCenterRunNowTimeoutMs } from "./ebk/dataCenterRunControl";
import {
  competitorGroupPatchFromForm,
  defaultHotelForm,
  groupFormFromWatchGroup,
  hotelFormFromHotel,
  nextGroupKeyAfterDelete,
  resetHotelFormForGroup,
  type CompetitorGroupForm,
  type CompetitorHotelForm,
} from "./competitorWatchForms";
import {
  calendarAggregateStatus,
  canStartPolling,
  canStopPolling,
  competitorAggregateStatus,
  globalPushStripStatus,
  manualCompetitorRunButtonText,
  pollingStatusText,
  priceChangeAggregateStatus,
  schedulerDetailText,
  startPollingButtonText,
  stopPollingButtonText,
} from "./pollingStatus";
import { strategyFormFromConfig, type PricingStrategyForm } from "./priceChangeAlertModel";
import {
  calendarAlertLabel,
  calendarConfigFormFromConfig,
  calendarConfigPatchFromForm,
  calendarDateStatusLabel,
  calendarMatrixSummary,
  calendarPriceDisplay,
  calendarScheduleModeLabel,
  addMatrixBriefPushTime,
  matrixBriefStatusLabel,
  matrixBriefTimesLabel,
  removeMatrixBriefPushTime,
  defaultCalendarPriceConfigForm,
  type CalendarPriceConfigForm,
} from "./calendarPriceWatchModel";
import {
  canBindToCtripCompetitorWatch,
  canBindToCtripDataCenter,
  defaultAccountFormForPlatform,
} from "./accountPlatformModel.ts";
import {
  createBrowserSession,
  deleteBrowserSession,
  listBrowserSessions,
  patchBrowserSession,
  type BrowserIntendedUse,
  type BrowserSession,
} from "../services/browserSessionsApi";
import { createShop, listShops, type Shop } from "../services/shopsApi";
import {
  createCompetitorGroup,
  deleteCompetitorGroup,
  deleteCompetitorHotel,
  getCompetitorPriceTrend,
  getLatestCompetitorSnapshot,
  listCompetitorGroups,
  listCompetitorHotels,
  patchCompetitorGroup,
  patchCompetitorHotel,
  putCompetitorBrowserPool,
  runCompetitorNow,
  upsertCompetitorHotel,
  type CompetitorHotel,
  type CompetitorLatestSnapshot,
  type CompetitorPriceTrend,
  type CompetitorWatchGroup,
} from "../services/competitorWatchApi";
import {
  getIntegrationConfig,
  createFeishuRoleAssignment,
  getSupervisorOverview,
  listLlmModels,
  patchFeishuAgentProfile,
  patchFeishuChatAgentBinding,
  patchFeishuRoleAssignment,
  testLlmConnection,
  updateIntegrationConfig,
  createSkill,
  deleteSkill,
  getSkill,
  listSkills,
  updateSkill,
  createRagDocument,
  createRagFolder,
  deleteRagDocument,
  deleteRagFolder,
  getEmbeddingConfig,
  getRagDocument,
  listRagDocuments,
  listRagFolders,
  renameRagFolder,
  searchRag,
  testEmbeddingConfig,
  updateEmbeddingConfig,
  updateRagDocument,
  type EmbeddingConfig,
  type EmbeddingConnectionTestResult,
  type IntegrationConfigSnapshot,
  createFeishuAgentProfile,
  setFeishuProfileCredentials,
  deleteFeishuAgentProfile,
  createFeishuChatAgentBinding,
  deleteFeishuChatAgentBinding,
  type FeishuChatAgentBindingPatch,
  type RagDocument,
  type RagDocumentDetail,
  type RagFolderSummary,
  type RagSearchResult,
  type SkillDetail,
  type SkillSummary,
  type SupervisorOverview,
  type LlmConnectionTestResult,
  type LlmModelsResult,
} from "../services/configApi";
import { openCtripLogin } from "../services/ctripEbookingApi";
import { checkMeituanLoginState, openMeituanLogin } from "../services/meituanEbookingApi.ts";
import { checkFliggyLoginState, openFliggyLogin } from "../services/fliggyEbookingApi.ts";
import { exportCompetitorPriceTrendCsv } from "../services/exportsApi.ts";
import {
  createDataCenterGroup,
  deleteDataCenterGroup,
  getLatestDataCenterRun,
  listDataCenterDatasets,
  listDataCenterGroups,
  patchDataCenterDataset,
  patchDataCenterGroup,
  runDailyBriefNow,
  runDataCenterNow,
  type DataCenterDataset,
  type DataCenterLatestSnapshot,
  type DataCenterRun,
  type DataCenterWatchGroup,
} from "../services/ctripEbookingWatchApi";
import { getRuntimeHealth, type RuntimeHealth } from "../services/opsHealthApi";
import { setRuntimeApiKey } from "../services/http";
import {
  getLatestEbkSnapshot,
  readAllEbkSnapshots,
  type EbkSnapshotRecord,
} from "../services/ebkSnapshotsApi";
import {
  evaluatePriceChangeAlert,
  getLatestPriceChangeAlert,
  getPricingStrategyConfig,
  patchPricingStrategyConfig,
  type PriceChangeAlert,
  type PricingStrategyConfig,
} from "../services/priceChangeAlertsApi";
import {
  getCalendarPriceConfig,
  getCalendarSchedulerStatus,
  getLatestCalendarPriceMatrix,
  listCalendarConfigSummaries,
  listCalendarPriceAlerts,
  patchCalendarPriceConfig,
  pushCalendarMatrixBriefNow,
  runCalendarPriceNow,
  type CalendarConfigSummary,
  type CalendarPriceAlert,
  type CalendarPriceMatrix,
  type CalendarPriceWatchConfig,
  type CalendarSchedulerStatus,
} from "../services/calendarPriceWatchApi";
import {
  getEbkGlobalPushSchedulerStatus,
  listEbkGlobalPushConfigs,
  type EbkGlobalPushConfig,
} from "../services/ebkGlobalPushApi";
import {
  autoUpdateStoreTemplates,
  deleteTemplate,
  fetchReviewsNow,
  getAutoConfig,
  importHistory,
  listReviewGroups,
  listReviews,
  listTasks,
  listTemplates,
  processPending,
  saveTemplate,
  updateAutoConfig,
  type ReplyTemplate,
  type ReviewAutoConfig,
  type ReviewCatalog,
  type ReviewGroup,
  type ReviewTask,
} from "../services/reviewsApi";

type TabKey = "accounts" | "ebk_ops" | "competitor" | "calendar_prices" | "price_alerts" | "review_management" | "history" | "bi_daily";
type AdvancedMaintenanceView = "llm_config" | "knowledge";

type Notice = {
  tone: "success" | "error" | "info";
  text: string;
};

type IntegrationConfigForm = {
  llm_provider: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
};

const defaultAccountForm: AccountForm = {
  platform_account_id: "",
  display_name: "",
  browser_session_id: "",
  shop_key: "",
  ...defaultAccountFormForPlatform("ctrip"),
};

const defaultIntegrationConfigForm: IntegrationConfigForm = {
  llm_provider: "deepseek",
  llm_base_url: "https://api.deepseek.com",
  llm_model: "deepseek-v4-flash",
  llm_api_key: "",
};

export function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("ebk_ops");
  const [advancedMaintenanceOpen, setAdvancedMaintenanceOpen] = useState(false);
  const [advancedAgentConfigOpen, setAdvancedAgentConfigOpen] = useState(false);
  const [pageHealthOpen, setPageHealthOpen] = useState(false);
  const [advancedMaintenanceView, setAdvancedMaintenanceView] =
    useState<AdvancedMaintenanceView>("llm_config");
  const [supervisorApiKey, setSupervisorApiKey] = useState("");
  const [supervisorOverview, setSupervisorOverview] = useState<SupervisorOverview | null>(null);
  const [supervisorOverviewError, setSupervisorOverviewError] = useState<string | null>(null);
  // 高级配置在页面底部，全局 notice 在页面顶部；操作反馈必须就地可见，
  // 否则运营在面板里点了按钮毫无回音（2026-09-11 反馈「加了没反应」）。
  const [advancedNotice, setAdvancedNotice] = useState<Notice | null>(null);
  const [admissionRevision, setAdmissionRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);

  const [sessions, setSessions] = useState<BrowserSession[]>([]);
  const [shops, setShops] = useState<Shop[]>([]);
  const [accountForm, setAccountForm] = useState<AccountForm>(defaultAccountForm);

  const [dataCenterGroups, setDataCenterGroups] = useState<DataCenterWatchGroup[]>([]);
  const [selectedDataCenterKey, setSelectedDataCenterKeyState] = useState<string>("");
  const selectedDataCenterKeyRef = useRef("");
  const [dataCenterDatasets, setDataCenterDatasets] = useState<DataCenterDataset[]>([]);
  const [dataCenterLatest, setDataCenterLatest] = useState<DataCenterRun | null>(null);
  const [dataCenterLatestSnapshot, setDataCenterLatestSnapshot] =
    useState<DataCenterLatestSnapshot | null>(null);
  const [meituanAccountId, setMeituanAccountId] = useState("");
  const [meituanSnapshot, setMeituanSnapshot] = useState<EbkSnapshotRecord | null>(null);
  const [meituanLoadError, setMeituanLoadError] = useState<string | null>(null);
  const [fliggyAccountId, setFliggyAccountId] = useState("");
  const [fliggySnapshot, setFliggySnapshot] = useState<EbkSnapshotRecord | null>(null);
  const [fliggyLoadError, setFliggyLoadError] = useState<string | null>(null);

  const [competitorGroups, setCompetitorGroups] = useState<CompetitorWatchGroup[]>([]);
  const [selectedCompetitorKey, setSelectedCompetitorKey] = useState<string>("");
  const [competitorHotels, setCompetitorHotels] = useState<CompetitorHotel[]>([]);
  const [competitorLatest, setCompetitorLatest] = useState<CompetitorLatestSnapshot | null>(null);
  const [competitorTrend, setCompetitorTrend] = useState<CompetitorPriceTrend | null>(null);
  const [trendDays, setTrendDays] = useState(1);
  const [trendGranularity, setTrendGranularity] = useState<"run" | "hour" | "day">("run");
  const trendRangeRef = useRef<{ days: number; granularity: "run" | "hour" | "day" }>({ days: 1, granularity: "run" });

  const applyTrendRange = (days: number, granularity: "run" | "hour" | "day") => {
    trendRangeRef.current = { days, granularity };
    setTrendDays(days);
    setTrendGranularity(granularity);
    const groupKey = selectedCompetitorKey;
    if (!groupKey) {
      return;
    }
    void (async () => {
      const includeDisabled = showDisabledCompetitorHotels;
      const trend = await getCompetitorPriceTrend(groupKey, { includeDisabled, days, granularity }).catch(() => null);
      setCompetitorTrend(trend);
    })();
  };
  const [priceChangeStrategy, setPriceChangeStrategy] = useState<PricingStrategyConfig | null>(null);
  const [priceChangeAlert, setPriceChangeAlert] = useState<PriceChangeAlert | null>(null);
  const [priceChangeForm, setPriceChangeForm] = useState<PricingStrategyForm | null>(null);
  const [integrationConfig, setIntegrationConfig] = useState<IntegrationConfigSnapshot | null>(null);
  const [integrationConfigForm, setIntegrationConfigForm] = useState<IntegrationConfigForm>(
    defaultIntegrationConfigForm,
  );
  const [llmConnectionTest, setLlmConnectionTest] = useState<LlmConnectionTestResult | null>(null);
  const [llmModels, setLlmModels] = useState<string[]>([]);
  const [llmModelsResult, setLlmModelsResult] = useState<LlmModelsResult | null>(null);
  const [groupForm, setGroupForm] = useState<CompetitorGroupForm>(groupFormFromWatchGroup(null));
  const [hotelForm, setHotelForm] = useState<CompetitorHotelForm>(defaultHotelForm);
  const [editingHotelId, setEditingHotelId] = useState<number | null>(null);
  const [showDisabledCompetitorHotels, setShowDisabledCompetitorHotels] = useState(false);
  const [calendarConfig, setCalendarConfig] = useState<CalendarPriceWatchConfig | null>(null);
  const [calendarConfigForm, setCalendarConfigForm] = useState<CalendarPriceConfigForm>(
    defaultCalendarPriceConfigForm,
  );
  const [calendarMatrix, setCalendarMatrix] = useState<CalendarPriceMatrix | null>(null);
  const [calendarAlerts, setCalendarAlerts] = useState<CalendarPriceAlert[]>([]);
  const [calendarSchedulerStatus, setCalendarSchedulerStatus] = useState<CalendarSchedulerStatus | null>(
    null,
  );
  const [calendarConfigSummaries, setCalendarConfigSummaries] = useState<CalendarConfigSummary[] | null>(null);
  const [globalPushConfigs, setGlobalPushConfigs] = useState<EbkGlobalPushConfig[] | null>(null);
  const [globalPushScheduler, setGlobalPushScheduler] = useState<{ enabled: boolean; status: string; last_heartbeat_at: string | null } | null>(null);
  const [, setCalendarStripLoaded] = useState(false);
  const dataCenterRequestRef = useRef<AbortController | null>(null);
  const competitorRequestRef = useRef<AbortController | null>(null);
  const calendarStripRequestRef = useRef<AbortController | null>(null);
  const calendarGroupRequestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      dataCenterRequestRef.current?.abort();
      competitorRequestRef.current?.abort();
      calendarStripRequestRef.current?.abort();
      calendarGroupRequestRef.current?.abort();
    };
  }, []);

  const dataCenterGroup = useMemo(
    () => dataCenterGroups.find((item) => item.group_key === selectedDataCenterKey) ?? dataCenterGroups[0] ?? null,
    [dataCenterGroups, selectedDataCenterKey],
  );
  const selectedCompetitorGroup = useMemo(
    () => competitorGroups.find((item) => item.group_key === selectedCompetitorKey) ?? competitorGroups[0] ?? null,
    [competitorGroups, selectedCompetitorKey],
  );

  // 顶部状态条跨组聚合：不再只反映当前选中的组，避免“切组才显示开启”的误导。
  const competitorStrip = useMemo(
    () => competitorAggregateStatus(competitorGroups),
    [competitorGroups],
  );
  const calendarStrip = useMemo(
    () => calendarAggregateStatus(calendarConfigSummaries, calendarSchedulerStatus),
    [calendarConfigSummaries, calendarSchedulerStatus],
  );
  const priceChangeStrip = useMemo(
    () =>
      priceChangeAggregateStatus(
        priceChangeStrategy?.enabled,
        competitorGroups.find(
          (group) => group.group_key === priceChangeStrategy?.watch_group_key,
        )?.name ?? null,
        competitorGroups,
      ),
    [priceChangeStrategy?.enabled, priceChangeStrategy?.watch_group_key, competitorGroups],
  );
  const globalPushStrip = useMemo(
    () => globalPushStripStatus(globalPushConfigs, globalPushScheduler),
    [globalPushConfigs, globalPushScheduler],
  );

  const setSelectedDataCenterKey = useCallback((groupKey: string) => {
    selectedDataCenterKeyRef.current = groupKey;
    setSelectedDataCenterKeyState(groupKey);
  }, []);

  const refreshDataCenterGroup = useCallback(async (groupKey: string) => {
    dataCenterRequestRef.current?.abort();
    const controller = new AbortController();
    dataCenterRequestRef.current = controller;
    try {
      const [datasets, latest] = await Promise.all([
        listDataCenterDatasets(groupKey, controller.signal),
        getLatestDataCenterRun(groupKey, controller.signal).catch(() => null),
      ]);
      if (controller.signal.aborted) {
        return;
      }
      setSelectedDataCenterKey(groupKey);
      setDataCenterDatasets(datasets);
      // latest 拉取失败时保留旧显示，不静默置空；真无 run 时后端返回 run=null 的完整对象。
      if (latest) {
        setDataCenterLatest(latest.run ?? null);
        setDataCenterLatestSnapshot(latest);
      }
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      throw error;
    } finally {
      if (dataCenterRequestRef.current === controller) {
        dataCenterRequestRef.current = null;
      }
    }
  }, [setSelectedDataCenterKey]);

  // EBK 操作台打开时的 30s 只读轮询：worker 完成读取后页面能自动跟上；
  // 隐藏/切组/卸载即停，不与手动刷新重叠，永不触发 run-now。
  useEffect(() => {
    if (activeTab !== "ebk_ops" || !selectedDataCenterKey) {
      return;
    }
    const groupKey = selectedDataCenterKey;
    const poll = () => {
      if (document.visibilityState !== "visible" || busy || dataCenterRequestRef.current) {
        return;
      }
      void refreshDataCenterGroup(groupKey).catch(() => {
        // 轮询失败保留旧值，不打断操作员。
      });
    };
    const timer = window.setInterval(poll, 30_000);
    const onWake = () => {
      if (document.visibilityState === "visible") {
        poll();
      }
    };
    document.addEventListener("visibilitychange", onWake);
    window.addEventListener("focus", onWake);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onWake);
      window.removeEventListener("focus", onWake);
    };
  }, [activeTab, selectedDataCenterKey, busy, refreshDataCenterGroup]);

  const refreshCalendarStripStatus = useCallback(async (groupKey: string) => {
    calendarStripRequestRef.current?.abort();
    const controller = new AbortController();
    calendarStripRequestRef.current = controller;
    try {
      const [config, scheduler] = await Promise.all([
        getCalendarPriceConfig(groupKey, controller.signal).catch(() => null),
        getCalendarSchedulerStatus(controller.signal).catch(() => null),
      ]);
      if (controller.signal.aborted) {
        return;
      }
      setCalendarConfig(config);
      if (config) {
        setCalendarConfigForm(calendarConfigFormFromConfig(config));
      }
      setCalendarSchedulerStatus(scheduler);
      setCalendarStripLoaded(true);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      throw error;
    } finally {
      if (calendarStripRequestRef.current === controller) {
        calendarStripRequestRef.current = null;
      }
    }
  }, []);

  const syncCompetitorGroupState = useCallback(
    async (
      groupKey: string,
      groups: CompetitorWatchGroup[],
      options?: { resetHotelForm?: boolean; includeDisabled?: boolean },
    ) => {
      competitorRequestRef.current?.abort();
      calendarStripRequestRef.current?.abort();
      const controller = new AbortController();
      competitorRequestRef.current = controller;
      const includeDisabled = options?.includeDisabled ?? false;
      try {
        const [hotels, latest, trend, alert] = await Promise.all([
          listCompetitorHotels(groupKey, { includeDisabled, signal: controller.signal }),
          getLatestCompetitorSnapshot(groupKey, { includeDisabled, signal: controller.signal }).catch(
            () => null,
          ),
          getCompetitorPriceTrend(groupKey, {
            includeDisabled,
            signal: controller.signal,
            days: trendRangeRef.current.days,
            granularity: trendRangeRef.current.granularity,
          }).catch(() => null),
          getLatestPriceChangeAlert(groupKey, controller.signal).catch(() => null),
        ]);
        if (controller.signal.aborted) {
          return;
        }
        const currentGroup = groups.find((item) => item.group_key === groupKey) ?? null;
        setSelectedCompetitorKey(groupKey);
        setShowDisabledCompetitorHotels(includeDisabled);
        setCompetitorHotels(hotels);
        setCompetitorLatest(latest);
        setCompetitorTrend(trend);
        setPriceChangeAlert(alert);
        if (currentGroup) {
          setGroupForm(groupFormFromWatchGroup(currentGroup));
        }
        if (options?.resetHotelForm ?? true) {
          setHotelForm(resetHotelFormForGroup());
          setEditingHotelId(null);
        }
        await refreshCalendarStripStatus(groupKey);
      } catch (error) {
        if (isAbortError(error)) {
          return;
        }
        throw error;
      } finally {
        if (competitorRequestRef.current === controller) {
          competitorRequestRef.current = null;
        }
      }
    },
    [refreshCalendarStripStatus],
  );

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [
        nextHealth,
        nextSessions,
        nextShops,
        nextDataCenterGroups,
        nextCompetitorGroups,
        nextStrategy,
        nextIntegrationConfig,
        nextCalendarConfigSummaries,
        nextGlobalPushConfigs,
        nextGlobalPushScheduler,
      ] =
        await Promise.all([
          getRuntimeHealth().catch(() => null),
          listBrowserSessions(),
          listShops().catch(() => []),
          listDataCenterGroups(),
          listCompetitorGroups(),
          getPricingStrategyConfig().catch(() => null),
          getIntegrationConfig().catch(() => null),
          listCalendarConfigSummaries().catch(() => []),
          listEbkGlobalPushConfigs().catch(() => null),
          getEbkGlobalPushSchedulerStatus().catch(() => null),
        ]);
      setHealth(nextHealth);
      setSessions(nextSessions);
      setShops(nextShops);
      setDataCenterGroups(nextDataCenterGroups);
      setCompetitorGroups(nextCompetitorGroups);
      setPriceChangeStrategy(nextStrategy);
      setCalendarConfigSummaries(nextCalendarConfigSummaries);
      setGlobalPushConfigs(nextGlobalPushConfigs);
      setGlobalPushScheduler(nextGlobalPushScheduler);
      if (nextIntegrationConfig) {
        setIntegrationConfig(nextIntegrationConfig);
        setIntegrationConfigForm(integrationConfigFormFromSnapshot(nextIntegrationConfig));
      }
      const currentDataCenterKey = selectedDataCenterKeyRef.current;
      const nextDataCenterGroup =
        nextDataCenterGroups.find((item) => item.group_key === currentDataCenterKey) ??
        nextDataCenterGroups[0];
      if (nextDataCenterGroup) {
        await refreshDataCenterGroup(nextDataCenterGroup.group_key);
      } else {
        setSelectedDataCenterKey("");
        setDataCenterDatasets([]);
        setDataCenterLatest(null);
        setDataCenterLatestSnapshot(null);
      }
      const nextCompetitorGroup =
        nextCompetitorGroups.find((item) => item.group_key === selectedCompetitorKey) ??
        nextCompetitorGroups[0];
      if (nextCompetitorGroup) {
        await syncCompetitorGroupState(nextCompetitorGroup.group_key, nextCompetitorGroups);
      } else {
        setCompetitorHotels([]);
        setCompetitorLatest(null);
        setCompetitorTrend(null);
        setPriceChangeAlert(null);
        setGroupForm(groupFormFromWatchGroup(null));
        setHotelForm(resetHotelFormForGroup());
        setEditingHotelId(null);
      }
      setNotice(null);
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      setLoading(false);
    }
  }, [refreshDataCenterGroup, selectedCompetitorKey, syncCompetitorGroupState]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const refreshCompetitor = useCallback(
    async (
      groupKey: string,
      options?: { resetHotelForm?: boolean; includeDisabled?: boolean },
    ) => {
      const includeDisabled = options?.includeDisabled ?? showDisabledCompetitorHotels;
      const [hotels, latest, trend, alert] = await Promise.all([
        listCompetitorHotels(groupKey, { includeDisabled }),
        getLatestCompetitorSnapshot(groupKey, { includeDisabled }).catch(() => null),
        getCompetitorPriceTrend(groupKey, {
          includeDisabled,
          days: trendRangeRef.current.days,
          granularity: trendRangeRef.current.granularity,
        }).catch(() => null),
        getLatestPriceChangeAlert(groupKey).catch(() => null),
      ]);
      setCompetitorHotels(hotels);
      setCompetitorLatest(latest);
      setCompetitorTrend(trend);
      setPriceChangeAlert(alert);
      if (options?.resetHotelForm) {
        setHotelForm(resetHotelFormForGroup());
        setEditingHotelId(null);
      }
    },
    [showDisabledCompetitorHotels],
  );

  const syncCalendarGroupState = useCallback(async (groupKey: string) => {
    calendarGroupRequestRef.current?.abort();
    const controller = new AbortController();
    calendarGroupRequestRef.current = controller;
    try {
      const [config, matrix, alerts, scheduler] = await Promise.all([
        getCalendarPriceConfig(groupKey, controller.signal).catch(() => null),
        getLatestCalendarPriceMatrix(groupKey, controller.signal).catch(() => null),
        listCalendarPriceAlerts(groupKey, controller.signal).catch(
          () => [] as CalendarPriceAlert[],
        ),
        getCalendarSchedulerStatus(controller.signal).catch(() => null),
      ]);
      if (controller.signal.aborted) {
        return;
      }
      setSelectedCompetitorKey(groupKey);
      if (config) {
        setCalendarConfig(config);
        setCalendarConfigForm(calendarConfigFormFromConfig(config));
      } else {
        setCalendarConfig(null);
        setCalendarConfigForm(defaultCalendarPriceConfigForm);
      }
      setCalendarMatrix(matrix);
      setCalendarAlerts(alerts);
      setCalendarSchedulerStatus(scheduler);
      setCalendarStripLoaded(true);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      throw error;
    } finally {
      if (calendarGroupRequestRef.current === controller) {
        calendarGroupRequestRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    if (activeTab === "calendar_prices" && selectedCompetitorGroup) {
      void syncCalendarGroupState(selectedCompetitorGroup.group_key);
    }
  }, [activeTab, selectedCompetitorGroup?.group_key, syncCalendarGroupState]);

  useEffect(() => {
    const meituanSessions = sessions.filter(
      (session) => session.platform === "meituan" && session.intended_use === "ebk",
    );
    if (!meituanSessions.length) {
      setMeituanAccountId("");
      setMeituanSnapshot(null);
      setMeituanLoadError(null);
      return;
    }
    if (!meituanSessions.some((session) => session.platform_account_id === meituanAccountId)) {
      setMeituanAccountId(meituanSessions[0].platform_account_id);
    }
  }, [sessions, meituanAccountId]);

  useEffect(() => {
    const fliggySessions = sessions.filter(
      (session) => session.platform === "fliggy" && session.intended_use === "ebk",
    );
    if (!fliggySessions.length) {
      setFliggyAccountId("");
      setFliggySnapshot(null);
      setFliggyLoadError(null);
      return;
    }
    if (!fliggySessions.some((session) => session.platform_account_id === fliggyAccountId)) {
      setFliggyAccountId(fliggySessions[0].platform_account_id);
    }
  }, [sessions, fliggyAccountId]);

  async function refreshFliggyLatest(platformAccountId: string) {
    if (!platformAccountId) {
      setFliggySnapshot(null);
      setFliggyLoadError(null);
      return;
    }
    setBusy("fliggy-latest");
    try {
      const latest = await getLatestEbkSnapshot({
        platform: "fliggy",
        platformAccountId,
      });
      setFliggySnapshot(latest);
      setFliggyLoadError(null);
    } catch (error) {
      setFliggySnapshot(null);
      const message = error instanceof Error ? error.message : "加载飞猪 snapshot 失败";
      setFliggyLoadError(
        message.includes("404") || message.toLowerCase().includes("no ebk snapshot")
          ? "暂未读取。完成登录后可执行读取并保存。"
          : message,
      );
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (!fliggyAccountId) {
      return;
    }
    void refreshFliggyLatest(fliggyAccountId);
  }, [fliggyAccountId]);

  async function handleFliggyReadAndSave() {
    if (!fliggyAccountId) {
      setNotice({ tone: "info", text: "请先选择飞猪账号。" });
      return;
    }
    await runAction("fliggy-read-all", async () => {
      const result = await readAllEbkSnapshots({
        platforms: ["fliggy"],
        fliggyPlatformAccountId: fliggyAccountId,
        pushToFeishu: true,
        feishuWatchGroupKey: dataCenterGroup?.group_key,
      });
      await refreshFliggyLatest(fliggyAccountId);
      setNotice({
        tone: result.status === "success" ? "success" : "info",
        text: result.summary || `飞猪 EBK 读取完成：${statusLabel(String(result.status))}`,
      });
    });
  }

  useEffect(() => {
    setShowDisabledCompetitorHotels(false);
  }, [selectedCompetitorKey]);

  async function runAction(label: string, action: () => Promise<void>) {
    setBusy(label);
    try {
      await action();
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  function notifyAdvanced(tone: Notice["tone"], text: string) {
    setNotice({ tone, text });
    setAdvancedNotice({ tone, text });
  }

  async function runAdvancedAction(label: string, action: () => Promise<void>) {
    setBusy(label);
    try {
      await action();
    } catch (error) {
      const text = errorText(error);
      setNotice({ tone: "error", text });
      setAdvancedNotice({ tone: "error", text });
    } finally {
      setBusy(null);
    }
  }

  // ---- 评价管理（review_management）----
  const [reviewPlatform, setReviewPlatform] = useState<ReviewPlatform>("ctrip");
  const [reviewGroupKey, setReviewGroupKey] = useState<string>("");
  const [reviewGroups, setReviewGroups] = useState<ReviewGroup[]>([]);
  const [reviewConfig, setReviewConfig] = useState<ReviewAutoConfig | null>(null);
  const [reviewCatalog, setReviewCatalog] = useState<ReviewCatalog | null>(null);
  const [reviewTemplates, setReviewTemplates] = useState<ReplyTemplate[]>([]);
  const [reviewTasks, setReviewTasks] = useState<ReviewTask[]>([]);
  const reviewSyncAbortRef = useRef<AbortController | null>(null);

  // 店铺=监控组；当前子平台的浏览器账号从监控组+平台解析（未挂载则 null）。
  const reviewStoreSessionId =
    reviewGroups.find((g) => g.group_key === reviewGroupKey)?.platforms[reviewPlatform]
      ?.browser_session_id ?? null;

  const syncReviewState = useCallback(async (platform: string, sessionId: string | null) => {
    reviewSyncAbortRef.current?.abort();
    const controller = new AbortController();
    reviewSyncAbortRef.current = controller;
    try {
      // 不拉 auto-config：抓取/回复等操作后重拉会把用户未保存的模式下拉改动
      // 覆盖回库里的旧值（2026-09-02 运营反馈：改半自动/全自动后跳回关闭）。
      // config 只在平台/店铺切换与保存成功后由 loadReviewConfig 单独拉取。
      const [reviewsRes, templates, tasks] = await Promise.all([
        listReviews(platform, {
          browser_session_id: sessionId ?? undefined,
          signal: controller.signal,
        }),
        listTemplates(platform, {
          browser_session_id: sessionId ?? undefined,
          signal: controller.signal,
        }),
        listTasks(platform, controller.signal),
      ]);
      setReviewCatalog(reviewsRes.catalog);
      setReviewTemplates(templates);
      setReviewTasks(tasks);
    } catch (error) {
      if ((error as { name?: string })?.name !== "AbortError") {
        // 软失败：保留现有数据，页面显示空态。
      }
    }
  }, []);

  const loadReviewConfig = useCallback(async (platform: string, sessionId: string | null) => {
    try {
      setReviewConfig(await getAutoConfig(platform, sessionId));
    } catch {
      // 软失败：保留现有配置展示。
    }
  }, []);

  const loadReviewGroups = useCallback(async () => {
    try {
      const groups = await listReviewGroups();
      setReviewGroups(groups);
      // 首次进入：默认选第一个监控组。
      setReviewGroupKey((prev) => prev || groups[0]?.group_key || "");
    } catch {
      setReviewGroups([]);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== "review_management") {
      return;
    }
    void loadReviewGroups();
  }, [activeTab, loadReviewGroups]);

  useEffect(() => {
    if (activeTab !== "review_management") {
      return;
    }
    void syncReviewState(reviewPlatform, reviewStoreSessionId);
    void loadReviewConfig(reviewPlatform, reviewStoreSessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewGroupKey, reviewPlatform]);

  function handleReviewGroupChange(groupKey: string) {
    setReviewGroupKey(groupKey);
  }

  function handleReviewConfigSave(form: ReviewConfigForm) {
    void runAction("review-save-config", async () => {
      const formError = reviewConfigFormError(form);
      if (formError) {
        throw new Error(formError);
      }
      // 按监控组+平台保存：浏览器账号与飞书群由后端从监控组继承。
      const config = await updateAutoConfig({
        platform: reviewPlatform,
        ctrip_watch_group_key: reviewGroupKey,
        ...reviewConfigPatchFromForm(form),
      });
      setReviewConfig(config);
      setNotice({ tone: "success", text: "已保存自动回复设置。" });
      void loadReviewGroups();
    });
  }

  function handleReviewFetch() {
    void runAction("review-fetch", async () => {
      const result = await fetchReviewsNow(reviewPlatform, reviewStoreSessionId);
      if (result.fetch.status !== "ok") {
        setNotice({
          tone: "info",
          text: result.fetch.error || "平台评价接口未接入，暂无法抓取。",
        });
      } else {
        const pending = result.fetch.pending_count ?? 0;
        setNotice({
          tone: "success",
          text: `抓取完成：当前未回复 ${pending} 条。自动处理不在抓取时触发，按「定时处理时间」到点执行。`,
        });
      }
      await syncReviewState(reviewPlatform, reviewStoreSessionId);
    });
  }

  function handleReviewImportHistory() {
    void runAction("review-import-history", async () => {
      const response = await importHistory(reviewPlatform, reviewStoreSessionId);
      const result = response.result;
      if (result.status !== "ok") {
        setNotice({ tone: "info", text: result.error || "平台评价接口未接入，暂无法导入。" });
        return;
      }
      const byCategory = result.by_category ?? {};
      setNotice({
        tone: "success",
        text: `导入完成：好评 ${byCategory.good ?? 0} 条、中评 ${byCategory.medium ?? 0} 条、差评 ${byCategory.bad ?? 0} 条模板。`,
      });
      await syncReviewState(reviewPlatform, reviewStoreSessionId);
    });
  }

  function handleReviewAutoUpdateTemplates() {
    void runAction("review-auto-update", async () => {
      if (!reviewStoreSessionId) {
        setNotice({
          tone: "info",
          text: "该监控组下本子平台未挂载，无法自动更新。",
        });
        return;
      }
      const group = reviewGroups.find((g) => g.group_key === reviewGroupKey);
      const storeLabel = `${group?.name ?? reviewGroupKey} · ${platformLabel(reviewPlatform)}`;
      const confirmed = window.confirm(autoUpdateTemplatesConfirmText(storeLabel));
      if (!confirmed) {
        return;
      }
      const response = await autoUpdateStoreTemplates(
        reviewPlatform,
        reviewStoreSessionId,
      );
      const result = response.result;
      setNotice({
        tone: result.status === "ok" ? "success" : "info",
        text: autoUpdateResultText(result),
      });
      await syncReviewState(reviewPlatform, reviewStoreSessionId);
    });
  }

  function handleReviewProcessPending() {
    void runAction("review-process-pending", async () => {
      const response = await processPending(reviewPlatform, reviewStoreSessionId);
      const process = response.process;
      if (process.status === "mode_off") {
        setNotice({ tone: "info", text: "当前为关闭自动回复模式，请先在上方选择半自动或全自动。" });
      } else if (process.status === "missing_chat_id") {
        setNotice({ tone: "info", text: "半自动模式需要该监控组配置飞书群（用于推送数量提醒）。" });
      } else if (process.status === "not_ready") {
        setNotice({ tone: "info", text: process.error || "平台评价接口未接入。" });
      } else if (process.mode === "full_auto") {
        const byCategory = process.by_category ?? {};
        const summaryNote =
          process.summary_push?.status === "missing_chat_id"
            ? "；统计卡未推送：该监控组未配置飞书群"
            : "";
        setNotice({
          tone: "success",
          text:
            `处理完成：自动回复 ${process.submitted ?? 0} 条` +
            `（好评 ${byCategory.good ?? 0}、中评 ${byCategory.medium ?? 0}、差评 ${byCategory.bad ?? 0}）${summaryNote}。`,
        });
      } else {
        setNotice({
          tone: "success",
          text: `处理完成：推送 ${process.pushed_cards ?? 0} 张数量提醒。`,
        });
      }
      await syncReviewState(reviewPlatform, reviewStoreSessionId);
    });
  }

  function handleReviewTemplateSave(form: TemplateForm) {
    void runAction("review-template-save", async () => {
      const { payload, error } = templatePayloadFromForm(form);
      if (error) {
        throw new Error(error);
      }
      // 选了店铺时模板归属该店铺；平台级视图下保存为平台通用模板。
      await saveTemplate({ ...payload, browser_session_id: reviewStoreSessionId });
      setNotice({ tone: "success", text: payload.id === null ? "已新增回复模板。" : "已更新回复模板。" });
      await syncReviewState(reviewPlatform, reviewStoreSessionId);
    });
  }

  function handleReviewTemplateDelete(templateId: number) {
    void runAction(`review-template-delete-${templateId}`, async () => {
      await deleteTemplate(templateId);
      setNotice({ tone: "success", text: "已删除回复模板。" });
      await syncReviewState(reviewPlatform, reviewStoreSessionId);
    });
  }

  async function refreshMeituanLatest(platformAccountId: string) {
    if (!platformAccountId) {
      setMeituanSnapshot(null);
      setMeituanLoadError(null);
      return;
    }
    setBusy("meituan-latest");
    try {
      const latest = await getLatestEbkSnapshot({
        platform: "meituan",
        platformAccountId,
      });
      setMeituanSnapshot(latest);
      setMeituanLoadError(null);
    } catch (error) {
      setMeituanSnapshot(null);
      const message = error instanceof Error ? error.message : "加载美团 snapshot 失败";
      setMeituanLoadError(
        message.includes("404") || message.toLowerCase().includes("no ebk snapshot")
          ? "暂未读取。完成登录后可在后续步骤执行读取并保存。"
          : message,
      );
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (!meituanAccountId) {
      return;
    }
    void refreshMeituanLatest(meituanAccountId);
  }, [meituanAccountId]);

  async function handleMeituanReadAndSave() {
    if (!meituanAccountId) {
      setNotice({ tone: "info", text: "请先选择美团账号。" });
      return;
    }
    await runAction("meituan-read-all", async () => {
      const result = await readAllEbkSnapshots({
        platforms: ["meituan"],
        meituanPlatformAccountId: meituanAccountId,
        pushToFeishu: true,
        feishuWatchGroupKey: dataCenterGroup?.group_key,
      });
      await refreshMeituanLatest(meituanAccountId);
      setNotice({
        tone: result.status === "success" ? "success" : "info",
        text: result.summary || `美团 EBK 读取完成：${statusLabel(String(result.status))}`,
      });
    });
  }

  async function handleCreateSession() {
    await runAction("create-session", async () => {
      const session = await createBrowserSession({
        platform_account_id: accountForm.platform_account_id.trim(),
        display_name: accountForm.display_name.trim(),
        browser_session_id: accountForm.browser_session_id.trim() || undefined,
        platform: accountForm.platform,
        intended_use: accountForm.platform === "ctrip" ? accountForm.intended_use : "ebk",
        connection_mode: accountForm.connection_mode,
        session_port: Number(accountForm.session_port),
        shop_key:
          accountForm.platform === "ctrip" && accountForm.intended_use === "competitor_price"
            ? undefined
            : accountForm.shop_key || undefined,
      });
      setSessions(await listBrowserSessions());
      setAccountForm(defaultAccountForm);
      setNotice({ tone: "success", text: `已新增浏览器账号：${session.display_name}` });
    });
  }

  async function handleCreateShop(name: string) {
    await runAction("create-shop", async () => {
      const shop = await createShop({ name });
      setShops(await listShops());
      setAccountForm((current) => ({ ...current, shop_key: current.shop_key || shop.shop_key }));
      setNotice({ tone: "success", text: `已新建店铺：${shop.name}` });
    });
  }

  async function handleOpenLogin(session: BrowserSession, loginContext: BrowserIntendedUse) {
    await runAction(`open-login-${session.browser_session_id}`, async () => {
      const result =
        session.platform === "meituan"
          ? await openMeituanLogin({
              platformAccountId: session.platform_account_id,
              browserSessionId: session.browser_session_id,
            })
          : session.platform === "fliggy"
            ? await openFliggyLogin({
                platformAccountId: session.platform_account_id,
                browserSessionId: session.browser_session_id,
              })
            : await openCtripLogin({
                platformAccountId: session.platform_account_id,
                browserSessionId: session.browser_session_id,
                loginContext: loginContext === "llm_chat" ? "ebk" : loginContext,
              });
      setSessions(await listBrowserSessions());
      setNotice({ tone: result.status === "success" ? "success" : "info", text: result.summary });
    });
  }

  async function handleCheckMeituanLogin(session: BrowserSession) {
    await runAction(`check-meituan-login-${session.browser_session_id}`, async () => {
      const result = await checkMeituanLoginState({
        platformAccountId: session.platform_account_id,
        browserSessionId: session.browser_session_id,
      });
      setSessions(await listBrowserSessions());
      setNotice({ tone: result.status === "success" ? "success" : "info", text: result.summary });
    });
  }

  async function handleCheckFliggyLogin(session: BrowserSession) {
    await runAction(`check-fliggy-login-${session.browser_session_id}`, async () => {
      const result = await checkFliggyLoginState({
        platformAccountId: session.platform_account_id,
        browserSessionId: session.browser_session_id,
      });
      setSessions(await listBrowserSessions());
      setNotice({ tone: result.status === "success" ? "success" : "info", text: result.summary });
    });
  }

  async function handleBindDataCenter(session: BrowserSession, groupKey: string) {
    if (!groupKey) {
      setNotice({ tone: "error", text: "请先选择要绑定的 EBK 监控组（EBK 操作台里创建）。" });
      return;
    }
    await runAction(`bind-data-center-${session.browser_session_id}`, async () => {
      const updated = await patchDataCenterGroup(groupKey, {
        platform_account_id: session.platform_account_id,
        browser_session_id: session.browser_session_id,
      });
      setDataCenterGroups(replaceDataCenterGroup(dataCenterGroups, updated));
      setSelectedDataCenterKey(updated.group_key);
      setNotice({
        tone: "success",
        text: `已把「${session.display_name}」绑定为「${updated.name}」的 EBK 浏览器账号。`,
      });
    });
  }

  async function handleBindCompetitor(session: BrowserSession, groupKey: string) {
    if (!groupKey) {
      setNotice({ tone: "error", text: "请先选择要绑定的竞品监控组（竞品价格页里创建）。" });
      return;
    }
    await runAction(`bind-competitor-${session.browser_session_id}`, async () => {
      const updated = await patchCompetitorGroup(groupKey, {
        platform_account_id: session.platform_account_id,
        browser_session_id: session.browser_session_id,
      });
      const nextGroups = await replaceCompetitorGroup(competitorGroups, updated);
      setCompetitorGroups(nextGroups);
      await syncCompetitorGroupState(updated.group_key, nextGroups);
      setNotice({
        tone: "success",
        text: `已把「${session.display_name}」绑定为「${updated.name}」的竞品浏览器账号。`,
      });
    });
  }

  async function handleRunDataCenter() {
    if (!dataCenterGroup) {
      return;
    }
    const groupKey = dataCenterGroup.group_key;
    const enabledCount = dataCenterDatasets.filter((dataset) => dataset.enabled).length;
    await runAction("run-data-center", async () => {
      const result = await runDataCenterNow(groupKey, {
        timeoutMs: dataCenterRunNowTimeoutMs(enabledCount),
      });
      // run-now 是长请求：期间切换监控组后，旧组的晚到响应不得写当前组状态。
      if (selectedDataCenterKeyRef.current !== groupKey) {
        return;
      }
      let latest = null;
      let latestFailed = false;
      try {
        latest = await getLatestDataCenterRun(groupKey);
      } catch (error) {
        if (isAbortError(error)) {
          throw error;
        }
        latestFailed = true;
      }
      if (selectedDataCenterKeyRef.current !== groupKey) {
        return;
      }
      if (latest) {
        setDataCenterLatest(latest.run ?? null);
        setDataCenterLatestSnapshot(latest);
      }
      // 数据集区块的 last_status/last_captured_at 只有重拉 datasets 才会更新。
      try {
        setDataCenterDatasets(await listDataCenterDatasets(groupKey));
      } catch (error) {
        if (isAbortError(error)) {
          throw error;
        }
        // 拉取失败保留旧列表。
      }
      setDataCenterGroups(await listDataCenterGroups());
      const suffix = latestFailed ? "（最新结果拉取失败，已保留上次显示，可稍后刷新。）" : "";
      setNotice({ tone: statusTone(String(result.status ?? "")), text: String(result.summary ?? "EBK 数据中心读取已完成。") + suffix });
    });
  }

  async function handleSetDataCenterPolling(enabled: boolean) {
    if (!dataCenterGroup) {
      return;
    }
    await runAction("patch-data-center", async () => {
      const updated = await patchDataCenterGroup(dataCenterGroup.group_key, {
        auto_poll_enabled: enabled,
      });
      setDataCenterGroups(replaceDataCenterGroup(dataCenterGroups, updated));
      setSelectedDataCenterKey(updated.group_key);
      setNotice({
        tone: "success",
        text: enabled ? "EBK 数据中心自动轮询已开启。" : "EBK 数据中心自动轮询已暂停。",
      });
    });
  }

  async function handleSetDataCenterDailyBrief(enabled: boolean) {
    if (!dataCenterGroup) {
      return;
    }
    await runAction("patch-daily-brief", async () => {
      const updated = await patchDataCenterGroup(dataCenterGroup.group_key, {
        daily_brief_enabled: enabled,
      });
      setDataCenterGroups(replaceDataCenterGroup(dataCenterGroups, updated));
      setSelectedDataCenterKey(updated.group_key);
      setNotice({
        tone: "success",
        text: enabled ? "EBK 每日简报定时推送已开启。" : "EBK 每日简报定时推送已关闭。",
      });
    });
  }

  async function handleStopAllEbkSchedules() {
    const targets = dataCenterGroups.filter(
      (group) => group.enabled && (group.auto_poll_enabled || group.daily_brief_enabled),
    );
    if (!targets.length) {
      return;
    }
    await runAction("patch-data-center-stop-all", async () => {
      let nextGroups = dataCenterGroups;
      for (const group of targets) {
        const updated = await patchDataCenterGroup(group.group_key, {
          auto_poll_enabled: false,
          daily_brief_enabled: false,
        });
        nextGroups = replaceDataCenterGroup(nextGroups, updated);
      }
      setDataCenterGroups(nextGroups);
      if (dataCenterGroup) {
        setSelectedDataCenterKey(dataCenterGroup.group_key);
      }
      setNotice({ tone: "success", text: "已关闭全部 EBK 定时任务。" });
    });
  }

  async function handleRunCompetitor(pushToFeishu: boolean) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("run-competitor", async () => {
      const result = await runCompetitorNow(selectedCompetitorGroup.group_key, pushToFeishu);
      await refreshCompetitor(selectedCompetitorGroup.group_key);
      setCompetitorGroups(await listCompetitorGroups());
      setNotice({ tone: statusTone(String(result.status ?? "")), text: String(result.summary ?? "竞品价格读取已完成。") });
    });
  }

  async function handleSetCompetitorPolling(enabled: boolean) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("patch-competitor-group", async () => {
      const updated = await patchCompetitorGroup(selectedCompetitorGroup.group_key, {
        auto_poll_enabled: enabled,
      });
      const nextGroups = await replaceCompetitorGroup(competitorGroups, updated);
      setCompetitorGroups(nextGroups);
      setGroupForm(groupFormFromWatchGroup(updated));
      await syncCompetitorGroupState(updated.group_key, nextGroups, { resetHotelForm: false });
      setNotice({
        tone: "success",
        text: enabled ? "竞品监控自动轮询已开启。" : "竞品监控自动轮询已暂停。",
      });
    });
  }

  async function handleRunCalendarPrice(pushToFeishu: boolean) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("run-calendar-price", async () => {
      const result = await runCalendarPriceNow(selectedCompetitorGroup.group_key, pushToFeishu);
      await syncCalendarGroupState(selectedCompetitorGroup.group_key);
      setNotice({
        tone: statusTone(String(result.status ?? "")),
        text: String(result.summary ?? "日历价读取已完成。"),
      });
    });
  }

  async function handlePushCalendarMatrixBrief() {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("push-calendar-matrix-brief", async () => {
      const result = await pushCalendarMatrixBriefNow(selectedCompetitorGroup.group_key);
      await syncCalendarGroupState(selectedCompetitorGroup.group_key);
      setNotice({
        tone: statusTone(String(result.status ?? "")),
        text: String(result.summary ?? "日历价矩阵简报已推送。"),
      });
    });
  }

  async function handleSaveCalendarConfig() {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("patch-calendar-config", async () => {
      const updated = await patchCalendarPriceConfig(
        selectedCompetitorGroup.group_key,
        calendarConfigPatchFromForm(calendarConfigForm),
      );
      setCalendarConfig(updated);
      setCalendarConfigForm(calendarConfigFormFromConfig(updated));
      setNotice({ tone: "success", text: "日历价监测配置已保存。" });
    });
  }

  async function handleSetCalendarPolling(enabled: boolean) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("patch-calendar-config", async () => {
      const updated = await patchCalendarPriceConfig(selectedCompetitorGroup.group_key, {
        auto_poll_enabled: enabled,
      });
      setCalendarConfig(updated);
      setCalendarConfigForm(calendarConfigFormFromConfig(updated));
      setCalendarSchedulerStatus(await getCalendarSchedulerStatus().catch(() => null));
      setNotice({
        tone: "success",
        text: enabled ? "日历价自动轮询已开启。" : "日历价自动轮询已暂停。",
      });
    });
  }

  async function handleSaveCalendarFeishuWebhook(feishuWebhookUrl: string) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("patch-calendar-feishu", async () => {
      const updated = await patchCompetitorGroup(selectedCompetitorGroup.group_key, {
        feishu_webhook_url: feishuWebhookUrl,
      });
      const nextGroups = await replaceCompetitorGroup(competitorGroups, updated);
      setCompetitorGroups(nextGroups);
      await syncCalendarGroupState(updated.group_key);
      setNotice({
        tone: "success",
        text: feishuWebhookUrl.trim() ? "日历价预警机器人已保存。" : "日历价预警机器人已清除。",
      });
    });
  }

  async function handleSavePriceChangeStrategy() {
    if (!priceChangeForm) {
      return;
    }
    await runAction("save-price-strategy", async () => {
      const strategy = await patchPricingStrategyConfig({
        ...strategyPatchFromForm(priceChangeForm),
        watch_group_key: selectedCompetitorGroup?.group_key,
      });
      setPriceChangeStrategy(strategy);
      setNotice({ tone: "success", text: "价格变化提醒规则已保存。" });
    });
  }

  async function handleCreateDataCenterGroup(shopKey?: string) {
    await runAction("create-data-center-group", async () => {
      const created = await createDataCenterGroup({
        group_key: `ebk_${Date.now()}`,
        name: "新的 EBK 数据中心监控组",
        brief_title: "EBK 数据中心简报",
        daily_brief_title: "EBK 数据简报",
        daily_brief_push_time: "08:30",
        shop_key: shopKey ?? null,
      });
      const nextGroups = await listDataCenterGroups();
      setDataCenterGroups(nextGroups);
      await refreshDataCenterGroup(created.group_key);
      setNotice({ tone: "success", text: "已新增 EBK 数据中心监控组。" });
    });
  }

  async function handleDeleteDataCenterGroup() {
    if (!dataCenterGroup) {
      return;
    }
    const groupKey = dataCenterGroup.group_key;
    const groupName = dataCenterGroup.name;
    if (!window.confirm(`确认删除监控组「${groupName}」？该组的数据集配置和历史读取记录也会删除。`)) {
      return;
    }
    await runAction("delete-data-center-group", async () => {
      await deleteDataCenterGroup(groupKey);
      const nextKey = nextWatchGroupKeyAfterDelete(dataCenterGroups, groupKey);
      const nextGroups = await listDataCenterGroups();
      setDataCenterGroups(nextGroups);
      const nextGroup = nextGroups.find((item) => item.group_key === nextKey) ?? nextGroups[0] ?? null;
      if (nextGroup) {
        await refreshDataCenterGroup(nextGroup.group_key);
      } else {
        setSelectedDataCenterKey("");
        setDataCenterDatasets([]);
        setDataCenterLatest(null);
        setDataCenterLatestSnapshot(null);
      }
      setNotice({ tone: "success", text: "已删除 EBK 数据中心监控组。" });
    });
  }

  async function handleRunDailyBrief() {
    if (!dataCenterGroup) {
      return;
    }
    await runAction("run-daily-brief", async () => {
      const result = await runDailyBriefNow(dataCenterGroup.group_key);
      const latest = await getLatestDataCenterRun(dataCenterGroup.group_key).catch(() => null);
      setDataCenterLatest(latest?.run ?? null);
      setDataCenterLatestSnapshot(latest);
      setDataCenterGroups(await listDataCenterGroups());
      setNotice({ tone: statusTone(String(result.status ?? "")), text: String(result.summary ?? "EBK 数据简报已刷新并推送。") });
    });
  }

  async function handleTogglePriceChangeStrategy(nextEnabled: boolean) {
    const currentForm = priceChangeForm ?? strategyFormFromConfig(priceChangeStrategy);
    const nextForm = { ...currentForm, enabled: nextEnabled };
    setPriceChangeForm(nextForm);
    await runAction("toggle-price-strategy", async () => {
      const strategy = await patchPricingStrategyConfig({
        ...strategyPatchFromForm(nextForm),
        enabled: nextEnabled,
        watch_group_key: selectedCompetitorGroup?.group_key,
      });
      setPriceChangeStrategy(strategy);
      setPriceChangeForm(strategyFormFromConfig(strategy));
      setNotice({
        tone: "success",
        text: nextEnabled ? "价格变化提醒已恢复。" : "价格变化提醒已停止。",
      });
    });
  }

  async function handleSavePriceChangeAlertTitle(title: string) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("save-price-alert-title", async () => {
      const updated = await patchCompetitorGroup(selectedCompetitorGroup.group_key, {
        price_change_alert_title: title.trim(),
      });
      const nextGroups = await replaceCompetitorGroup(competitorGroups, updated);
      setCompetitorGroups(nextGroups);
      setGroupForm(groupFormFromWatchGroup(updated));
      await syncCompetitorGroupState(updated.group_key, nextGroups, { resetHotelForm: false });
      setNotice({ tone: "success", text: "价格变化提醒推送标题已保存。" });
    });
  }

  async function handleEvaluatePriceChange() {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("evaluate-price-change", async () => {
      const alert = await evaluatePriceChangeAlert({
        watch_group_key: selectedCompetitorGroup.group_key,
        push_to_feishu: false,
      });
      setPriceChangeAlert(alert);
      setNotice({ tone: statusTone(String(alert.status ?? "")), text: alert.summary });
    });
  }

  async function handleSaveIntegrationConfig() {
    await runAction("save-integration-config", async () => {
      const nextConfig = await updateIntegrationConfig({
        llm_provider: integrationConfigForm.llm_provider,
        llm_base_url: integrationConfigForm.llm_base_url,
        llm_model: integrationConfigForm.llm_model,
        llm_api_key: integrationConfigForm.llm_api_key,
      });
      setIntegrationConfig(nextConfig);
      setIntegrationConfigForm(integrationConfigFormFromSnapshot(nextConfig));
      setLlmConnectionTest(null);
      setNotice({ tone: "success", text: "模型配置已保存。" });
    });
  }

  async function handleTestLlmConnection() {
    await runAction("test-llm-connection", async () => {
      const result = await testLlmConnection({
        llm_provider: integrationConfigForm.llm_provider,
        llm_base_url: integrationConfigForm.llm_base_url,
        llm_model: integrationConfigForm.llm_model,
        llm_api_key: integrationConfigForm.llm_api_key,
      });
      setLlmConnectionTest(result);
      setNotice({
        tone: result.status === "success" ? "success" : "info",
        text: result.summary,
      });
    });
  }

  async function handleLoadSupervisorOverview() {
    const apiKey = supervisorApiKey.trim();
    if (!apiKey) {
      setSupervisorOverviewError("请输入 Supervisor 访问密钥。");
      return;
    }
    await runAction("load-supervisor-overview", async () => {
      try {
        setSupervisorOverview(await getSupervisorOverview(apiKey));
        setSupervisorOverviewError(null);
      } catch (error) {
        setSupervisorOverviewError(errorText(error));
        throw error;
      }
    });
  }

  async function reloadSupervisorOverview() {
    const apiKey = supervisorApiKey.trim();
    if (!apiKey) {
      return;
    }
    setSupervisorOverview(await getSupervisorOverview(apiKey));
    setSupervisorOverviewError(null);
    // 准入面板读的是 Hermes YAML，不在总览里；每次总览刷新都让准入面板
    // 跟着重读，新增群绑定/凭据部署后无需刷新整页。
    setAdmissionRevision((value) => value + 1);
  }

  function handleSetProfileCredentials(
    profile: Parameters<AdvancedAgentConfigPanelProps["onSetProfileCredentials"]>[0],
    input: Parameters<AdvancedAgentConfigPanelProps["onSetProfileCredentials"]>[1],
  ) {
    void runAdvancedAction(`profile-creds-${profile.profile_key}`, async () => {
      await setFeishuProfileCredentials(supervisorApiKey, profile.profile_key, {
        app_id: input.appId,
        app_secret: input.appSecret,
        display_name: input.displayName,
      });
      await reloadSupervisorOverview();
      notifyAdvanced(
        "success",
        "飞书应用凭据已保存并部署；profile 现在可以启用并绑定群。",
      );
    });
  }

  function handleCreateAgentProfile(input: Parameters<AdvancedAgentConfigPanelProps["onCreateProfile"]>[0]) {
    void runAdvancedAction("create-agent-profile", async () => {
      await createFeishuAgentProfile(supervisorApiKey, {
        profile_key: input.profileKey,
        display_name: input.displayName,
        enabled: input.enabled,
      });
      await reloadSupervisorOverview();
      notifyAdvanced(
        "success",
        input.enabled
          ? "AI 助手 profile 已创建并启用。"
          : "AI 助手 profile 已创建（未启用）；点击该行的「配置凭据」输入飞书 App ID/Secret 后即可启用。",
      );
    });
  }

  function handleDeleteAgentProfile(profile: Parameters<AdvancedAgentConfigPanelProps["onDeleteProfile"]>[0]) {
    void runAdvancedAction(`profile-delete-${profile.profile_key}`, async () => {
      const deleted = await deleteFeishuAgentProfile(supervisorApiKey, profile.profile_key);
      await reloadSupervisorOverview();
      if (deleted.cleanup_warning) {
        notifyAdvanced("error", deleted.cleanup_warning);
      } else if (deleted.manifest_cleaned) {
        notifyAdvanced(
          "success",
          `AI 助手「${profile.display_name}」已删除，凭据清单与后端映射已同步回收。`,
        );
      } else {
        notifyAdvanced("success", `AI 助手「${profile.display_name}」已删除。`);
      }
    });
  }

  function handlePatchSupervisorProfile(profile: Parameters<AdvancedAgentConfigPanelProps["onPatchProfile"]>[0], enabled: boolean) {
    void runAdvancedAction(`profile-${profile.profile_key}`, async () => {
      await patchFeishuAgentProfile(supervisorApiKey, profile.profile_key, { enabled });
      await reloadSupervisorOverview();
      notifyAdvanced("success", enabled ? "AI 助手已启用。" : "AI 助手已停用。");
    });
  }

  function handlePatchSupervisorBinding(
    binding: Parameters<AdvancedAgentConfigPanelProps["onPatchBinding"]>[0],
    input: FeishuChatAgentBindingPatch,
  ) {
    void runAdvancedAction(`binding-${binding.id}`, async () => {
      const updated = await patchFeishuChatAgentBinding(supervisorApiKey, binding.id, input);
      await reloadSupervisorOverview();
      if (updated.admission_warning) {
        notifyAdvanced("info", updated.admission_warning);
      } else if (input.enabled === false) {
        notifyAdvanced("success", "群绑定已停用，机器人不再在该群应答。");
      }
    });
  }

  function handleDeleteChatBinding(binding: Parameters<AdvancedAgentConfigPanelProps["onDeleteBinding"]>[0]) {
    void runAdvancedAction(`binding-delete-${binding.id}`, async () => {
      const deleted = await deleteFeishuChatAgentBinding(supervisorApiKey, binding.id);
      await reloadSupervisorOverview();
      if (deleted.admission_warning) {
        notifyAdvanced("info", deleted.admission_warning);
      } else {
        notifyAdvanced("success", "群绑定已删除，该群已移出飞书准入名单。");
      }
    });
  }

  function handleSaveManualPricingChat(session: BrowserSession, chatId: string | null) {
    void runAdvancedAction(`pricing-chat-${session.browser_session_id}`, async () => {
      const updated = await patchBrowserSession(session.browser_session_id, {
        pricing_feishu_chat_id: chatId,
      });
      setSessions((current) => current.map((item) => item.browser_session_id === updated.browser_session_id ? updated : item));
      await reloadSupervisorOverview();
      notifyAdvanced("success", chatId ? "手动调价指令群已保存。" : "手动调价指令群已清除。");
    });
  }

  function handleSaveCompetitorPricingAccount(group: CompetitorWatchGroup, browserSessionId: string | null) {
    void runAdvancedAction(`pricing-account-${group.group_key}`, async () => {
      const session = sessions.find((item) => item.browser_session_id === browserSessionId);
      const updated = await patchCompetitorGroup(group.group_key, browserSessionId
        ? {
            pricing_browser_session_id: browserSessionId,
            pricing_platform_account_id: session?.platform_account_id,
          }
        : { clear_pricing_binding: true });
      setCompetitorGroups(await listCompetitorGroups());
      if (selectedCompetitorKey === updated.group_key) {
        setGroupForm(groupFormFromWatchGroup(updated));
      }
      await reloadSupervisorOverview();
      notifyAdvanced("success", browserSessionId ? "竞品组调价账号已保存。" : "竞品组调价账号已清除。");
    });
  }

  function handleBindEbkGroupChat(groupKey: string, chatId: string | null) {
    // Bind (or unbind) a single EBK watch group's feishu_chat_id to this chat.
    // The panel clears the previously-bound group before assigning a new one,
    // keeping the invariant that one chat maps to at most one EBK group.
    void runAdvancedAction(`ebk-binding-${groupKey}`, async () => {
      await patchDataCenterGroup(groupKey, { feishu_chat_id: chatId });
      const updated = await listDataCenterGroups();
      setDataCenterGroups(updated);
      await reloadSupervisorOverview();
      notifyAdvanced(
        "success",
        chatId ? "EBK 监控组投递群已绑定。" : "EBK 监控组投递群已解除绑定。",
      );
    });
  }

  function handleBindCompetitorGroupChat(groupKey: string, chatId: string | null) {
    // 与 EBK 绑定同规则：绑定/解绑单个竞品监控组的运营问答上下文群。
    void runAdvancedAction(`competitor-binding-${groupKey}`, async () => {
      await patchCompetitorGroup(groupKey, { feishu_chat_id: chatId });
      const updated = await listCompetitorGroups();
      setCompetitorGroups(updated);
      await reloadSupervisorOverview();
      notifyAdvanced(
        "success",
        chatId ? "竞品监控组上下文群已绑定。" : "竞品监控组上下文群已解除绑定。",
      );
    });
  }

  function handleCreateChatBinding(input: Parameters<AdvancedAgentConfigPanelProps["onCreateChatBinding"]>[0]) {
    // 新增整套店铺接入的第一步：先建 chat→profile 绑定（EBK/竞品组都
    // 要求目标 chat 已有启用绑定），再按选择自动挂 EBK/竞品监控组。
    void runAdvancedAction("create-chat-binding", async () => {
      const created = await createFeishuChatAgentBinding(supervisorApiKey, {
        chat_id: input.chatId,
        profile_key: input.profileKey,
        display_name: input.displayName || null,
        enabled: true,
      });
      if (input.ebkGroupKey) {
        await patchDataCenterGroup(input.ebkGroupKey, { feishu_chat_id: input.chatId });
      }
      if (input.competitorGroupKey) {
        await patchCompetitorGroup(input.competitorGroupKey, { feishu_chat_id: input.chatId });
      }
      const [updatedDataCenterGroups, updatedCompetitorGroups] = await Promise.all([
        listDataCenterGroups(),
        listCompetitorGroups(),
      ]);
      setDataCenterGroups(updatedDataCenterGroups);
      setCompetitorGroups(updatedCompetitorGroups);
      await reloadSupervisorOverview();
      const groupSuffix =
        input.ebkGroupKey || input.competitorGroupKey ? "并已绑定所选监控组。" : "";
      if (created.admission_warning) {
        notifyAdvanced("info", created.admission_warning);
      } else if (created.admission_synced) {
        notifyAdvanced(
          "success",
          `群绑定已创建并同步飞书准入名单，机器人可在该群应答；${groupSuffix}`,
        );
      } else {
        notifyAdvanced(
          "success",
          `群绑定已创建；该 AI 助手尚未启用或未部署凭据，暂未同步准入名单，启用后会自动同步。${groupSuffix}`,
        );
      }
    });
  }

  function handleSaveSupervisorRole(input: Parameters<AdvancedAgentConfigPanelProps["onSaveRole"]>[0]) {
    void runAdvancedAction(`role-${input.profileKey}`, async () => {
      if (input.assignment) {
        await patchFeishuRoleAssignment(supervisorApiKey, input.assignment.id, {
          role: input.role,
          granted_by: input.grantedBy,
        });
      } else {
        await createFeishuRoleAssignment(supervisorApiKey, {
          subject_type: "profile",
          subject_ref: input.profileKey,
          role: input.role,
          granted_by: input.grantedBy,
        });
      }
      await reloadSupervisorOverview();
      notifyAdvanced("success", "角色分配已保存。");
    });
  }

  async function handleLoadLlmModels() {
    await runAction("load-llm-models", async () => {
      const result = await listLlmModels({
        llm_provider: integrationConfigForm.llm_provider,
        llm_base_url: integrationConfigForm.llm_base_url,
        llm_api_key: integrationConfigForm.llm_api_key,
      });
      setLlmModelsResult(result);
      setLlmModels(result.models);
      setNotice({
        tone: result.status === "success" ? "success" : "info",
        text: result.summary,
      });
    });
  }

  async function handleSaveCompetitorGroup(extraInput?: {
    feishu_mobile_webhook_url?: string;
    feishu_chat_id?: string;
  }) {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction("patch-competitor-group", async () => {
      const { pool_member_browser_session_ids, ...groupPatch } = competitorGroupPatchFromForm(groupForm, sessions);
      await putCompetitorBrowserPool(
        selectedCompetitorGroup.group_key,
        (pool_member_browser_session_ids ?? []).map((browserSessionId, index) => ({
          browser_session_id: browserSessionId,
          enabled: true,
          sort_order: index,
          weight: 1,
        })),
      );
      const updated = await patchCompetitorGroup(selectedCompetitorGroup.group_key, {
        ...groupPatch,
        feishu_desktop_webhook_url: "",
        ...extraInput,
      });
      const nextGroups = await replaceCompetitorGroup(competitorGroups, updated);
      setCompetitorGroups(nextGroups);
      setGroupForm(groupFormFromWatchGroup(updated));
      await syncCompetitorGroupState(updated.group_key, nextGroups, { resetHotelForm: false });
      setNotice({ tone: "success", text: "竞品监控组配置已保存。" });
    });
  }

  async function handleDeleteCompetitorGroup() {
    if (!selectedCompetitorGroup) {
      return;
    }
    const groupKey = selectedCompetitorGroup.group_key;
    const groupName = selectedCompetitorGroup.name;
    if (!window.confirm(`确认删除监控组「${groupName}」？该组的酒店配置和历史趋势也会删除。`)) {
      return;
    }
    await runAction("delete-competitor-group", async () => {
      await deleteCompetitorGroup(groupKey);
      const nextKey = nextGroupKeyAfterDelete(competitorGroups, groupKey);
      const nextGroups = await listCompetitorGroups();
      setCompetitorGroups(nextGroups);
      const nextGroup = nextGroups.find((item) => item.group_key === nextKey) ?? nextGroups[0] ?? null;
      if (nextGroup) {
        await syncCompetitorGroupState(nextGroup.group_key, nextGroups);
      } else {
        setSelectedCompetitorKey("");
        setCompetitorHotels([]);
        setCompetitorLatest(null);
        setCompetitorTrend(null);
        setGroupForm(groupFormFromWatchGroup(null));
        setHotelForm(resetHotelFormForGroup());
        setEditingHotelId(null);
      }
      setNotice({ tone: "success", text: "已删除竞品监控组。" });
    });
  }

  async function handleSaveHotel() {
    if (!selectedCompetitorGroup) {
      return;
    }
    await runAction(editingHotelId ? `hotel-${editingHotelId}` : "add-hotel", async () => {
      const payload = {
        role: hotelForm.role,
        alias: hotelForm.alias.trim(),
        short_alias: hotelForm.short_alias.trim() || null,
        detail_url: hotelForm.detail_url.trim(),
        sort_order: Number(hotelForm.sort_order),
        enabled: hotelForm.enabled,
      };
      if (editingHotelId) {
        await patchCompetitorHotel(selectedCompetitorGroup.group_key, editingHotelId, payload);
      } else {
        await upsertCompetitorHotel(selectedCompetitorGroup.group_key, payload);
      }
      setHotelForm(resetHotelFormForGroup());
      setEditingHotelId(null);
      await refreshCompetitor(selectedCompetitorGroup.group_key);
      setCompetitorGroups(await listCompetitorGroups());
      setNotice({ tone: "success", text: "已保存酒店配置。" });
    });
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Pan Hotel Watch</p>
          <h1>生产级数据盯盘</h1>
          <p className="lede">真实账号、真实浏览器、真实读取。前端不提供写价入口；非直连账号仅能通过 Feishu 审批链路进入受控调价流程。</p>
        </div>
        <button className="icon-button" onClick={() => void loadAll()} title="刷新后台状态" type="button">
          {loading ? <Loader2 className="spin" size={18} /> : <RefreshCw size={18} />}
        </button>
      </header>

      <section className="status-strip">
        <StatusItem icon={Activity} label="后端" value={health ? statusLabel(health.summary.status) : "检查中"} />
        <StatusItem icon={MonitorCheck} label="浏览器账号" value={`${sessions.length} 个`} />
        <StatusItem
          icon={Database}
          label="EBK 全局推送"
          value={globalPushStrip.text}
          detail={globalPushStrip.detail}
        />
        <StatusItem
          icon={Building2}
          label="竞品监控"
          value={competitorStrip.text}
          detail={competitorStrip.detail}
        />
        <StatusItem
          icon={CalendarDays}
          label="日历价提醒"
          value={calendarStrip.text}
          detail={calendarStrip.detail}
        />
        <StatusItem
          icon={Bell}
          label="价格变化提醒"
          value={priceChangeStrip.text}
          detail={priceChangeStrip.detail}
        />
      </section>

      {notice ? <div className={`notice ${notice.tone}`}>{notice.text}</div> : null}

      <nav className="tabbar" aria-label="功能切换">
        <TabButton active={activeTab === "accounts"} icon={MonitorCheck} label="账号与浏览器" onClick={() => setActiveTab("accounts")} />
        <TabButton active={activeTab === "ebk_ops"} icon={Database} label="EBK 操作台" onClick={() => setActiveTab("ebk_ops")} />
        <TabButton active={activeTab === "competitor"} icon={Building2} label="竞品价格" onClick={() => setActiveTab("competitor")} />
        <TabButton active={activeTab === "calendar_prices"} icon={CalendarDays} label="日历价监测" onClick={() => setActiveTab("calendar_prices")} />
        <TabButton active={activeTab === "price_alerts"} icon={Bell} label="价格变化提醒" onClick={() => setActiveTab("price_alerts")} />
        <TabButton active={activeTab === "review_management"} icon={MessageSquare} label="评价管理" onClick={() => setActiveTab("review_management")} />
        <TabButton active={activeTab === "history"} icon={History} label="历史记录" onClick={() => setActiveTab("history")} />
        <TabButton active={activeTab === "bi_daily"} icon={BarChart3} label="经营 BI" onClick={() => setActiveTab("bi_daily")} />
      </nav>

      {activeTab === "accounts" ? (
        <AccountsPanel
          sessions={sessions}
          shops={shops}
          form={accountForm}
          busy={busy}
          dataCenterGroups={dataCenterGroups}
          competitorGroups={competitorGroups}
          onFormChange={setAccountForm}
          onCreate={() => void handleCreateSession()}
          onCreateShop={(name) => void handleCreateShop(name)}
          onOpenLogin={handleOpenLogin}
          onCheckMeituanLogin={handleCheckMeituanLogin}
          onCheckFliggyLogin={handleCheckFliggyLogin}
          onBindDataCenter={handleBindDataCenter}
          onBindCompetitor={handleBindCompetitor}
          onPatchSession={(session, input) =>
            void runAction(`patch-session-${session.browser_session_id}`, async () => {
              await patchBrowserSession(session.browser_session_id, input);
              setSessions(await listBrowserSessions());
              setNotice({ tone: "success", text: "已更新浏览器账号配置。" });
            })
          }
          onDelete={(session) =>
            void runAction(`delete-${session.browser_session_id}`, async () => {
              await deleteBrowserSession(session.browser_session_id);
              await loadAll();
            })
          }
        />
      ) : null}

      {activeTab === "ebk_ops" ? (
        <EbkOpsConsole
          ctrip={{
            businessData: buildCtripBusinessData(dataCenterLatestSnapshot),
            latest: dataCenterLatest,
            groups: dataCenterGroups,
            group: dataCenterGroup,
            datasets: dataCenterDatasets,
            sessions,
            busy,
            onSelectGroup: (groupKey) =>
              void runAction("select-data-center-group", async () => {
                await refreshDataCenterGroup(groupKey);
              }),
            onCreateGroup: () => void handleCreateDataCenterGroup(),
            onDeleteGroup: () => void handleDeleteDataCenterGroup(),
            onSetPolling: (enabled) => void handleSetDataCenterPolling(enabled),
            onSetDailyBrief: (enabled) => void handleSetDataCenterDailyBrief(enabled),
            onStopAllSchedules: () => void handleStopAllEbkSchedules(),
            selectedKey: selectedDataCenterKey,
            onPatchBriefGroup: (input) =>
              dataCenterGroup
                ? void runAction("patch-daily-brief", async () => {
                    const updated = await patchDataCenterGroup(dataCenterGroup.group_key, input);
                    setDataCenterGroups(replaceDataCenterGroup(dataCenterGroups, updated));
                    setSelectedDataCenterKey(updated.group_key);
                    setNotice({ tone: "success", text: "EBK 数据简报配置已保存。" });
                  })
                : undefined,
            onPatchDataCenterGroup: (input) =>
              dataCenterGroup
                ? void runAction("patch-data-center", async () => {
                    const updated = await patchDataCenterGroup(dataCenterGroup.group_key, input);
                    setDataCenterGroups(replaceDataCenterGroup(dataCenterGroups, updated));
                    setSelectedDataCenterKey(updated.group_key);
                    setNotice({ tone: "success", text: "EBK 数据中心配置已保存。" });
                  })
                : undefined,
            onPatchDataset: (dataset, input) =>
              dataCenterGroup
                ? void runAction(`dataset-${dataset.dataset_id}`, async () => {
                    await patchDataCenterDataset(dataCenterGroup.group_key, dataset.dataset_id, input);
                    setDataCenterDatasets(await listDataCenterDatasets(dataCenterGroup.group_key));
                  })
                : undefined,
            onRefreshBrief: () => void handleRunDailyBrief(),
            onRunDataCenter: () => void handleRunDataCenter(),
          }}
          meituan={{
            sessions,
            selectedAccountId: meituanAccountId,
            snapshot: meituanSnapshot,
            loadError: meituanLoadError,
            busy,
            onSelectAccount: setMeituanAccountId,
            onReadAndSave: () => void handleMeituanReadAndSave(),
            readHint: "读取并保存会刷新美团 EBK 最新 snapshot，并用当前选中的携程数据中心飞书机器人推送摘要。",
          }}
          fliggy={{
            sessions,
            selectedAccountId: fliggyAccountId,
            snapshot: fliggySnapshot,
            loadError: fliggyLoadError,
            busy,
            onSelectAccount: setFliggyAccountId,
            onReadAndSave: () => void handleFliggyReadAndSave(),
            readHint: "读取并保存会刷新飞猪 EBK 最新 snapshot，并用当前选中的携程数据中心飞书机器人推送摘要。",
          }}
          shops={shops}
          dataCenterGroups={dataCenterGroups}
          onCreateCtripGroupForShop={(shop) => void handleCreateDataCenterGroup(shop.shop_key)}
          onRefreshDataCenterGroups={() => {
            void (async () => {
              setDataCenterGroups(await listDataCenterGroups());
            })();
          }}
        />
      ) : null}

      {activeTab === "competitor" ? (
        <CompetitorPanel
          groups={competitorGroups}
          selectedGroup={selectedCompetitorGroup}
          selectedKey={selectedCompetitorKey}
          hotels={competitorHotels}
          latest={competitorLatest}
          trend={competitorTrend}
          trendDays={trendDays}
          trendGranularity={trendGranularity}
          onTrendRangeChange={applyTrendRange}
          sessions={sessions}
          groupForm={groupForm}
          hotelForm={hotelForm}
          editingHotelId={editingHotelId}
          busy={busy}
          onSelectGroup={(groupKey) => {
            void syncCompetitorGroupState(groupKey, competitorGroups);
          }}
          onCreateGroup={() =>
            void runAction("create-group", async () => {
              const created = await createCompetitorGroup({
                group_key: `watch_${Date.now()}`,
                name: "新的竞品监控组",
              });
              const nextGroups = await listCompetitorGroups();
              setCompetitorGroups(nextGroups);
              await syncCompetitorGroupState(created.group_key, nextGroups);
            })
          }
          onDeleteGroup={() => void handleDeleteCompetitorGroup()}
          onGroupFormChange={setGroupForm}
          onSaveGroup={(extraInput) => void handleSaveCompetitorGroup(extraInput)}
          onSetPolling={(enabled) => void handleSetCompetitorPolling(enabled)}
          onHotelFormChange={setHotelForm}
          onSaveHotel={() => void handleSaveHotel()}
          onEditHotel={(hotel) => {
            setEditingHotelId(hotel.id);
            setHotelForm(hotelFormFromHotel(hotel));
          }}
          onCancelHotelEdit={() => {
            setEditingHotelId(null);
            setHotelForm(resetHotelFormForGroup());
          }}
          showDisabledHotels={showDisabledCompetitorHotels}
          onToggleShowDisabledHotels={(next) => {
            if (!selectedCompetitorGroup) {
              return;
            }
            void runAction("toggle-disabled-hotels", async () => {
              setShowDisabledCompetitorHotels(next);
              await refreshCompetitor(selectedCompetitorGroup.group_key, { includeDisabled: next });
            });
          }}
          onRestoreHotel={(hotel) =>
            selectedCompetitorGroup
              ? void runAction(`restore-hotel-${hotel.id}`, async () => {
                  await patchCompetitorHotel(selectedCompetitorGroup.group_key, hotel.id, { enabled: true });
                  await refreshCompetitor(selectedCompetitorGroup.group_key, { includeDisabled: true });
                  setCompetitorGroups(await listCompetitorGroups());
                  setNotice({ tone: "success", text: `已恢复「${hotel.alias}」。` });
                })
              : undefined
          }
          onRemoveHotel={(hotel) =>
            selectedCompetitorGroup
              ? void runAction(`remove-hotel-${hotel.id}`, async () => {
                  if (
                    !window.confirm(
                      `确认从列表移除「${hotel.alias}」？移除后不再参与轮询和简报，历史价格仍保留在数据库。`,
                    )
                  ) {
                    return;
                  }
                  await deleteCompetitorHotel(selectedCompetitorGroup.group_key, hotel.id);
                  await refreshCompetitor(selectedCompetitorGroup.group_key);
                  setCompetitorGroups(await listCompetitorGroups());
                  setNotice({ tone: "success", text: `已从列表移除「${hotel.alias}」。` });
                })
              : undefined
          }
          onRun={handleRunCompetitor}
        />
      ) : null}

      {activeTab === "calendar_prices" ? (
        <CalendarPricePanel
          groups={competitorGroups}
          selectedGroup={selectedCompetitorGroup}
          selectedKey={selectedCompetitorKey}
          config={calendarConfig}
          configForm={calendarConfigForm}
          matrix={calendarMatrix}
          alerts={calendarAlerts}
          schedulerStatus={calendarSchedulerStatus}
          sessions={sessions}
          busy={busy}
          onSelectGroup={(groupKey) => {
            void syncCalendarGroupState(groupKey);
          }}
          onConfigFormChange={setCalendarConfigForm}
          onSaveConfig={() => void handleSaveCalendarConfig()}
          onSetPolling={(enabled) => void handleSetCalendarPolling(enabled)}
          onSaveFeishuWebhook={(value) => void handleSaveCalendarFeishuWebhook(value)}
          onRun={(pushToFeishu) => void handleRunCalendarPrice(pushToFeishu)}
          onPushMatrixBrief={() => void handlePushCalendarMatrixBrief()}
        />
      ) : null}

      {activeTab === "price_alerts" ? (
        <PriceChangePanel
          groups={competitorGroups}
          selectedGroup={selectedCompetitorGroup}
          selectedKey={selectedCompetitorKey}
          latest={competitorLatest}
          priceChangeStrategy={priceChangeStrategy}
          priceChangeAlert={priceChangeAlert}
          busy={busy}
          onSelectGroup={(groupKey) => {
            void syncCompetitorGroupState(groupKey, competitorGroups, { resetHotelForm: false });
          }}
          onPriceChangeFormChange={setPriceChangeForm}
          onSavePriceChangeStrategy={() => void handleSavePriceChangeStrategy()}
          onEvaluatePriceChange={() => void handleEvaluatePriceChange()}
          onTogglePriceChangeStrategy={(nextEnabled) => void handleTogglePriceChangeStrategy(nextEnabled)}
          onSaveAlertTitle={(title) => void handleSavePriceChangeAlertTitle(title)}
        />
      ) : null}

      {activeTab === "review_management" ? (
        <ReviewManagementPanel
          platform={reviewPlatform}
          config={reviewConfig}
          catalog={reviewCatalog}
          templates={reviewTemplates}
          tasks={reviewTasks}
          busy={busy}
          groups={reviewGroups}
          groupKey={reviewGroupKey}
          onGroupChange={handleReviewGroupChange}
          onPlatformChange={setReviewPlatform}
          onConfigSave={handleReviewConfigSave}
          onFetch={handleReviewFetch}
          onImportHistory={handleReviewImportHistory}
          onAutoUpdateTemplates={handleReviewAutoUpdateTemplates}
          onProcessPending={handleReviewProcessPending}
          onTemplateSave={handleReviewTemplateSave}
          onTemplateDelete={handleReviewTemplateDelete}
        />
      ) : null}

      {activeTab === "history" ? <HistoryPanel /> : null}
      {activeTab === "bi_daily" ? <BiDailyPanel /> : null}

      <section className="advanced-maintenance">
        <button
          className="advanced-maintenance-toggle"
          type="button"
          aria-expanded={advancedMaintenanceOpen}
          onClick={() => {
            const nextOpen = !advancedMaintenanceOpen;
            setAdvancedMaintenanceOpen(nextOpen);
            if (!nextOpen) {
              setAdvancedMaintenanceView("llm_config");
            }
          }}
        >
          <Settings size={16} />
          高级维护
          <span className="advanced-maintenance-hint">兼容模式（高级）· LLM 配置</span>
          <span aria-hidden="true">{advancedMaintenanceOpen ? "▲" : "▼"}</span>
        </button>
        {advancedMaintenanceOpen ? (
          advancedMaintenanceView === "knowledge" ? (
            <KnowledgeManagementPanel
              supervisorApiKey={supervisorApiKey}
              onBack={() => setAdvancedMaintenanceView("llm_config")}
            />
          ) : (
            <ModelConfigPanel
              supervisorApiKey={supervisorApiKey}
              config={integrationConfig}
              form={integrationConfigForm}
              models={llmModels}
              modelsResult={llmModelsResult}
              busy={busy}
              testResult={llmConnectionTest}
              onFormChange={setIntegrationConfigForm}
              onSave={() => void handleSaveIntegrationConfig()}
              onLoadModels={() => void handleLoadLlmModels()}
              onTest={() => void handleTestLlmConnection()}
              onOpenKnowledge={() => setAdvancedMaintenanceView("knowledge")}
            />
          )
        ) : null}
        <button
          className="advanced-agent-toggle"
          type="button"
          aria-expanded={advancedAgentConfigOpen}
          onClick={() => {
            setAdvancedAgentConfigOpen((open) => {
              if (open) {
                setAdvancedNotice(null);
              }
              return !open;
            });
          }}
        >
          <ShieldCheck size={16} />
          高级配置
          <span className="advanced-maintenance-hint">Agent、飞书群、监控组与权限</span>
          <span aria-hidden="true">{advancedAgentConfigOpen ? "▲" : "▼"}</span>
        </button>
        {advancedAgentConfigOpen ? (
          <>
          <AdvancedAgentConfigPanel            overview={supervisorOverview}
            supervisorApiKey={supervisorApiKey}
            loading={busy === "load-supervisor-overview"}
            busy={busy}
            error={supervisorOverviewError}
            actionNotice={advancedNotice}
            admissionRevision={admissionRevision}
            sessions={sessions}
            competitorGroups={competitorGroups}
            onApiKeyChange={(value) => {
              setSupervisorApiKey(value);
              setRuntimeApiKey(value);
              if (!value.trim()) {
                setSupervisorOverview(null);
                setSupervisorOverviewError(null);
              }
            }}
            onLoad={() => void handleLoadSupervisorOverview()}
            onPatchProfile={handlePatchSupervisorProfile}
            onCreateProfile={handleCreateAgentProfile}
            onDeleteProfile={handleDeleteAgentProfile}
            onSetProfileCredentials={handleSetProfileCredentials}
            onPatchBinding={handlePatchSupervisorBinding}
            onCreateChatBinding={handleCreateChatBinding}
            onDeleteBinding={handleDeleteChatBinding}
            onSaveManualPricingChat={handleSaveManualPricingChat}
            onSaveCompetitorPricingAccount={handleSaveCompetitorPricingAccount}
            onBindEbkGroupChat={handleBindEbkGroupChat}
            onBindCompetitorGroupChat={handleBindCompetitorGroupChat}
            onSaveRole={handleSaveSupervisorRole}
          />
          <BrowserCachePanel apiKey={supervisorApiKey} />
          </>
        ) : null}
        <button
          className="advanced-agent-toggle"
          type="button"
          aria-expanded={pageHealthOpen}
          onClick={() => setPageHealthOpen((open) => !open)}
        >
          <Activity size={16} />
          页面健康
          <span className="advanced-maintenance-hint">页面结构 / selector 告警（只读）</span>
          <span aria-hidden="true">{pageHealthOpen ? "▲" : "▼"}</span>
        </button>
        {pageHealthOpen ? <PageHealthPanel /> : null}
      </section>
    </main>
  );
}

function CompetitorPanel(props: {
  groups: CompetitorWatchGroup[];
  selectedGroup: CompetitorWatchGroup | null;
  selectedKey: string;
  hotels: CompetitorHotel[];
  latest: CompetitorLatestSnapshot | null;
  trend: CompetitorPriceTrend | null;
  trendDays: number;
  trendGranularity: "run" | "hour" | "day";
  onTrendRangeChange: (days: number, granularity: "run" | "hour" | "day") => void;
  sessions: BrowserSession[];
  groupForm: CompetitorGroupForm;
  hotelForm: CompetitorHotelForm;
  editingHotelId: number | null;
  busy: string | null;
  onSelectGroup: (groupKey: string) => void;
  onCreateGroup: () => void;
  onDeleteGroup: () => void;
  onGroupFormChange: (form: CompetitorGroupForm) => void;
  onSaveGroup: (input?: {
    feishu_mobile_webhook_url?: string;
    feishu_chat_id?: string;
  }) => void;
  onSetPolling: (enabled: boolean) => void;
  onHotelFormChange: (form: CompetitorHotelForm) => void;
  onSaveHotel: () => void;
  onEditHotel: (hotel: CompetitorHotel) => void;
  onCancelHotelEdit: () => void;
  showDisabledHotels: boolean;
  onToggleShowDisabledHotels: (next: boolean) => void;
  onRestoreHotel: (hotel: CompetitorHotel) => void;
  onRemoveHotel: (hotel: CompetitorHotel) => void;
  onRun: (pushToFeishu: boolean) => void;
}) {
  const group = props.selectedGroup;
  const [mobileWebhookDraft, setMobileWebhookDraft] = useState("");
  useEffect(() => {
    setMobileWebhookDraft("");
  }, [group?.group_key]);
  const competitorSessions = props.sessions.filter((session) => canBindToCtripCompetitorWatch(session));
  const pricingSessions = props.sessions.filter((session) => canBindToCtripDataCenter(session));
  const selectedCompetitorSessionIsAvailable = competitorSessions.some(
    (session) => session.browser_session_id === props.groupForm.browser_session_id,
  );
  const selectedPricingSessionIsAvailable = pricingSessions.some(
    (session) => session.browser_session_id === props.groupForm.pricing_browser_session_id,
  );
  const competitorBrowserSelectValue = selectedCompetitorSessionIsAvailable ? props.groupForm.browser_session_id : "";
  const pricingBrowserSelectValue = selectedPricingSessionIsAvailable ? props.groupForm.pricing_browser_session_id : "";
  const selectedBindingMissing = Boolean(props.groupForm.browser_session_id) && !selectedCompetitorSessionIsAvailable;
  const selectedPricingBindingMissing = Boolean(props.groupForm.pricing_browser_session_id) && !selectedPricingSessionIsAvailable;
  const browserPoolMode = props.groupForm.browser_rotation_mode === "pool_round_robin";
  const selectedPoolMemberIds = new Set(props.groupForm.pool_member_browser_session_ids);
  const hotelSaveBusy = props.busy === "add-hotel" || props.busy === `hotel-${props.editingHotelId}`;
  const [trendExportNotice, setExportNotice] = useState<string | null>(null);
  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>竞品价格</h2>
          <p>维护本店和竞品酒店 URL，读取携程公开酒店页的可信 DOM 房价并入库生成趋势。</p>
        </div>
        <div className="action-strip">
          <button className="secondary-button" type="button" onClick={() => props.onSetPolling(true)} disabled={!canStartPolling(group) || props.busy === "patch-competitor-group"}>
            <Play size={16} /> {startPollingButtonText()}
          </button>
          <button className="secondary-button" type="button" onClick={() => props.onSetPolling(false)} disabled={!canStopPolling(group) || props.busy === "patch-competitor-group"}>
            <X size={16} /> {stopPollingButtonText()}
          </button>
          <button className="primary-button" type="button" onClick={() => props.onRun(true)} disabled={!group || props.busy === "run-competitor"}>
            <Play size={16} /> {manualCompetitorRunButtonText()}
          </button>
        </div>
      </div>
      <div className="split-grid">
        <div className="panel">
          <div className="panel-title-row">
            <h3>监控组</h3>
            <div className="inline-actions">
              <button type="button" onClick={props.onCreateGroup} title="新建监控组"><Plus size={16} /></button>
              <button
                type="button"
                onClick={props.onDeleteGroup}
                title="删除当前监控组"
                disabled={!group || props.busy === "delete-competitor-group"}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
          <label>
            当前组
            <select value={props.selectedKey} onChange={(event) => props.onSelectGroup(event.target.value)} disabled={!props.groups.length}>
              {props.groups.map((item) => (
                <option key={item.group_key} value={item.group_key}>{item.name}</option>
              ))}
            </select>
          </label>
          {group ? (
            <div className="form-grid single">
              <label>
                名称
                <input
                  value={props.groupForm.name}
                  onChange={(event) => props.onGroupFormChange({ ...props.groupForm, name: event.target.value })}
                />
              </label>
              <label>
                推送卡片标题
                <input
                  value={props.groupForm.brief_title}
                  placeholder={props.groupForm.name || group.name}
                  onChange={(event) => props.onGroupFormChange({ ...props.groupForm, brief_title: event.target.value })}
                />
              </label>
              <div className="form-field-block">
                <span className="field-label">竞对读取账号</span>
                <div className="segmented-control" role="group" aria-label="竞对读取账号模式">
                  <button
                    className={browserPoolMode ? "" : "active"}
                    type="button"
                    onClick={() => props.onGroupFormChange({ ...props.groupForm, browser_rotation_mode: "fixed" })}
                  >
                    固定账号
                  </button>
                  <button
                    className={browserPoolMode ? "active" : ""}
                    type="button"
                    onClick={() => props.onGroupFormChange({ ...props.groupForm, browser_rotation_mode: "pool_round_robin" })}
                  >
                    账号池轮换
                  </button>
                </div>
                <span className="field-hint">只用于携程官网酒店价格读取；不会决定调价审批账号。</span>
              </div>
              {!browserPoolMode ? (
                <label>
                  固定竞对读取账号
                  <select value={competitorBrowserSelectValue} onChange={(event) => {
                    props.onGroupFormChange({ ...props.groupForm, browser_session_id: event.target.value });
                  }} disabled={!competitorSessions.length}>
                    <option value="" disabled hidden>请选择携程官网账号</option>
                    {competitorSessions.map((session) => (
                      <option key={session.browser_session_id} value={session.browser_session_id}>{session.display_name}</option>
                    ))}
                  </select>
                  {!competitorSessions.length ? <span className="field-hint">请先在账号与浏览器里新增竞品价格账号。</span> : null}
                  {selectedBindingMissing ? <span className="field-hint">当前绑定账号不在可用携程官网账号列表中，请重新选择后保存。</span> : null}
                </label>
              ) : (
                <div className="form-field-block">
                  <span className="field-label">账号池成员</span>
                  <div className="pool-member-list">
                    {competitorSessions.map((session) => {
                      const checked = selectedPoolMemberIds.has(session.browser_session_id);
                      return (
                        <label className="check-row" key={session.browser_session_id}>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(event) => {
                              const current = props.groupForm.pool_member_browser_session_ids;
                              const next = event.target.checked
                                ? [...current, session.browser_session_id]
                                : current.filter((id) => id !== session.browser_session_id);
                              props.onGroupFormChange({ ...props.groupForm, pool_member_browser_session_ids: Array.from(new Set(next)) });
                            }}
                          />
                          <span>{session.display_name} · {session.platform_account_id}</span>
                        </label>
                      );
                    })}
                  </div>
                  {!competitorSessions.length ? <span className="field-hint">请先在账号与浏览器里新增竞品价格账号。</span> : null}
                  {competitorSessions.length && !props.groupForm.pool_member_browser_session_ids.length ? (
                    <span className="field-hint">至少选择一个竞对读取账号后再启动轮询。</span>
                  ) : null}
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={props.groupForm.pool_failover_enabled}
                      onChange={(event) => props.onGroupFormChange({ ...props.groupForm, pool_failover_enabled: event.target.checked })}
                    />
                    <span>账号异常时自动换下一个池成员</span>
                  </label>
                  <label>
                    失败冷却分钟
                    <input
                      value={props.groupForm.pool_cooldown_minutes}
                      inputMode="numeric"
                      onChange={(event) => props.onGroupFormChange({ ...props.groupForm, pool_cooldown_minutes: event.target.value })}
                    />
                  </label>
                </div>
              )}
              <label>
                调价 EBK 账号（仅写价链路）
                <span className="field-hint">只用于价格变化提醒后的降价参考卡、审批与安全判断；不用于 EBK、竞品、日历价只读查询配对。</span>
                <select value={pricingBrowserSelectValue} onChange={(event) => {
                  props.onGroupFormChange({ ...props.groupForm, pricing_browser_session_id: event.target.value });
                }} disabled={!pricingSessions.length}>
                  <option value="">不绑定：只读查询正常，禁止写价</option>
                  {pricingSessions.map((session) => (
                    <option key={session.browser_session_id} value={session.browser_session_id}>
                      {session.display_name} · {connectionModeLabel(session.connection_mode)}
                    </option>
                  ))}
                </select>
                {!pricingSessions.length ? <span className="field-hint">请先在账号与浏览器里新增 EBK 后台账号。</span> : null}
                {selectedPricingBindingMissing ? <span className="field-hint">当前调价账号不在可用 EBK 账号列表中，请重新选择后保存。</span> : null}
                {group.pricing_account_configured ? (
                  <span className="field-hint">
                    当前绑定：{connectionModeLabel(group.pricing_connection_mode)}，{group.pricing_can_auto_write_price ? "可在 Feishu 审批通过后继续调价流程" : "仅生成建议，不允许自动改价"}。
                  </span>
                ) : (
                  <span className="field-hint">飞书群 ID 与 EBK/竞品监控组决定只读上下文；这里仅决定降价参考卡和后续 Feishu 审批写价能否继续，未绑定时禁止写价。</span>
                )}
              </label>
              <label>
                轮询模式
                <select
                  value={props.groupForm.schedule_mode}
                  onChange={(event) => props.onGroupFormChange({
                    ...props.groupForm,
                    schedule_mode: event.target.value as "fixed_slots" | "last_run_interval",
                  })}
                >
                  <option value="fixed_slots">固定时间点</option>
                  <option value="last_run_interval">按上次读取间隔</option>
                </select>
              </label>
              <label>
                开始时间
                <input
                  value={props.groupForm.schedule_start_time}
                  placeholder="06:00"
                  onChange={(event) => props.onGroupFormChange({ ...props.groupForm, schedule_start_time: event.target.value })}
                />
              </label>
              <label>
                结束时间
                <input
                  value={props.groupForm.schedule_end_time}
                  placeholder="01:00"
                  onChange={(event) => props.onGroupFormChange({ ...props.groupForm, schedule_end_time: event.target.value })}
                />
              </label>
              <label>
                间隔分钟
                <input
                  value={props.groupForm.schedule_interval_minutes}
                  inputMode="numeric"
                  onChange={(event) => props.onGroupFormChange({ ...props.groupForm, schedule_interval_minutes: event.target.value })}
                />
              </label>
              <label>
                推送日期范围
                <select
                  value={props.groupForm.push_date_scope}
                  onChange={(event) => props.onGroupFormChange({
                    ...props.groupForm,
                    push_date_scope: event.target.value as "today_only" | "today_tomorrow",
                  })}
                >
                  <option value="today_tomorrow">今日 + 明日</option>
                  <option value="today_only">仅今日</option>
                </select>
              </label>
              <p className="field-hint">飞书机器人推送折叠竖排全量房型卡片。</p>
              <label>
                手机卡片飞书机器人地址
                <div className="field-action-row">
                  <input
                    value={mobileWebhookDraft}
                    placeholder={group.feishu_mobile_webhook_configured ? `已配置：${group.feishu_mobile_webhook_masked ?? ""}` : "粘贴手机卡片飞书机器人地址"}
                    onChange={(event) => setMobileWebhookDraft(event.target.value)}
                  />
                  <button
                    className="secondary-button"
                    disabled={!mobileWebhookDraft.trim() || props.busy === "patch-competitor-group"}
                    onClick={() => {
                      props.onSaveGroup({ feishu_mobile_webhook_url: mobileWebhookDraft.trim() });
                      setMobileWebhookDraft("");
                    }}
                    type="button"
                  >
                    <Save size={16} /> 保存
                  </button>
                  <button
                    className="secondary-button"
                    disabled={!group.feishu_mobile_webhook_configured || props.busy === "patch-competitor-group"}
                    onClick={() => {
                      props.onSaveGroup({ feishu_mobile_webhook_url: "" });
                      setMobileWebhookDraft("");
                    }}
                    type="button"
                  >
                    <X size={16} /> 清除
                  </button>
                </div>
              </label>
              <label>
                运营问答上下文群（兼价格变化提醒群）
                <div className="field-action-row">
                  <input
                    value={props.groupForm.feishu_chat_id}
                    placeholder={group.feishu_chat_id_configured ? `已绑定：${group.feishu_chat_id_masked ?? ""}` : "oc_ 开头的飞书群 ID"}
                    onChange={(event) => props.onGroupFormChange({ ...props.groupForm, feishu_chat_id: event.target.value })}
                  />
                  <button
                    className="secondary-button"
                    disabled={!props.groupForm.feishu_chat_id.trim() || props.busy === "patch-competitor-group"}
                    onClick={() => {
                      props.onSaveGroup({ feishu_chat_id: props.groupForm.feishu_chat_id.trim() });
                      props.onGroupFormChange({ ...props.groupForm, feishu_chat_id: "" });
                    }}
                    type="button"
                  >
                    <Save size={16} /> 保存
                  </button>
                  <button
                    className="secondary-button"
                    disabled={!group.feishu_chat_id_configured || props.busy === "patch-competitor-group"}
                    onClick={() => {
                      props.onSaveGroup({ feishu_chat_id: "" });
                      props.onGroupFormChange({ ...props.groupForm, feishu_chat_id: "" });
                    }}
                    type="button"
                  >
                    <X size={16} /> 清除
                  </button>
                </div>
                <span className="field-hint">普通经营问答、今日简报、跑一次 EBK、竞品价和日历价查询会用它识别当前门店；竞品价格变化超过阈值后，也把这里作为价格变化提醒群和调价参考卡投递位置。实际写价账号请在本监控组里选择“调价 EBK 账号（仅写价链路）”。</span>
              </label>
              <div className="toggle-row">
                <label>
                  <input
                    type="checkbox"
                    checked={props.groupForm.enabled}
                    onChange={(event) => props.onGroupFormChange({ ...props.groupForm, enabled: event.target.checked })}
                  /> 启用
                </label>
              </div>
              <button className="primary-button" type="button" onClick={() => props.onSaveGroup()} disabled={props.busy === "patch-competitor-group"}>
                <Save size={16} /> 保存当前监控组设置
              </button>
            </div>
          ) : <EmptyState text="还没有竞品监控组。" />}
        </div>

        <div className="panel">
          <h3>运行状态</h3>
          {group ? (
            <div className="metric-grid">
              <Metric label="配置" value={statusLabel(group.runtime_config_status ?? "config_required")} />
              <Metric label="本店" value={String(group.enabled_own_hotel_count ?? 0)} />
              <Metric label="竞品" value={String(group.enabled_competitor_hotel_count ?? 0)} />
              <Metric label="读取窗口" value={group.schedule_state?.in_window ? "窗口内" : "窗口外"} />
              <Metric label="下一次轮询" value={group.schedule_state?.next_run_at ? formatTime(group.schedule_state.next_run_at) : "-"} />
              <Metric label="最近读取" value={group.latest_run ? statusLabel(group.latest_run.status) : "暂无"} />
              <Metric label="读取时间" value={group.latest_run ? formatTime(group.latest_run.captured_at) : "-"} />
            </div>
          ) : <EmptyState text="请选择或新建监控组。" />}
        </div>
      </div>

      <div className="split-grid">
        <div className="panel">
          <div className="panel-title-row">
            <h3>{props.editingHotelId ? "编辑酒店" : "添加酒店"}</h3>
            {props.editingHotelId ? (
              <button type="button" onClick={props.onCancelHotelEdit} title="取消编辑">
                <X size={16} />
              </button>
            ) : null}
          </div>
          <div className="form-grid single">
            <label>
              类型
              <select value={props.hotelForm.role} onChange={(event) => props.onHotelFormChange({ ...props.hotelForm, role: event.target.value as "own" | "competitor" })}>
                <option value="own">本店</option>
                <option value="competitor">竞品</option>
              </select>
            </label>
            <label>
              名称
              <input value={props.hotelForm.alias} onChange={(event) => props.onHotelFormChange({ ...props.hotelForm, alias: event.target.value })} placeholder="运营可识别全称" />
            </label>
            <label>
              简称
              <input
                value={props.hotelForm.short_alias}
                onChange={(event) => props.onHotelFormChange({ ...props.hotelForm, short_alias: event.target.value })}
                placeholder="如：江景店 / 步行街店"
                maxLength={32}
              />
            </label>
            <label>
              携程酒店详情 URL
              <input value={props.hotelForm.detail_url} onChange={(event) => props.onHotelFormChange({ ...props.hotelForm, detail_url: event.target.value })} placeholder="https://hotels.ctrip.com/..." />
            </label>
            <label>
              排序
              <input value={props.hotelForm.sort_order} onChange={(event) => props.onHotelFormChange({ ...props.hotelForm, sort_order: event.target.value })} inputMode="numeric" />
            </label>
            <label className="inline-check">
              <input type="checkbox" checked={props.hotelForm.enabled} onChange={(event) => props.onHotelFormChange({ ...props.hotelForm, enabled: event.target.checked })} /> 启用
            </label>
          </div>
          <button className="primary-button" type="button" onClick={props.onSaveHotel} disabled={!group || hotelSaveBusy}>
            <Save size={16} /> {props.editingHotelId ? "更新酒店" : "保存酒店"}
          </button>
        </div>

        <div className="panel">
          <div className="panel-title-row">
            <h3>酒店配置</h3>
            <label>
              <input
                type="checkbox"
                checked={props.showDisabledHotels}
                onChange={(event) => props.onToggleShowDisabledHotels(event.target.checked)}
              />
              显示已停用
            </label>
          </div>
          <div className="table-list">
            {props.hotels.map((hotel) => (
              <div className="hotel-row" key={hotel.id}>
                <div>
                  <strong>
                    {hotel.short_alias ? `${hotel.short_alias} · ` : ""}
                    {hotel.alias}
                    {hotel.role === "own" ? "（本店）" : ""}
                  </strong>
                  <span>{hotel.ctrip_hotel_id} · {hotel.enabled ? "启用" : "已停用"} · {statusLabel(hotel.last_status ?? "未读取")}</span>
                </div>
                <div className="row-actions">
                  {hotel.enabled ? (
                    <>
                      <button type="button" onClick={() => props.onEditHotel(hotel)} title="编辑酒店"><Pencil size={16} /></button>
                      {hotel.role !== "own" ? (
                        <button
                          type="button"
                          onClick={() => props.onRemoveHotel(hotel)}
                          title="从列表移除"
                        >
                          <Trash2 size={16} />
                        </button>
                      ) : null}
                    </>
                  ) : (
                    <button type="button" onClick={() => props.onRestoreHotel(hotel)} title="恢复启用">
                      <RefreshCw size={16} />
                    </button>
                  )}
                </div>
              </div>
            ))}
            {!props.hotels.length ? (
              <EmptyState
                text={
                  props.showDisabledHotels
                    ? "当前没有酒店配置。"
                    : "还没有启用的酒店配置。先添加 1 个本店和至少 1 个竞品，或勾选「显示已停用」。"
                }
              />
            ) : null}
          </div>
        </div>
      </div>

      <div className="panel">
        <h3>最新可信房价</h3>
        {props.latest ? <LatestCompetitorSnapshot snapshot={props.latest} /> : <EmptyState text="还没有竞品读取记录。" />}
      </div>

      <div className="panel">
        <div className="panel-title-row trend-title-row">
          <h3>可信价格趋势</h3>
          <span>仅展示可信 DOM 房价趋势</span>
          <div className="inline-edit-row">
            {[1, 7, 30].map((days) => (
              <button
                key={days}
                type="button"
                className={props.trendDays === days ? "primary-button" : "secondary-button"}
                onClick={() => props.onTrendRangeChange(days, props.trendGranularity)}
              >
                {days === 1 ? "今天" : `近 ${days} 天`}
              </button>
            ))}
            {(["run", "hour", "day"] as const).map((granularity) => (
              <button
                key={granularity}
                type="button"
                className={props.trendGranularity === granularity ? "primary-button" : "secondary-button"}
                onClick={() => props.onTrendRangeChange(props.trendDays, granularity)}
              >
                {granularity === "run" ? "按次" : granularity === "hour" ? "按小时" : "按天"}
              </button>
            ))}
            <button
              type="button"
              className="secondary-button"
              title="按当前范围与粒度导出 CSV"
              onClick={() => {
                void exportCompetitorPriceTrendCsv({
                  watchGroupKey: props.selectedKey,
                  days: props.trendDays,
                  granularity: props.trendGranularity,
                }).catch((error) => {
                  setExportNotice(
                    error instanceof Error ? error.message : "导出失败。",
                  );
                });
              }}
            >
              <Download size={16} /> 导出 CSV
            </button>
          </div>
        </div>
        {props.trend && props.trend.point_count > 0 ? (
          <CompetitorTrendPanel trend={props.trend} />
        ) : (
          <EmptyState text="还没有可展示的可信趋势点。完成一次竞品读取后会自动生成。" />
        )}
        {trendExportNotice ? <p className="notice-error">{trendExportNotice}</p> : null}
      </div>
    </section>
  );
}

function LatestCompetitorSnapshot({ snapshot }: { snapshot: CompetitorLatestSnapshot }) {
  return (
    <div className="latest-grid">
      {snapshot.hotels.map((hotel) => (
        <div className="latest-hotel" key={hotel.hotel_config.id}>
          <strong>{hotel.hotel_config.alias}{hotel.hotel_config.role === "own" ? "（本店）" : ""}</strong>
          {Object.entries(hotel.snapshots).map(([label, item]) => (
            <div className="rate-block" key={label}>
              <span>{label === "today" ? "今日" : "明日"} · {statusLabel(item.status)} · {item.checkin_date ?? "-"}</span>
              {item.rates.filter((rate) => rate.is_trusted).slice(0, 4).map((rate, index) => (
                <p key={`${label}-${index}`}>
                  ¥{rate.price_amount_yuan ?? "-"} · {breakfastLabel(rate.breakfast_kind)} · {rate.room_type || "-"} · {availabilityLabel(rate.availability_status)}
                </p>
              ))}
              {!item.rates.some((rate) => rate.is_trusted) ? <p>暂无可信 DOM 房价</p> : null}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function CompetitorTrendPanel({ trend }: { trend: CompetitorPriceTrend }) {
  return <CompetitorTrendChart trend={trend} />;
}

function CalendarPricePanel(props: {
  groups: CompetitorWatchGroup[];
  selectedGroup: CompetitorWatchGroup | null;
  selectedKey: string;
  config: CalendarPriceWatchConfig | null;
  configForm: CalendarPriceConfigForm;
  matrix: CalendarPriceMatrix | null;
  alerts: CalendarPriceAlert[];
  schedulerStatus: CalendarSchedulerStatus | null;
  sessions: BrowserSession[];
  busy: string | null;
  onSelectGroup: (groupKey: string) => void;
  onConfigFormChange: (form: CalendarPriceConfigForm) => void;
  onSaveConfig: () => void;
  onSetPolling: (enabled: boolean) => void;
  onSaveFeishuWebhook: (value: string) => void;
  onRun: (pushToFeishu: boolean) => void;
  onPushMatrixBrief: () => void;
}) {
  const group = props.selectedGroup;
  const config = props.config;
  const matrix = props.matrix;
  const [feishuWebhookDraft, setFeishuWebhookDraft] = useState("");
  const [matrixBriefTimeDraft, setMatrixBriefTimeDraft] = useState("12:00");
  useEffect(() => {
    setFeishuWebhookDraft("");
    setMatrixBriefTimeDraft("12:00");
  }, [group?.group_key]);
  const matrixBriefSchedule = config?.matrix_brief_schedule_state;
  const matrixBriefSlots =
    matrixBriefSchedule?.slots ??
    (config?.matrix_brief_push_times ?? []).map((push_time) => ({
      push_time,
      next_at: null as string | null,
      last_pushed_at: null as string | null,
    }));
  const competitorColumns =
    matrix?.competitor_hotels?.length
      ? matrix.competitor_hotels
      : Array.from(
          new Map(
            (matrix?.dates ?? [])
              .flatMap((row) => row.competitors ?? [])
              .map((item) => [item.hotel_alias, { alias: item.hotel_alias, short_alias: null }]),
          ).values(),
        );
  const calendarSessions = props.sessions.filter((session) => canBindToCtripCompetitorWatch(session));
  const selectedCalendarSession = calendarSessions.find(
    (session) => session.browser_session_id === props.configForm.browser_session_id,
  );
  const calendarBrowserSelectValue =
    selectedCalendarSession?.browser_session_id ?? props.configForm.browser_session_id ?? "";
  const competitorBrowserLabel = group?.browser_session_id
    ? `竞品读取：${group.browser_session_id}`
    : "竞品读取：未配置";
  const summary = calendarMatrixSummary({
    status: matrix?.status,
    alert_count: matrix?.alert_count ?? props.alerts.length,
    future_days: matrix?.future_days ?? config?.future_days ?? 15,
  });

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>日历价监测</h2>
          <p>只读比较本店与竞对未来日历价，触发预警后请人工查看房型、早餐和促销差异。本页不提供写价执行入口。</p>
        </div>
        <div className="action-strip">
          <button
            className="secondary-button"
            type="button"
            disabled={
              !group ||
              !canStartPolling({
                enabled: group.enabled,
                auto_poll_enabled: props.config?.auto_poll_enabled,
              }) ||
              props.busy === "patch-calendar-config"
            }
            onClick={() => props.onSetPolling(true)}
          >
            <Play size={16} /> {startPollingButtonText()}
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={
              !group ||
              !canStopPolling({
                enabled: group.enabled,
                auto_poll_enabled: props.config?.auto_poll_enabled,
              }) ||
              props.busy === "patch-calendar-config"
            }
            onClick={() => props.onSetPolling(false)}
          >
            <X size={16} /> {stopPollingButtonText()}
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={!group || props.busy === "run-calendar-price"}
            onClick={() => props.onRun(true)}
          >
            <Play size={16} /> 立即读取并推送预警
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!group || props.busy === "push-calendar-matrix-brief"}
            onClick={props.onPushMatrixBrief}
          >
            <Play size={16} /> 立即推送矩阵到飞书
          </button>
        </div>
      </div>

      <div className="panel">
        <h3>当前状态</h3>
        <label>
          监控组
          <select
            value={props.selectedKey}
            onChange={(event) => props.onSelectGroup(event.target.value)}
            disabled={!props.groups.length}
          >
            {props.groups.map((item) => (
              <option key={item.group_key} value={item.group_key}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {group ? (
          <div className="metric-grid">
            <Metric label="监测状态" value={summary} />
            <Metric
              label="自动轮询"
              value={
                config?.auto_poll_enabled
                  ? pollingStatusText({
                      enabled: true,
                      auto_poll_enabled: true,
                      scheduler_status: props.schedulerStatus ?? undefined,
                    })
                  : "未开启"
              }
              detail={schedulerDetailText(props.schedulerStatus ?? undefined)}
            />
            <Metric
              label="读取窗口"
              value={`${config?.schedule_start_time ?? group.schedule_start_time ?? "06:00"} - ${config?.schedule_end_time ?? group.schedule_end_time ?? "01:00"}`}
              detail="默认跟随竞品价格的开始时间和结束时间"
            />
            <Metric
              label="轮询模式"
              value={calendarScheduleModeLabel(config?.schedule_mode ?? group.schedule_mode)}
              detail="跟随竞品价格轮询模式；固定时间点按窗口起点对齐，按上次读取间隔从上次日历价读取时间起算"
            />
            <Metric
              label="下一次轮询"
              value={config?.schedule_state?.next_run_at ? formatTime(config.schedule_state.next_run_at) : "-"}
            />
            <Metric
              label="日历读取账号"
              value={
                config?.browser_session_configured
                  ? config.browser_session_id ?? "已配置"
                  : "未配置"
              }
              detail={`独立 Profile，与竞品读取账号分开可避免互抢浏览器。${competitorBrowserLabel}`}
            />
            <Metric
              label="最近读取"
              value={matrix?.run ? statusLabel(matrix.run.status) : "暂无"}
            />
            <Metric
              label="读取时间"
              value={matrix?.run ? formatTime(matrix.run.captured_at) : "-"}
            />
            <Metric
              label="预警机器人"
              value={group.feishu_webhook_configured ? "已配置" : "未配置"}
              detail={group.feishu_webhook_masked ?? undefined}
            />
            <Metric
              label="矩阵定时推送"
              value={matrixBriefStatusLabel(config?.matrix_brief_enabled)}
              detail={
                config?.matrix_brief_enabled
                  ? matrixBriefTimesLabel(
                      config.matrix_brief_push_times ?? [
                        config.matrix_brief_morning_push_time,
                        config.matrix_brief_evening_push_time,
                      ],
                    )
                  : "开启后每天在固定时间重新读取并推送完整矩阵"
              }
            />
            {matrixBriefSlots.map((slot) => (
              <Metric
                key={slot.push_time}
                label={`下次 ${slot.push_time}`}
                value={slot.next_at ? formatTime(slot.next_at) : "-"}
                detail={
                  slot.last_pushed_at ? `上次 ${formatTime(slot.last_pushed_at)}` : undefined
                }
              />
            ))}
          </div>
        ) : (
          <EmptyState text="请先创建并配置竞品监控组。" />
        )}
      </div>

      <div className="split-grid">
        <div className="panel">
          <h3>配置</h3>
          <label>
            未来天数（1-30）
            <input
              type="number"
              min={1}
              max={30}
              value={props.configForm.future_days}
              onChange={(event) =>
                props.onConfigFormChange({ ...props.configForm, future_days: event.target.value })
              }
              disabled={!group}
            />
          </label>
          <label>
            本店偏高阈值（元，对比最低竞对）
            <input
              type="number"
              min={0}
              value={props.configForm.high_gap_threshold_yuan}
              onChange={(event) =>
                props.onConfigFormChange({
                  ...props.configForm,
                  high_gap_threshold_yuan: event.target.value,
                })
              }
              disabled={!group}
            />
          </label>
          <label>
            本店明显低于竞对阈值（元，对比最低竞对）
            <input
              type="number"
              min={0}
              value={props.configForm.low_gap_threshold_yuan}
              onChange={(event) =>
                props.onConfigFormChange({
                  ...props.configForm,
                  low_gap_threshold_yuan: event.target.value,
                })
              }
              disabled={!group}
            />
          </label>
          <label>
            轮询间隔（分钟）
            <input
              type="number"
              min={1}
              value={props.configForm.schedule_interval_minutes}
              onChange={(event) =>
                props.onConfigFormChange({
                  ...props.configForm,
                  schedule_interval_minutes: event.target.value,
                })
              }
              disabled={!group}
            />
            <span className="field-hint">
              日历价独立间隔；轮询模式与读取窗口跟随竞品价格配置。
              {calendarScheduleModeLabel(config?.schedule_mode ?? group?.schedule_mode) === "固定时间点"
                ? "固定时间点模式下按窗口起点与间隔对齐整点槽位。"
                : "按上次读取间隔模式下从上次日历价读取完成时间起算。"}
            </span>
          </label>
          <label className="form-field-wide">
            日历价读取账号（独立 Profile）
            <select
              value={calendarBrowserSelectValue}
              onChange={(event) =>
                props.onConfigFormChange({
                  ...props.configForm,
                  browser_session_id: event.target.value,
                })
              }
              disabled={!group || !calendarSessions.length}
            >
              <option value="">请选择浏览器账号</option>
              {calendarSessions.map((session) => (
                <option key={session.browser_session_id} value={session.browser_session_id}>
                  {session.display_name || session.platform_account_id} · {session.browser_session_id}
                </option>
              ))}
            </select>
            <span className="field-hint">
              请选择用途为「竞品价格」的浏览器账号；可与竞品轮询绑定同一携程登录，但必须是不同 Profile/窗口。
              {!calendarSessions.length ? "请先在账号与浏览器里新增竞品价格账号。" : null}
            </span>
          </label>
          <label className="form-field-wide">
            预警推送标题
            <input
              value={props.configForm.alert_title}
              onChange={(event) =>
                props.onConfigFormChange({ ...props.configForm, alert_title: event.target.value })
              }
              placeholder={group?.name ? `${group.name}日历价监测` : "例如：澜江路日历价监测"}
              disabled={!group}
            />
          </label>
          <label className="form-field-wide">
            日历价预警机器人地址
            <div className="field-action-row">
              <input
                value={feishuWebhookDraft}
                onChange={(event) => setFeishuWebhookDraft(event.target.value)}
                placeholder={
                  group?.feishu_webhook_configured
                    ? `已配置：${group.feishu_webhook_masked ?? ""}`
                    : "粘贴飞书机器人地址"
                }
                disabled={!group}
              />
              <button
                className="secondary-button"
                type="button"
                disabled={!group || !feishuWebhookDraft.trim() || props.busy === "patch-calendar-feishu"}
                onClick={() => {
                  props.onSaveFeishuWebhook(feishuWebhookDraft.trim());
                  setFeishuWebhookDraft("");
                }}
              >
                <Save size={16} /> 保存机器人
              </button>
              <button
                className="secondary-button"
                type="button"
                disabled={!group?.feishu_webhook_configured || props.busy === "patch-calendar-feishu"}
                onClick={() => {
                  props.onSaveFeishuWebhook("");
                  setFeishuWebhookDraft("");
                }}
              >
                <X size={16} /> 清除机器人
              </button>
            </div>
          </label>
          <div className="panel-subsection">
            <h4>矩阵定时推送</h4>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={props.configForm.matrix_brief_enabled}
                onChange={(event) =>
                  props.onConfigFormChange({
                    ...props.configForm,
                    matrix_brief_enabled: event.target.checked,
                  })
                }
                disabled={!group}
              />
              开启矩阵定时简报（到点重新读取并推送，默认每天 10:00 / 15:00 / 21:00）
            </label>
            <label>
              矩阵推送标题
              <input
                value={props.configForm.matrix_brief_title}
                onChange={(event) =>
                  props.onConfigFormChange({
                    ...props.configForm,
                    matrix_brief_title: event.target.value,
                  })
                }
                placeholder={group?.name ? `${group.name}日历价矩阵` : "例如：澜江路日历价矩阵"}
                disabled={!group}
              />
            </label>
            <div className="matrix-brief-times">
              <span className="field-label">推送时刻</span>
              <div className="matrix-brief-time-list">
                {props.configForm.matrix_brief_push_times.map((slot) => (
                  <div className="matrix-brief-time-chip" key={slot}>
                    <span>{slot}</span>
                    <button
                      className="ghost-button"
                      type="button"
                      disabled={!group || props.configForm.matrix_brief_push_times.length <= 1}
                      onClick={() =>
                        props.onConfigFormChange({
                          ...props.configForm,
                          matrix_brief_push_times: removeMatrixBriefPushTime(
                            props.configForm.matrix_brief_push_times,
                            slot,
                          ),
                        })
                      }
                      aria-label={`删除推送时刻 ${slot}`}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <div className="field-action-row">
                <label>
                  新增时刻
                  <input
                    type="time"
                    value={matrixBriefTimeDraft}
                    onChange={(event) => setMatrixBriefTimeDraft(event.target.value)}
                    disabled={!group}
                  />
                </label>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={!group || !matrixBriefTimeDraft}
                  onClick={() => {
                    props.onConfigFormChange({
                      ...props.configForm,
                      matrix_brief_push_times: addMatrixBriefPushTime(
                        props.configForm.matrix_brief_push_times,
                        matrixBriefTimeDraft,
                      ),
                    });
                  }}
                >
                  <Plus size={16} /> 添加
                </button>
              </div>
            </div>
            <span className="field-hint">
              可增删推送时刻，至少保留 1 个；矩阵简报与预警推送共用上方机器人地址。
            </span>
          </div>
          <div className="action-strip">
            <button
              className="primary-button"
              type="button"
              disabled={!group || props.busy === "patch-calendar-config"}
              onClick={props.onSaveConfig}
            >
              <Save size={16} /> 保存配置
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={!group || props.busy === "run-calendar-price"}
              onClick={() => props.onRun(false)}
            >
              <Play size={16} /> 仅读取不入群
            </button>
          </div>
        </div>
        <div className="panel">
          <h3>数据口径</h3>
          <div className="policy-list">
            <span>读取窗口默认跟随竞品价格的开始时间和结束时间配置。</span>
            <span>日历价使用本页单独绑定的浏览器 Profile；竞品价格使用竞品页自己的读取账号。</span>
            <span>两个 Profile 可登录同一携程账号，但必须是不同浏览器实例，才能并行读取。</span>
            <span>只使用携程公共页日历 DOM 可信价格，不进入调价执行链。</span>
            <span>本店价格高于竞对最低价且达到阈值时，发送「本店偏高」预警；持平或最便宜时不提醒。</span>
            <span>本店价格明显低于竞对最低价且达到阈值时，可发送「明显低于竞对」预警；可在配置中单独关闭此类飞书推送。</span>
            <span>矩阵定时推送会在配置的时刻重新读取并推送完整矩阵，与预警推送独立；默认 10:00 / 15:00 / 21:00。</span>
            <span>缺失、登录跳转或页面结构变化会标记为待复核，不会写入虚构价格。</span>
          </div>
        </div>
      </div>

      <div className="panel">
        <h3>未来价格矩阵</h3>
        {matrix?.dates?.length ? (
          <div
            className="calendar-matrix"
            style={{
              ["--calendar-matrix-cols" as string]: String(2 + competitorColumns.length),
            }}
          >
            <div className="calendar-row calendar-row-head calendar-row-matrix">
              <span>日期</span>
              <span title={matrix.dates[0]?.own?.hotel_alias || "本店"}>
                {matrix.own_short_alias || "本店"}
              </span>
              {competitorColumns.map((hotel) => (
                <span key={hotel.alias} title={hotel.alias}>
                  {hotel.short_alias || hotel.alias}
                </span>
              ))}
            </div>
            {matrix.dates.map((row) => {
              const rowAlerts = row.alerts ?? [];
              const highAlert = rowAlerts.some((item) => item.alert_type === "own_price_high");
              const lowAlert = rowAlerts.some((item) => item.alert_type === "own_price_low");
              const priceByAlias = new Map(
                (row.competitors ?? []).map((item) => [item.hotel_alias, item]),
              );
              return (
                <div className="calendar-row calendar-row-matrix" key={row.checkin_date}>
                  <span>{row.checkin_date.slice(5)}</span>
                  <span
                    className={
                      highAlert ? "calendar-alert-high" : lowAlert ? "calendar-alert-low" : ""
                    }
                    title={calendarDateStatusLabel(row.own?.status)}
                  >
                    {calendarPriceDisplay(row.own?.price_amount_yuan)}
                  </span>
                  {competitorColumns.map((hotel) => {
                    const competitor = priceByAlias.get(hotel.alias);
                    return (
                      <span
                        key={`${row.checkin_date}-${hotel.alias}`}
                        title={calendarDateStatusLabel(competitor?.status)}
                      >
                        {calendarPriceDisplay(competitor?.price_amount_yuan)}
                      </span>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState text="还没有日历价读取结果。保存配置后点击立即读取。" />
        )}
      </div>

      <div className="panel">
        <h3>最近预警</h3>
        {props.alerts.length ? (
          <div className="latest-grid">
            {props.alerts.map((alert) => (
              <div className="latest-card" key={alert.id}>
                <strong>{calendarAlertLabel(alert.alert_type)}</strong>
                <span>{alert.checkin_date}</span>
                <span>
                  本店 {calendarPriceDisplay(alert.own_price_yuan)} / 参考{" "}
                  {calendarPriceDisplay(alert.reference_price_yuan)} / 差 ¥{alert.gap_yuan}
                </span>
                <span className="muted">{formatTime(alert.created_at)}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState text="当前没有日历价预警记录。" />
        )}
      </div>
    </section>
  );
}

function PriceChangePanel(props: {
  groups: CompetitorWatchGroup[];
  selectedGroup: CompetitorWatchGroup | null;
  selectedKey: string;
  latest: CompetitorLatestSnapshot | null;
  priceChangeStrategy: PricingStrategyConfig | null;
  priceChangeAlert: PriceChangeAlert | null;
  busy: string | null;
  onSelectGroup: (groupKey: string) => void;
  onPriceChangeFormChange: (form: PricingStrategyForm) => void;
  onSavePriceChangeStrategy: () => void;
  onEvaluatePriceChange: () => void;
  onTogglePriceChangeStrategy: (enabled: boolean) => void;
  onSaveAlertTitle: (title: string) => void;
}) {
  const group = props.selectedGroup;
  const [alertTitleDraft, setAlertTitleDraft] = useState(group?.price_change_alert_title ?? "");

  useEffect(() => {
    setAlertTitleDraft(group?.price_change_alert_title ?? "");
  }, [group?.group_key, group?.price_change_alert_title]);

  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <h2>价格变化提醒</h2>
          <p>基于已入库可信房价比较前后两次读取，竞对降价超过阈值时生成本店降价建议，竞对涨价只提醒价格变化。</p>
        </div>
      </div>

      <div className="split-grid price-alert-context">
        <div className="panel">
          <h3>提醒范围</h3>
          <label>
            监控组
            <select value={props.selectedKey} onChange={(event) => props.onSelectGroup(event.target.value)} disabled={!props.groups.length}>
              {props.groups.map((item) => (
                <option key={item.group_key} value={item.group_key}>{item.name}</option>
              ))}
            </select>
          </label>
          <label className="form-field-wide">
            价格变化提醒推送标题
            <input
              value={alertTitleDraft}
              onChange={(event) => setAlertTitleDraft(event.target.value)}
              placeholder={group?.brief_title || group?.name || "例如：澜江路价格变化提醒"}
              disabled={!group}
            />
            <span className="field-hint">用于区分不同监控组的价格变化推送；留空时使用竞品价格推送标题或监控组名称。</span>
          </label>
          <div className="action-strip">
            <button
              className="secondary-button"
              type="button"
              disabled={!group || props.busy === "save-price-alert-title"}
              onClick={() => props.onSaveAlertTitle(alertTitleDraft)}
            >
              <Save size={16} /> 保存提醒标题
            </button>
          </div>
          {group ? (
            <div className="metric-grid">
              <Metric label="当前组" value={group.name} />
              <Metric label="配置状态" value={statusLabel(group.runtime_config_status ?? "config_required")} />
              <Metric label="最近读取" value={group.latest_run ? statusLabel(group.latest_run.status) : "暂无"} />
              <Metric label="读取时间" value={group.latest_run ? formatTime(group.latest_run.captured_at) : "-"} />
            </div>
          ) : (
            <EmptyState text="请先创建并配置竞品监控组。" />
          )}
        </div>
        <div className="panel">
          <h3>数据口径</h3>
          <div className="policy-list">
            <span>仅使用已通过页面结构校验的可信房价。</span>
            <span>只比较当前成功读取和上一轮成功读取。</span>
            <span>非直连账号才会继续发送审批卡；直连或未确认账号只生成建议。</span>
            <span>监控组不同，酒店配置和价格趋势也会同步切换。</span>
          </div>
        </div>
      </div>

      <div className="panel price-alert-panel-wide">
        <PriceChangeAlertPanel
          strategy={props.priceChangeStrategy}
          alert={props.priceChangeAlert}
          busy={props.busy}
          onFormChange={props.onPriceChangeFormChange}
          onSaveStrategy={props.onSavePriceChangeStrategy}
          onEvaluate={props.onEvaluatePriceChange}
          onToggleEnabled={props.onTogglePriceChangeStrategy}
        />
      </div>

      <div className="panel">
        <h3>最近可信房价基础</h3>
        {props.latest ? <LatestCompetitorSnapshot snapshot={props.latest} /> : <EmptyState text="还没有竞品读取记录。先在竞品价格页完成一次读取。" />}
      </div>
    </section>
  );
}

function ModelConfigPanel(props: {
  supervisorApiKey: string;
  config: IntegrationConfigSnapshot | null;
  form: IntegrationConfigForm;
  models: string[];
  modelsResult: LlmModelsResult | null;
  busy: string | null;
  testResult: LlmConnectionTestResult | null;
  onFormChange: (form: IntegrationConfigForm) => void;
  onSave: () => void;
  onLoadModels: () => void;
  onTest: () => void;
  onOpenKnowledge: () => void;
}) {
  const canUseLlmApi = Boolean(props.config?.llm_api_key_configured || props.form.llm_api_key.trim());
  return (
    <section className="workspace model-config-column">
      <div className="section-head">
        <div>
          <h2>LLM 配置</h2>
          <p>配置 OpenAI-compatible 服务地址、API key 和模型。API key 只保存到后端，前端只显示脱敏状态。</p>
        </div>
      </div>

      <LlmConfigConsole
        supervisorApiKey={props.supervisorApiKey}
        legacyConfig={props.config}
        legacyForm={props.form}
        legacyModels={props.models}
        legacyModelsResult={props.modelsResult}
        legacyTestResult={props.testResult}
        legacyBusy={props.busy}
        onLegacyFormChange={props.onFormChange}
        onLegacySave={props.onSave}
        onLegacyLoadModels={props.onLoadModels}
        onLegacyTest={props.onTest}
      />

      <details className="advanced-details llm-legacy-config">
        <summary>旧版单模型与连接参数</summary>
      <div className="panel">
        <p>
          当前默认飞书对话由 Hermes 接管；如果启用 <code>FEISHU_AGENT_MODE=builtin</code>，后端内置 Agent、模型摘要和连接测试会使用这里配置的服务。
          “拉取模型”使用当前表单参数请求服务商的 <code>/models</code> 接口，不会自动保存 API key。
        </p>
      </div>

      <div className="panel">
        <div className="panel-title-row">
          <h3>当前状态</h3>
          <ShieldCheck size={18} />
        </div>
        <div className="metric-grid">
          <Metric label="供应商" value={props.config?.llm_provider ?? props.form.llm_provider} />
          <Metric label="模型" value={props.config?.llm_model ?? props.form.llm_model} />
          <Metric
            label="API 密钥"
            value={props.config?.llm_api_key_configured ? `已保存 ${props.config.llm_api_key_masked ?? ""}` : "未保存"}
          />
          <Metric label="接口地址" value={props.config?.llm_base_url ?? props.form.llm_base_url} />
        </div>
      </div>

      <div className="panel">
        <h3>连接参数</h3>
        <div className="form-grid">
          <label>
            模型供应商
            <input
              value={props.form.llm_provider}
              onChange={(event) => props.onFormChange({ ...props.form, llm_provider: event.target.value })}
              placeholder="deepseek"
            />
          </label>
          <label>
            模型名称
            <input
              list="llm-model-options"
              value={props.form.llm_model}
              onChange={(event) => props.onFormChange({ ...props.form, llm_model: event.target.value })}
              placeholder="deepseek-v4-flash"
            />
            <datalist id="llm-model-options">
              {props.models.map((model) => <option key={model} value={model} />)}
            </datalist>
          </label>
          <label className="form-field-wide">
            接口地址
            <input
              value={props.form.llm_base_url}
              onChange={(event) => props.onFormChange({ ...props.form, llm_base_url: event.target.value })}
              placeholder="https://api.deepseek.com"
            />
          </label>
          <label className="form-field-wide">
            API 密钥
            <input
              type="password"
              value={props.form.llm_api_key}
              onChange={(event) => props.onFormChange({ ...props.form, llm_api_key: event.target.value })}
              placeholder={props.config?.llm_api_key_configured ? `已保存：${props.config.llm_api_key_masked ?? ""}` : "输入模型 API key"}
            />
            <span className="field-hint">留空保存不会覆盖已保存 key；输入新 key 后保存会替换旧 key。</span>
          </label>
        </div>
        <div className="action-strip model-config-actions">
          <button
            className="primary-button"
            type="button"
            disabled={props.busy === "save-integration-config"}
            onClick={props.onSave}
          >
            <Save size={16} /> 保存模型配置
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!canUseLlmApi || props.busy === "load-llm-models"}
            onClick={props.onLoadModels}
          >
            <RefreshCw size={16} /> 拉取模型
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={!canUseLlmApi || props.busy === "test-llm-connection"}
            onClick={props.onTest}
          >
            <KeyRound size={16} /> 测试连接
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title-row">
          <h3>模型列表</h3>
          <span className="muted">{props.models.length ? `${props.models.length} 个可用模型` : "尚未拉取"}</span>
        </div>
        {props.modelsResult ? (
          <div className={`connection-result ${props.modelsResult.status === "success" ? "success" : "warning"}`}>
            <strong>{props.modelsResult.summary}</strong>
            {props.modelsResult.next_action ? <p>{props.modelsResult.next_action}</p> : null}
          </div>
        ) : (
          <EmptyState text="填写 URL 和 API key 后点击“拉取模型”；服务商需支持 OpenAI-compatible /models 接口。" />
        )}
        {props.models.length ? (
          <div className="policy-list">
            {props.models.map((model) => <span key={model}>{model}</span>)}
          </div>
        ) : null}
      </div>

      <div className="panel">
        <h3>连接测试</h3>
        {props.testResult ? (
          <div className={`connection-result ${props.testResult.status === "success" ? "success" : "warning"}`}>
            <strong>{props.testResult.summary}</strong>
            <span>{statusLabel(props.testResult.status)} · {props.testResult.provider} · {props.testResult.model}</span>
            {props.testResult.next_action ? <p>{props.testResult.next_action}</p> : null}
            {props.testResult.data.reply_preview ? <p>{props.testResult.data.reply_preview}</p> : null}
          </div>
        ) : (
          <EmptyState text="保存 API key 后可测试连接；测试不会读取酒店数据，也不会触发飞书推送。" />
        )}
      </div>

      </details>
      <button
        className="knowledge-management-jump"
        type="button"
        onClick={props.onOpenKnowledge}
      >
        <BookOpen size={16} />
        知识管理
        <span className="advanced-maintenance-hint">Skill 管理 · RAG 知识库</span>
        <span aria-hidden="true">→</span>
      </button>
    </section>
  );
}

type KnowledgeTab = "skills" | "rag" | "distill";

function KnowledgeManagementPanel(props: {
  supervisorApiKey: string;
  onBack: () => void;
}) {
  const [tab, setTab] = useState<KnowledgeTab>("skills");
  return (
    <section className="workspace">
      <div className="section-head">
        <div>
          <button className="secondary-button" type="button" onClick={props.onBack}>
            ← 返回
          </button>
          <h2>知识管理</h2>
          <p className="muted">
            管理共享 Skill 与 RAG 知识库。Skill 写入仓库源后经符号链接同步到各 Hermes profile。
          </p>
        </div>
      </div>
      <div className="action-strip">
        <TabButton active={tab === "skills"} icon={BookOpen} label="Skill 管理" onClick={() => setTab("skills")} />
        <TabButton active={tab === "rag"} icon={Database} label="RAG 知识库" onClick={() => setTab("rag")} />
        <TabButton active={tab === "distill"} icon={BrainCircuit} label="自学习" onClick={() => setTab("distill")} />
      </div>
      {tab === "skills" ? <SkillManagementPanel supervisorApiKey={props.supervisorApiKey} /> : null}
      {tab === "rag" ? <RagKnowledgePanel supervisorApiKey={props.supervisorApiKey} /> : null}
      {tab === "distill" ? <DistillPanel supervisorApiKey={props.supervisorApiKey} /> : null}
    </section>
  );
}

const EMPTY_EMBEDDING_FORM = {
  provider: "openai",
  model_name: "text-embedding-3-small",
  base_url: "",
  api_key: "",
};

function RagKnowledgePanel(props: { supervisorApiKey: string }) {
  const [config, setConfig] = useState<EmbeddingConfig | null>(null);
  const [form, setForm] = useState({ ...EMPTY_EMBEDDING_FORM });
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [summary, setSummary] = useState<RagFolderSummary>({ folders: [], unfiled_count: 0, total_count: 0 });
  const [view, setView] = useState<"shelf" | "kb">("shelf");
  const [activeKb, setActiveKb] = useState<number | "unfiled">("unfiled");
  const [kbTab, setKbTab] = useState<"overview" | "docs" | "search" | "settings">("overview");
  const [newFolderName, setNewFolderName] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [docForm, setDocForm] = useState({ title: "", content: "" });
  const [detail, setDetail] = useState<RagDocumentDetail | null>(null);
  const [detailForm, setDetailForm] = useState({ title: "", folderChoice: "", newFolder: "", content: "" });
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RagSearchResult[]>([]);
  const [embeddingTest, setEmbeddingTest] = useState<EmbeddingConnectionTestResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const folderOptions = useMemo(() => summary.folders.map((folder) => folder.name), [summary]);
  const activeKbFolder = useMemo(
    () => (activeKb === "unfiled" ? null : summary.folders.find((folder) => folder.id === activeKb) ?? null),
    [activeKb, summary],
  );
  const kbIsUnfiled = activeKb === "unfiled";
  const activeKbName = activeKbFolder?.name ?? (kbIsUnfiled ? "默认" : "");
  const kbDocs = useMemo(() => {
    if (kbIsUnfiled) {
      return documents.filter((document) => !document.folder);
    }
    return activeKbFolder ? documents.filter((document) => document.folder === activeKbFolder.name) : [];
  }, [documents, kbIsUnfiled, activeKbFolder]);
  const kbChunkTotal = useMemo(
    () => kbDocs.reduce((total, document) => total + document.chunk_count, 0),
    [kbDocs],
  );

  const load = useCallback(async () => {
    if (!props.supervisorApiKey) {
      setError("需要 supervisor API 密钥才能管理 RAG 知识库。");
      return;
    }
    setError(null);
    // 向量模型配置独立加载，避免文件夹/文档加载失败时连带显示“未配置”。
    try {
      setConfig(await getEmbeddingConfig(props.supervisorApiKey));
      setEmbeddingTest(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载向量模型配置失败。");
    }
    try {
      setDocuments(await listRagDocuments(props.supervisorApiKey));
    } catch {
      /* 文档列表加载失败不阻塞配置 */
    }
    try {
      setSummary(await listRagFolders(props.supervisorApiKey));
    } catch {
      /* 文件夹加载失败不阻塞配置与文档 */
    }
  }, [props.supervisorApiKey]);

  async function refreshFolders() {
    try {
      setSummary(await listRagFolders(props.supervisorApiKey));
    } catch {
      /* 文件夹刷新失败不阻塞主流程 */
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSaveConfig() {
    setBusy("save-embedding-config");
    setError(null);
    try {
      const next = await updateEmbeddingConfig(props.supervisorApiKey, {
        provider: form.provider,
        model_name: form.model_name,
        base_url: form.base_url,
        api_key: form.api_key || undefined,
      });
      setConfig(next);
      setEmbeddingTest(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存向量模型配置失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleTestConfig() {
    setBusy("test-embedding-config");
    setError(null);
    setEmbeddingTest(null);
    try {
      setEmbeddingTest(
        await testEmbeddingConfig(props.supervisorApiKey, {
          provider: form.provider,
          model_name: form.model_name,
          base_url: form.base_url,
          api_key: form.api_key || undefined,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "向量模型连接测试失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleIngest() {
    setBusy("ingest-document");
    setError(null);
    try {
      await createRagDocument(props.supervisorApiKey, {
        title: docForm.title,
        content: docForm.content,
        folder: kbIsUnfiled ? undefined : activeKbName || undefined,
      });
      setDocForm({ title: "", content: "" });
      setDocuments(await listRagDocuments(props.supervisorApiKey));
      await refreshFolders();
    } catch (err) {
      setError(err instanceof Error ? err.message : "文档入库失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setBusy("ingest-read-file");
    setError(null);
    try {
      const { title, content } = await prepareRagDocumentFromFile(file, docForm.title);
      setDocForm((current) => ({ ...current, title, content }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取文件失败。");
    } finally {
      setBusy(null);
      event.target.value = "";
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm("确认删除该知识库文档？")) {
      return;
    }
    setBusy(`delete-document-${id}`);
    setError(null);
    try {
      await deleteRagDocument(props.supervisorApiKey, id);
      if (detail?.id === id) {
        setDetail(null);
      }
      setDocuments(await listRagDocuments(props.supervisorApiKey));
      await refreshFolders();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除文档失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleSelectDocument(id: number) {
    setBusy(`get-document-${id}`);
    setError(null);
    try {
      const documentDetail = await getRagDocument(props.supervisorApiKey, id);
      setDetail(documentDetail);
      const currentFolder = normalizeFolderName(documentDetail.folder);
      const known = currentFolder !== UNFILED_FOLDER && folderOptions.includes(currentFolder);
      setDetailForm({
        title: documentDetail.title,
        folderChoice: currentFolder === UNFILED_FOLDER ? "" : known ? currentFolder : NEW_FOLDER_CHOICE,
        newFolder: currentFolder === UNFILED_FOLDER || known ? "" : currentFolder,
        content: documentDetail.raw_content,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载文档详情失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleSaveDetail() {
    if (!detail) {
      return;
    }
    if (!detailForm.title.trim()) {
      setError("文档标题不能为空。");
      return;
    }
    if (detailForm.content !== detail.raw_content) {
      const confirmed = window.confirm("正文已修改，保存后将重新切片并生成向量。继续保存？");
      if (!confirmed) {
        return;
      }
    }
    setBusy("update-document");
    setError(null);
    try {
      const updated = await updateRagDocument(props.supervisorApiKey, detail.id, {
        title: detailForm.title.trim(),
        folder: resolveFolderChoice(detailForm.folderChoice, detailForm.newFolder),
        content: detailForm.content,
      });
      setDetail(updated);
      setDocuments(await listRagDocuments(props.supervisorApiKey));
      await refreshFolders();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存文档失败。");
    } finally {
      setBusy(null);
    }
  }

  function handleExportDocument() {
    if (!detail) {
      return;
    }
    const markdown = `# ${detail.title}\n\n${detail.raw_content}\n`;
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${detail.title}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async function handleCreateFolder() {
    const name = newFolderName.trim();
    if (!name) {
      return;
    }
    setBusy("create-folder");
    setError(null);
    try {
      await createRagFolder(props.supervisorApiKey, name);
      setNewFolderName("");
      await refreshFolders();
    } catch (err) {
      setError(err instanceof Error ? err.message : "新建文件夹失败。");
    } finally {
      setBusy(null);
    }
  }

  function openKb(target: number | "unfiled") {
    setActiveKb(target);
    setKbTab("overview");
    setDetail(null);
    setView("kb");
    if (target !== "unfiled") {
      const folder = summary.folders.find((item) => item.id === target);
      setRenameValue(folder?.name ?? "");
    } else {
      setRenameValue("");
    }
  }

  async function handleRenameFolder(folderId: number) {
    const name = renameValue.trim();
    const current = summary.folders.find((folder) => folder.id === folderId)?.name ?? "";
    if (!name || name === current) {
      return;
    }
    setBusy(`rename-folder-${folderId}`);
    setError(null);
    try {
      await renameRagFolder(props.supervisorApiKey, folderId, name);
      await refreshFolders();
      setDocuments(await listRagDocuments(props.supervisorApiKey));
      setRenameValue(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重命名知识库失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteFolder(folderId: number) {
    if (!window.confirm("删除该知识库？其中文档将归入「默认」，不会被删除。")) {
      return;
    }
    setBusy(`delete-folder-${folderId}`);
    setError(null);
    try {
      await deleteRagFolder(props.supervisorApiKey, folderId);
      if (activeKb === folderId) {
        setView("shelf");
        setActiveKb("unfiled");
      }
      await refreshFolders();
      setDocuments(await listRagDocuments(props.supervisorApiKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除知识库失败。");
    } finally {
      setBusy(null);
    }
  }

  function renderDocCard(doc: RagDocument) {
    return (
      <article key={doc.id} className={`skill-card ${detail?.id === doc.id ? "active" : ""}`}>
        <div className="skill-card-main">
          <div className="skill-card-title">
            <FileText size={15} />
            <span>{doc.title}</span>
            <span className={`skill-source-tag ${doc.source_type === "distilled" ? "manual" : "curated"}`}>
              {doc.source_type === "distilled" ? "自学习" : "手动"}
            </span>
          </div>
          <div className="skill-card-meta">
            <span>{doc.chunk_count} 切片</span>
            {doc.updated_at ? (
              <>
                <span>·</span>
                <span><Clock size={12} /> {formatTime(doc.updated_at)}</span>
              </>
            ) : null}
          </div>
        </div>
        <div className="skill-card-actions">
          <button
            className="icon-button"
            type="button"
            title="查看 / 编辑文档"
            onClick={() => void handleSelectDocument(doc.id)}
            disabled={busy === `get-document-${doc.id}`}
          >
            {busy === `get-document-${doc.id}` ? <Loader2 className="spin" size={15} /> : <Pencil size={15} />}
          </button>
          <button className="icon-button danger" type="button" title="删除文档" onClick={() => void handleDelete(doc.id)}>
            <Trash2 size={15} />
          </button>
        </div>
      </article>
    );
  }

  async function handleSearch() {
    setBusy("rag-search");
    setError(null);
    try {
      setResults(await searchRag(props.supervisorApiKey, query));
    } catch (err) {
      setError(err instanceof Error ? err.message : "检索失败。");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {error ? <div className="connection-result warning"><strong>{error}</strong></div> : null}

      {view === "shelf" ? (
        <section className="panel rag-shelf">
          <div className="panel-title-row">
            <h3>知识库</h3>
            <span className="muted">{summary.total_count} 篇文档 · {summary.folders.length} 个知识库</span>
          </div>

          <button
            type="button"
            className={`rag-shelf-row ${kbIsUnfiled ? "active" : ""}`}
            onClick={() => openKb("unfiled")}
          >
            <Folder size={18} />
            <div className="rag-shelf-row-main">
              <strong>默认</strong>
              <span className="muted">未归入任何知识库的文档</span>
            </div>
            <span className="rag-folder-count">{summary.unfiled_count}</span>
            <ChevronRight size={16} />
          </button>

          {summary.folders.map((folder) => (
            <button
              type="button"
              key={folder.id}
              className={`rag-shelf-row ${activeKb === folder.id ? "active" : ""}`}
              onClick={() => openKb(folder.id)}
            >
              <Folder size={18} />
              <div className="rag-shelf-row-main">
                <strong>{folder.name}</strong>
                <span className="muted">{folder.document_count} 篇文档</span>
              </div>
              <span className="rag-folder-count">{folder.document_count}</span>
              <ChevronRight size={16} />
            </button>
          ))}

          <form
            className="rag-shelf-new"
            onSubmit={(event) => {
              event.preventDefault();
              void handleCreateFolder();
            }}
          >
            <input
              value={newFolderName}
              onChange={(event) => setNewFolderName(event.target.value)}
              placeholder="＋ 新建知识库（命名后回车）"
            />
            <button type="submit" className="secondary-button" disabled={busy === "create-folder" || !newFolderName.trim()}>
              <Plus size={14} /> 新建
            </button>
          </form>
        </section>
      ) : null}

      {view === "kb" ? (
        <section className="panel rag-kb-detail">
          <div className="panel-title-row rag-kb-detail-header">
            <button type="button" className="icon-button" title="返回知识库列表" onClick={() => setView("shelf")}>
              <ArrowLeft size={16} />
            </button>
            <div>
              <h3>{activeKbName}</h3>
              <span className="muted">{kbDocs.length} 篇文档 · {kbChunkTotal} 切片</span>
            </div>
          </div>

          <div className="rag-kb-tabs">
            <button type="button" className={`rag-kb-tab ${kbTab === "overview" ? "active" : ""}`} onClick={() => setKbTab("overview")}>概览</button>
            <button type="button" className={`rag-kb-tab ${kbTab === "docs" ? "active" : ""}`} onClick={() => setKbTab("docs")}>文档管理</button>
            <button type="button" className={`rag-kb-tab ${kbTab === "search" ? "active" : ""}`} onClick={() => setKbTab("search")}>知识库检索</button>
            <button type="button" className={`rag-kb-tab ${kbTab === "settings" ? "active" : ""}`} onClick={() => setKbTab("settings")}>设置</button>
          </div>

          {kbTab === "overview" ? (
            <div className="rag-kb-overview">
              <Metric label="文档数" value={String(kbDocs.length)} />
              <Metric label="总切片数" value={String(kbChunkTotal)} />
              <Metric label="来源" value={kbIsUnfiled ? "默认（未分组）" : "独立知识库"} />
              <Metric label="向量模型" value={config ? `${config.provider}/${config.model_name}` : "未配置"} />
            </div>
          ) : null}

          {kbTab === "docs" ? (
            <div className="rag-kb-docs">
              <div className="action-strip model-config-actions">
                <label className="file-upload-button">
                  <Upload size={16} /> 从文件载入
                  <input
                    type="file"
                    accept=".txt,.md,.csv,.json,.log,text/plain,text/markdown,application/json,text/csv"
                    onChange={(event) => void handleFile(event)}
                    disabled={busy === "ingest-read-file"}
                    hidden
                  />
                </label>
                <span className="muted">支持 .txt/.md/.csv/.json/.log；载入后标题取文件名，正文可在下方编辑</span>
              </div>
              <div className="form-grid">
                <label className="form-field-wide">
                  标题
                  <input value={docForm.title} onChange={(e) => setDocForm({ ...docForm, title: e.target.value })} placeholder="文档标题" />
                </label>
              </div>
              <label className="form-field-wide">
                正文
                <textarea
                  className="skill-editor"
                  value={docForm.content}
                  onChange={(e) => setDocForm({ ...docForm, content: e.target.value })}
                  rows={6}
                  placeholder="粘贴文档内容，入库后自动切片并生成向量"
                />
              </label>
              <div className="action-strip model-config-actions">
                <button className="primary-button" type="button" disabled={busy === "ingest-document" || !docForm.title || !docForm.content} onClick={() => void handleIngest()}>
                  <Plus size={16} /> 入库到「{activeKbName}」
                </button>
              </div>

              <div className="panel-title-row">
                <h4>文档列表</h4>
                <span className="muted">{kbDocs.length} 篇</span>
              </div>
              {kbDocs.length ? (
                <div className="skill-card-list rag-doc-list">{kbDocs.map(renderDocCard)}</div>
              ) : (
                <EmptyState text="该知识库暂无文档。" />
              )}

              {detail ? (
                <div className="rag-doc-detail">
                  <div className="panel-title-row">
                    <h4>文档详情</h4>
                    <button className="icon-button" type="button" title="关闭详情" onClick={() => setDetail(null)}>
                      <X size={16} />
                    </button>
                  </div>
                  <div className="form-grid">
                    <label className="form-field-wide">
                      标题
                      <input value={detailForm.title} onChange={(e) => setDetailForm({ ...detailForm, title: e.target.value })} placeholder="文档标题" />
                    </label>
                    <label>
                      归属知识库
                      <select value={detailForm.folderChoice} onChange={(e) => setDetailForm({ ...detailForm, folderChoice: e.target.value })}>
                        <option value="">默认（未分组）</option>
                        {folderOptions.map((folder) => (
                          <option key={folder} value={folder}>{folder}</option>
                        ))}
                        <option value={NEW_FOLDER_CHOICE}>＋ 新建知识库…</option>
                      </select>
                    </label>
                    {detailForm.folderChoice === NEW_FOLDER_CHOICE ? (
                      <label>
                        新知识库名称
                        <input value={detailForm.newFolder} onChange={(e) => setDetailForm({ ...detailForm, newFolder: e.target.value })} placeholder="输入新知识库名称" />
                      </label>
                    ) : null}
                  </div>
                  <label className="form-field-wide">
                    正文（修改后保存将重新切片并生成向量）
                    <textarea className="skill-editor" value={detailForm.content} onChange={(e) => setDetailForm({ ...detailForm, content: e.target.value })} rows={12} />
                  </label>
                  <div className="action-strip model-config-actions">
                    <button className="primary-button" type="button" disabled={busy === "update-document" || !detailForm.title.trim()} onClick={() => void handleSaveDetail()}>
                      <Save size={16} /> 保存修改
                    </button>
                    <button className="secondary-button" type="button" onClick={handleExportDocument}>
                      <Download size={16} /> 导出 Markdown
                    </button>
                  </div>
                  <p className="muted">
                    创建于 {detail.created_at} · 更新于 {detail.updated_at ?? "—"} · 来源{" "}
                    {detail.source_type === "distilled" ? "自学习蒸馏" : "手动入库"}
                  </p>
                </div>
              ) : null}
            </div>
          ) : null}

          {kbTab === "search" ? (
            <div className="rag-kb-search">
              <div className="form-grid">
                <label className="form-field-wide">
                  查询
                  <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="输入检索查询（检索范围为全部已入库文档）" />
                </label>
              </div>
              <div className="action-strip model-config-actions">
                <button className="secondary-button" type="button" disabled={busy === "rag-search" || !query} onClick={() => void handleSearch()}>
                  <RefreshCw size={16} /> 检索
                </button>
              </div>
              {results.length ? (
                <div className="policy-list">
                  {results.map((hit) => (
                    <div key={`${hit.document_id}-${hit.score}`} className="status-item rag-search-hit">
                      <span>{hit.title} <small>· 相似度 {hit.score}</small></span>
                      <small>{hit.chunk_text}</small>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {kbTab === "settings" ? (
            <div className="rag-kb-settings">
              <h4>知识库设置</h4>
              {kbIsUnfiled ? (
                <p className="muted">「默认」为系统未分组容器，不可重命名或删除。新文档入库时若未指定知识库将落入此处。</p>
              ) : activeKbFolder ? (
                <div className="form-grid">
                  <label className="form-field-wide">
                    知识库名称
                    <input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} placeholder="知识库名称" />
                  </label>
                </div>
              ) : null}
              {activeKbFolder ? (
                <div className="action-strip model-config-actions">
                  <button className="primary-button" type="button" disabled={busy === `rename-folder-${activeKbFolder.id}` || !renameValue.trim()} onClick={() => void handleRenameFolder(activeKbFolder.id)}>
                    <Save size={16} /> 重命名
                  </button>
                  <button className="secondary-button danger" type="button" disabled={busy === `delete-folder-${activeKbFolder.id}`} onClick={() => void handleDeleteFolder(activeKbFolder.id)}>
                    <Trash2 size={16} /> 删除知识库（文档归入默认）
                  </button>
                </div>
              ) : null}

              <h4>向量模型</h4>
              <div className="form-grid">
                <label>
                  供应商
                  <input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} placeholder="openai" />
                </label>
                <label>
                  模型名称
                  <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} placeholder="text-embedding-3-small" />
                </label>
                <label className="form-field-wide">
                  接口地址
                  <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://api.openai.com/v1" />
                </label>
                <label className="form-field-wide">
                  API 密钥
                  <input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder={config?.api_key_configured ? `已保存：${config.api_key_masked ?? ""}` : "输入向量模型 API key"} />
                </label>
              </div>
              <div className="action-strip model-config-actions">
                <button className="primary-button" type="button" disabled={busy === "save-embedding-config"} onClick={() => void handleSaveConfig()}>
                  <Save size={16} /> 保存向量模型
                </button>
                <button className="secondary-button" type="button" disabled={busy === "test-embedding-config" || !form.provider.trim() || !form.model_name.trim() || !form.base_url.trim()} onClick={() => void handleTestConfig()}>
                  <KeyRound size={16} /> 测试连接
                </button>
              </div>
              {embeddingTest ? (
                <ResultNotice
                  status={embeddingTest.status}
                  summary={embeddingTest.summary}
                  detail={embeddingTest.vector_length ? `向量维度：${embeddingTest.vector_length}` : undefined}
                />
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}
    </>
  );
}

const EMPTY_SKILL_FORM = { name: "", description: "", content: "" };

function downloadSkillDetail(detail: SkillDetail) {
  const markdown = buildSkillExportMarkdown(detail);
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${detail.name}.md`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function SkillManagementPanel(props: { supervisorApiKey: string }) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<{ name: string; description: string; content: string }>({
    ...EMPTY_SKILL_FORM,
  });
  const [creating, setCreating] = useState(false);

  const loadList = useCallback(async () => {
    if (!props.supervisorApiKey) {
      setError("需要 supervisor API 密钥才能管理 Skill。");
      return;
    }
    setBusy("list-skills");
    setError(null);
    try {
      setSkills(await listSkills(props.supervisorApiKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 Skill 列表失败。");
    } finally {
      setBusy(null);
    }
  }, [props.supervisorApiKey]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  async function loadDetail(name: string) {
    setBusy(`get-skill-${name}`);
    setError(null);
    try {
      const detail = await getSkill(props.supervisorApiKey, name);
      setSelected(detail);
      setForm({ name: detail.name, description: detail.description, content: detail.content });
      setCreating(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 Skill 详情失败。");
    } finally {
      setBusy(null);
    }
  }

  function startCreate() {
    setSelected(null);
    setCreating(true);
    setForm({ ...EMPTY_SKILL_FORM });
  }

  async function handleSave() {
    setError(null);
    if (creating) {
      if (!form.name.trim()) {
        setError("Skill 名称不能为空。");
        return;
      }
      setBusy("create-skill");
      try {
        const detail = await createSkill(props.supervisorApiKey, {
          name: form.name.trim(),
          description: form.description.trim(),
          content: form.content,
        });
        setSelected(detail);
        setCreating(false);
        setSkills(await listSkills(props.supervisorApiKey));
      } catch (err) {
        setError(err instanceof Error ? err.message : "新增 Skill 失败。");
      } finally {
        setBusy(null);
      }
    } else if (selected) {
      setBusy("update-skill");
      try {
        const detail = await updateSkill(props.supervisorApiKey, selected.name, {
          description: form.description,
          content: form.content,
        });
        setSelected(detail);
        setSkills(await listSkills(props.supervisorApiKey));
      } catch (err) {
        setError(err instanceof Error ? err.message : "保存 Skill 失败。");
      } finally {
        setBusy(null);
      }
    }
  }

  async function handleDelete(skill: SkillSummary) {
    if (!window.confirm(`确认删除 Skill「${skill.name}」？此操作不可撤销。`)) {
      return;
    }
    setBusy(`delete-skill-${skill.name}`);
    setError(null);
    try {
      await deleteSkill(props.supervisorApiKey, skill.name);
      if (selected?.name === skill.name) {
        setSelected(null);
      }
      setSkills(await listSkills(props.supervisorApiKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除 Skill 失败。");
    } finally {
      setBusy(null);
    }
  }

  async function handleExport(skill: SkillSummary) {
    setBusy(`export-skill-${skill.name}`);
    setError(null);
    try {
      const detail =
        selected?.name === skill.name ? selected : await getSkill(props.supervisorApiKey, skill.name);
      downloadSkillDetail(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出 Skill 失败。");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="skill-management-layout">
      <section className="panel skill-library-panel">
      <div className="panel-title-row skill-library-header">
        <div>
          <h3>Skill 列表</h3>
          <p>{skills.length} 个 Skill</p>
        </div>
        <button className="primary-button" type="button" onClick={startCreate}>
          <Plus size={16} /> 新增 Skill
        </button>
      </div>
      {error ? <div className="connection-result warning"><strong>{error}</strong></div> : null}

      {skills.length ? (
        <div className="skill-card-list">
          {skills.map((skill) => (
            <article key={skill.name} className={`skill-card ${selected?.name === skill.name ? "active" : ""}`}>
              <div className="skill-card-main">
                <div className="skill-card-title">
                  <FileText size={17} />
                  <span>{skill.name}</span>
                  <span className={`skill-source-tag ${skill.source === "curated" ? "curated" : "manual"}`}>
                    {skill.source === "curated" ? "内置" : "手动"}
                  </span>
                </div>
                <p className="skill-card-description">
                  {skill.description || "（无描述）"}
                </p>
                <div className="skill-card-meta">
                  <span>{skill.references.length} 参考</span>
                  <span>·</span>
                  <span>{skill.templates.length} 模板</span>
                  {skill.updated_at ? (
                    <>
                      <span>·</span>
                      <span><Clock size={12} /> {formatTime(skill.updated_at)}</span>
                    </>
                  ) : null}
                </div>
              </div>
              <div className="skill-card-actions">
                <button
                  className="icon-button"
                  type="button"
                  title="编辑 Skill"
                  onClick={() => void loadDetail(skill.name)}
                  disabled={busy === `get-skill-${skill.name}`}
                >
                  <Pencil size={15} />
                </button>
                <button
                  className="icon-button"
                  type="button"
                  title="导出 Skill"
                  onClick={() => void handleExport(skill)}
                  disabled={busy === `export-skill-${skill.name}`}
                >
                  {busy === `export-skill-${skill.name}` ? <Loader2 className="spin" size={15} /> : <Download size={15} />}
                </button>
                <button
                  className="icon-button danger"
                  type="button"
                  disabled={skill.source === "curated"}
                  onClick={() => void handleDelete(skill)}
                  title={skill.source === "curated" ? "核心 Skill 不可删除" : undefined}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState text={busy === "list-skills" ? "加载中…" : "未配置任何 Skill。"} />
      )}

      </section>
      <section className="panel skill-editor-panel">
      {(creating || selected) && busy !== "list-skills" ? (
        <div className="skill-editor-form">
          <div className="panel-title-row skill-editor-header">
            <div>
              <h3>{creating ? "新增 Skill" : `编辑 ${selected?.name ?? ""}`}</h3>
              <p className="muted">
                {creating ? "填写名称、描述和 Markdown 正文" : "修改后保存，正文与附属模板会同步写入 Skill 文件"}
              </p>
            </div>
            <button
              className="icon-button"
              type="button"
              title="关闭编辑"
              onClick={() => {
                setSelected(null);
                setCreating(false);
              }}
            >
              <X size={16} />
            </button>
          </div>
          <div className="form-grid">
            <label>
              名称
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                disabled={!creating}
                placeholder="my-skill"
              />
            </label>
            <label>
              描述
              <input
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                placeholder="一句话描述"
              />
            </label>
          </div>
          <label className="form-field-wide">
            正文（Markdown）
            <textarea
              className="skill-editor"
              value={form.content}
              onChange={(event) => setForm({ ...form, content: event.target.value })}
              rows={16}
              placeholder="# Skill 正文"
            />
          </label>
          <div className="action-strip model-config-actions">
            <button className="primary-button" type="button" disabled={busy === "create-skill" || busy === "update-skill"} onClick={() => void handleSave()}>
              <Save size={16} /> {creating ? "创建" : "保存"}
            </button>
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setSelected(null);
                setCreating(false);
              }}
            >
              取消
            </button>
          </div>
          {selected ? (
            <div className="skill-preview-grid">
              <details className="advanced-details" open>
                <summary>参考文件（{Object.keys(selected.references).length}）</summary>
                {Object.entries(selected.references).length ? (
                  Object.entries(selected.references).map(([name, body]) => (
                    <pre key={name} className="reference-preview">
                      <strong>{name}</strong>
                      {"\n"}
                      {body}
                    </pre>
                  ))
                ) : (
                  <p className="muted">暂无参考文件</p>
                )}
              </details>
              <details className="advanced-details" open>
                <summary>模板文件（{Object.keys(selected.templates).length}）</summary>
                {Object.entries(selected.templates).length ? (
                  Object.entries(selected.templates).map(([name, body]) => (
                    <pre key={name} className="reference-preview">
                      <strong>{name}</strong>
                      {"\n"}
                      {body}
                    </pre>
                  ))
                ) : (
                  <p className="muted">暂无模板文件</p>
                )}
              </details>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="skill-editor-empty">
          <FileCode2 size={34} />
          <h3>{skills.length ? "选择一个 Skill 或新增" : "还没有 Skill"}</h3>
          <p>在左侧列表中选择 Skill 进行编辑，或点击“新增 Skill”。</p>
        </div>
      )}
      </section>
    </div>
  );
}

function StatusItem({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="status-item">
      <Icon size={18} />
      <div>
        <span>{label}</span>
        {detail ? <small>{detail}</small> : null}
      </div>
      <strong>{value}</strong>
    </div>
  );
}

function TabButton({ active, icon: Icon, label, onClick }: { active: boolean; icon: typeof Activity; label: string; onClick: () => void }) {
  return (
    <button className={active ? "tab active" : "tab"} type="button" onClick={onClick}>
      <Icon size={16} /> {label}
    </button>
  );
}

async function replaceCompetitorGroup(
  groups: CompetitorWatchGroup[],
  updated: CompetitorWatchGroup,
): Promise<CompetitorWatchGroup[]> {
  return groups.map((group) => (group.group_key === updated.group_key ? updated : group));
}

function replaceDataCenterGroup(
  groups: DataCenterWatchGroup[],
  updated: DataCenterWatchGroup,
): DataCenterWatchGroup[] {
  const found = groups.some((group) => group.group_key === updated.group_key);
  if (!found) {
    return [...groups, updated];
  }
  return groups.map((group) => (group.group_key === updated.group_key ? updated : group));
}

function nextWatchGroupKeyAfterDelete<T extends { group_key: string }>(
  groups: T[],
  deletedKey: string,
): string {
  const index = groups.findIndex((group) => group.group_key === deletedKey);
  if (index < 0) {
    return groups[0]?.group_key ?? "";
  }
  return groups[index + 1]?.group_key ?? groups[index - 1]?.group_key ?? "";
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败";
}

function statusTone(status: string): Notice["tone"] {
  if (status === "success") {
    return "success";
  }
  if (status === "failed" || status === "error") {
    return "error";
  }
  return "info";
}

function breakfastLabel(value: string): string {
  if (value === "none") {
    return "无早";
  }
  if (value === "included") {
    return "含早";
  }
  return "早餐未知";
}

function availabilityLabel(value: string): string {
  if (value === "tight") {
    return "房量紧张";
  }
  if (value === "sold_out") {
    return "售罄";
  }
  return "正常";
}

function integrationConfigFormFromSnapshot(
  snapshot: IntegrationConfigSnapshot,
): IntegrationConfigForm {
  return {
    llm_provider: snapshot.llm_provider,
    llm_base_url: snapshot.llm_base_url,
    llm_model: snapshot.llm_model,
    llm_api_key: "",
  };
}
