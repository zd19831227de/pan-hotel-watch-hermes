import { CtripBusinessDataPanel, DailyBriefPanel } from "./CtripDailyBriefPanel";
import { DataCenterPanel } from "./CtripDataCenterPanel";
import { EbkAdvancedDetails } from "./EbkAdvancedDetails";
import { EbkPlatformStatusBar } from "./EbkPlatformStatusBar";
import { type CtripBusinessData } from "../ctripBusinessDataModel";
import { type BrowserSession } from "../../services/browserSessionsApi";
import {
  type DataCenterDataset,
  type DataCenterRun,
  type DataCenterWatchGroup,
} from "../../services/ctripEbookingWatchApi";
import { formatTime, Metric, statusLabel } from "../ui/opsPrimitives";

export function CtripEbkDashboard(props: {
  businessData: CtripBusinessData;
  latest: DataCenterRun | null;
  groups: DataCenterWatchGroup[];
  group: DataCenterWatchGroup | null;
  datasets: DataCenterDataset[];
  sessions: BrowserSession[];
  busy: string | null;
  onSelectGroup: (groupKey: string) => void;
  onCreateGroup: () => void;
  onDeleteGroup: () => void;
  onSetPolling: (enabled: boolean) => void;
  onSetDailyBrief: (enabled: boolean) => void;
  onStopAllSchedules: () => void;
  selectedKey: string;
  onPatchBriefGroup: (input: Partial<DataCenterWatchGroup> & { feishu_webhook_url?: string }) => void;
  onPatchDataCenterGroup: (
    input: Partial<DataCenterWatchGroup> & { feishu_chat_id?: string | null; feishu_webhook_url?: string },
  ) => void;
  onPatchDataset: (dataset: DataCenterDataset, input: Partial<DataCenterDataset>) => void;
  onRefreshBrief: () => void;
  onRunDataCenter: () => void;
}) {
  // Prefer the backend session_status tag; fall back to a local check against
  // available EBK sessions so the operator UI never silently fires a read that
  // will fail with "Unknown browser session".
  const sessionUnregistered = (() => {
    if (!props.group) return false;
    if (props.group.session_status) {
      return props.group.session_status === "unregistered";
    }
    const boundSessionId = props.group.browser_session_id;
    if (!boundSessionId) return false;
    return !props.sessions.some(
      (session) => session.browser_session_id === boundSessionId,
    );
  })();

  return (
    <div className="ebk-ops-platform-body ctrip-ebk-dashboard">
      <EbkPlatformStatusBar
        platformLabel="携程 EBK"
        groups={props.groups}
        group={props.group}
        selectedKey={props.selectedKey}
        latest={props.latest}
        busy={props.busy}
        sessionUnregistered={sessionUnregistered}
        onSelectGroup={props.onSelectGroup}
        onRefreshBrief={props.onRefreshBrief}
        onRunDataCenter={props.onRunDataCenter}
        onSetPolling={props.onSetPolling}
        onSetDailyBrief={props.onSetDailyBrief}
        onStopAllSchedules={props.onStopAllSchedules}
      />

      <CtripBusinessDataPanel data={props.businessData} />

      <div className="panel">
        <h3>简报来源</h3>
        <div className="latest-grid">
          <Metric label="数据来源" value={props.group?.name ?? "未接入"} />
          <Metric label="最近读取" value={props.latest?.captured_at ? formatTime(props.latest.captured_at) : "暂无"} />
          <Metric label="读取结果" value={props.latest ? statusLabel(props.latest.status) : "暂无"} />
          <Metric label="推送结果" value={props.latest?.push_result?.summary ?? "尚未推送"} />
        </div>
      </div>

      <EbkAdvancedDetails title="高级配置：简报推送、店铺平台配置与数据集">
        <DailyBriefPanel
          variant="configOnly"
          businessData={props.businessData}
          latest={props.latest}
          groups={props.groups}
          group={props.group}
          busy={props.busy}
          onSelectGroup={props.onSelectGroup}
          onPatchGroup={props.onPatchBriefGroup}
          onRun={props.onRefreshBrief}
        />
        {props.group ? (
          <DataCenterPanel
            variant="configOnly"
            groups={props.groups}
            group={props.group}
            datasets={props.datasets}
            latest={props.latest}
            sessions={props.sessions}
            busy={props.busy}
            selectedKey={props.group.group_key}
            onSelectGroup={props.onSelectGroup}
            onCreateGroup={props.onCreateGroup}
            onDeleteGroup={props.onDeleteGroup}
            onSetPolling={props.onSetPolling}
            onPatchGroup={props.onPatchDataCenterGroup}
            onPatchDataset={props.onPatchDataset}
            onRun={props.onRunDataCenter}
          />
        ) : null}
      </EbkAdvancedDetails>
    </div>
  );
}
