from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SQLITE_PREFIX = "sqlite+aiosqlite:///"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_database_url(url: str) -> str:
    if not url.startswith(_SQLITE_PREFIX):
        return url
    raw_path = url[len(_SQLITE_PREFIX) :]
    if raw_path.startswith("/./"):
        raw_path = raw_path[3:]
    elif raw_path.startswith("./"):
        raw_path = raw_path[2:]
    elif (
        raw_path.startswith("/")
        and len(raw_path) > 2
        and raw_path[2] == ":"
    ):
        raw_path = raw_path[1:]
    path = Path(raw_path)
    if not path.is_absolute():
        path = (_project_root() / path).resolve()
    return f"{_SQLITE_PREFIX}{path.as_posix()}"


def _resolve_runtime_dir(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _project_root() / path
    return str(path.resolve())


class Settings(BaseSettings):
    app_name: str = "Pan Hotel Watch"
    environment: str = "local"
    database_url: str = "sqlite+aiosqlite:///./pan_hotel_watch.db"
    database_auto_create_tables: bool = True

    browser_state_dir: str = ".runtime/browser-state"
    playwright_artifact_dir: str = "output/playwright"
    # 群资料归档（每日实施经营数据/文档）专用目录：原始附件永久落盘，
    # 与 ebk_business_retention_days 等清理逻辑无关，任何任务不得清理。
    feishu_group_archive_dir: str = "data/feishu-group-archive"
    # Runtime cleanup retention (days). Configurable so the scheduled task and
    # the manual API/frontend can change retention without code edits.
    # Browser profile web-cache (Cache/Code Cache/GPU/Service Worker/etc.);
    # login state and Chromium models are never affected by this value.
    browser_cache_retention_days: int = 60
    # Playwright OTA-read screenshots under playwright_artifact_dir (*.png);
    # older than this many days are removed by the scheduled cleanup.
    playwright_screenshot_retention_days: int = 30

    # EBK 业务数据（ebk_read_snapshots + ctrip_ebooking_watch_runs）留存天数，
    # 每天凌晨自动清理 + 手动清理 API 共用的服务端留存阈值。
    ebk_business_retention_days: int = 180

    ctrip_ebooking_profile_dir: str = ""
    ctrip_ebooking_url: str = "https://ebooking.ctrip.com/login/index"
    ctrip_ebooking_login_url: str = "https://ebooking.ctrip.com/login/index"
    ctrip_ebooking_home_url: str = "https://ebooking.ctrip.com/home/mainland"
    ctrip_ebooking_username: str = ""
    ctrip_ebooking_password: str = ""
    ctrip_ebooking_auto_submit_login: bool = False
    ctrip_ebooking_default_platform_account_id: str = ""
    ctrip_ebooking_default_browser_session_id: str = ""
    ctrip_ebooking_default_display_name: str = "Ctrip EBK account"
    ctrip_ebooking_default_session_port: int = 9221
    # 美团/飞猪 EBK 浏览器的 CDP 调试端口（评价/退单适配器 attach 用）。
    meituan_ebooking_default_session_port: int = 9521
    fliggy_ebooking_default_session_port: int = 9531
    ctrip_public_competitor_login_url: str = "https://hotels.ctrip.com/"
    ctrip_public_competitor_enable_cdp: bool = False
    ctrip_public_competitor_cdp_url: str = "http://127.0.0.1:9222"
    ctrip_public_competitor_cdp_fallback_enabled: bool = True

    # 评价自动回复：真实平台适配器开关。默认关闭，逆向接入并显式开启后才连真实浏览器；
    # 关闭时对应平台返回 NotReady，不产生任何请求或假数据。
    review_ctrip_real_adapter_enabled: bool = False
    review_meituan_real_adapter_enabled: bool = False
    review_fliggy_real_adapter_enabled: bool = False

    # 退单抓取：真实平台适配器开关（同上，默认关闭保证测试隔离与未部署环境安全）。
    refund_ctrip_real_adapter_enabled: bool = False
    refund_meituan_real_adapter_enabled: bool = False
    refund_fliggy_real_adapter_enabled: bool = False

    # 今日预定抓取（@机器人触发，只读）：真实平台适配器开关（默认关闭）。
    # 携程（订单管理-今日预订，全部今日订单口径）、飞猪（订单列表 listV2，今日全部口径）、
    # 美团（订单管理-全部订单，购买时间=今天口径）已接通。
    reservation_ctrip_real_adapter_enabled: bool = False
    reservation_fliggy_real_adapter_enabled: bool = False
    reservation_meituan_real_adapter_enabled: bool = False

    # 评价自动回复定时调度开关（read_only_scheduler 挂 ReviewFetchScheduler）。
    review_scheduler_enabled: bool = False

    # EBK 周/月/日维度「昨日快照」日终轮次：每天定时读前一日的经营+排名数据落库，
    # 供飞书历史查询。默认 09:30——美团「按日」口径每日 9 点更新前日数据，早于
    # 该时点会拿到前前日数据；携程/飞猪昨日数据届时早已稳定。
    period_read_day_snapshot_enabled: bool = True
    period_read_day_snapshot_time: str = "09:30"

    # 经营 BI 日报（bi_daily）：每日定时采集入库（默认 09:40，避开 09:30 日终快照）；
    # 飞书日报推送默认关闭，开启后按店铺推送到各自携程锚点组。采集只读既有
    # 可信表并写入 bi_daily 事实表，不驱动任何浏览器页面或调价链路。
    bi_daily_collection_enabled: bool = True
    bi_daily_collection_time: str = "09:40"
    bi_daily_push_enabled: bool = False
    bi_daily_push_time: str = "10:00"

    llm_provider: str = "deepseek"
    llm_base_url: str = "https://api.deepseek.com"
    llm_allowed_hosts: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_api_key: str = ""
    # V5 Step 1 — fire a best-effort multi-model health probe on startup.
    # Defaults on so production marks each model's status; tests that drive
    # the probe explicitly disable it to avoid racing the startup background
    # task against their own mocked HTTP transport.
    llm_startup_health_check_enabled: bool = True
    # V5 Step 6 — EBK push throttle. Ships OFF because the plan gates Step 6
    # behind "推送链路稳定运行 1-2 周无降级"; flip on once that is verified.
    ebk_push_throttle_enabled: bool = False
    ebk_push_throttle_cooldown_minutes: int = 120
    # 美团/飞猪 EBK 定时监控失败态异常提醒冷却（默认每小时最多 1 张提醒卡）。
    platform_ebk_failure_alert_cooldown_minutes: int = 60
    # EBK 全局定时推送（三平台统一调度 → 三张分类卡）。默认 OFF：迁移门店
    # 关闭旧 auto_poll_enabled 后再打开（设计 §4.2）。
    ebk_global_push_scheduler_enabled: bool = False

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_app_credentials_json: str = ""
    feishu_chat_app_map_json: str = ""
    feishu_strict_tenant_routing: bool = False
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_default_actor_id: str = "operator"
    feishu_webhook_url: str = ""
    watch_api_shared_secret: str = ""
    pan_hotel_enforce_api_auth: bool = False
    feishu_default_platform_account_id: str = ""
    pan_hotel_bootstrap_supervisor_api_key: str = ""

    # 飞书入口模式：
    # - "hermes": 飞书自然语言对话由 Hermes Agent 接管，后端不再调用内置大模型；
    #   后端只通过 /api/v1/feishu/events 处理 Hermes 转发的卡片动作与结构化调价命令。
    # - "builtin": 旧行为，后端 feishu-listener 直接用内置大模型回复自由文本。
    feishu_agent_mode: str = "hermes"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig")

    @model_validator(mode="after")
    def _normalize_database_url(self) -> Self:
        self.database_url = _resolve_database_url(self.database_url)
        self.browser_state_dir = _resolve_runtime_dir(self.browser_state_dir)
        self.playwright_artifact_dir = _resolve_runtime_dir(self.playwright_artifact_dir)
        self.feishu_group_archive_dir = _resolve_runtime_dir(self.feishu_group_archive_dir)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
