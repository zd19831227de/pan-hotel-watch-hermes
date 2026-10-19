"""Platform adapters for the review management domain.

每个平台需要逆向四个 EBK 后台接口（评价列表 / 评价详情 / 提交回复 / 历史回复查询）。
逆向完成前默认返回 :class:`NotReadyReviewAdapter`，显式报“未接入”，
绝不为生产环境编造评价或回复数据（Real data first）。

接入真实实现时实现 :class:`ReviewPlatformAdapter` 并调用
:func:`register_review_adapter` 注册；上层 service / API / 飞书链路不需要改动。

TODO(Phase D 逆向，接入后替换本注册表):
- ctrip:   ebooking.ctrip.com 评价管理页接口（列表/详情/回复提交/历史回复）
- meituan: 美团商家后台评价管理接口
- fliggy:  飞猪商家后台评价管理接口
Cookie/登录态复用现有 EBK browser session 管理，不单独建登录链路；
登录过期/风控时抛 ReviewSubmitError(human_required 语义) 交由上层推送人工处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .types import ReviewItemInput


class ReviewAdapterError(RuntimeError):
    """评价适配器基础异常，message 面向运营可读。"""


class ReviewAdapterNotReadyError(ReviewAdapterError):
    """平台评价接口尚未逆向接入。"""


class ReviewSubmitError(ReviewAdapterError):
    """提交回复失败（登录过期 / 风控 / 平台报错）。"""


def to_review_adapter_error(exc: BaseException) -> ReviewAdapterError:
    """把 Playwright/网络层原生异常包装成运营可读的 ReviewAdapterError。

    适配器内 CDP 连接失败、页面 goto 超时、浏览器被人工关闭等都不是领域异常，
    但会穿透 service 层的 ReviewAdapterError 捕获：轻则任务行悬挂在 running，
    重则 API 直接 500 泄漏原始 traceback。统一在这里转成可读错误。
    """
    return ReviewAdapterError(
        f"评价页访问失败（{type(exc).__name__}），请确认平台浏览器在线后重试。"
    )


# 批量回复熔断阈值：每条回复一个独立页面会话（goto networkidle 约 40s），
# 浏览器掉线/假死时 max_count*3=60 次会全部撞墙白耗约 40 分钟且零提交
# （2026-10-08 巡检 F3，携程 60 条全部 TimeoutError 的根因形态）。
# 连续 N 次页面会话失败即放弃本轮，失败行如实带回由日报卡转橙提醒。
SUBMIT_PAGE_FAILURE_ABORT_THRESHOLD = 3


@dataclass
class PlatformReviewSnapshot:
    """一次评价列表抓取的结果。

    pending_total：平台侧未回复总数（页面「未回复(N)」解析）。等于本轮抓到的
    pending 条数时说明未回复列表已抓全，可用于对账清理库里的陈旧 pending。
    """

    hotel_id: str
    reviews: list[ReviewItemInput] = field(default_factory=list)
    pending_total: int | None = None


@dataclass
class HistoryReply:
    """一条历史已回复评价（用于首次构建回复模板库）。"""

    review_id: str
    hotel_id: str
    reply_content: str
    user_nickname: str | None = None
    score: float | None = None
    content: str | None = None
    review_time: str | None = None
    replied_at: str | None = None


class ReviewPlatformAdapter(Protocol):
    platform: str

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        """拉取评价列表（含已回复状态）。"""
        ...

    async def fetch_history_replies(self, limit: int = 200) -> list[HistoryReply]:
        """拉取历史已回复评价及回复内容。"""
        ...

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        """对指定评价提交回复；成功返回包含 ok=True 的结果。"""
        ...


class NotReadyReviewAdapter:
    """逆向未完成前的占位适配器：所有操作显式失败，不产生数据。"""

    def __init__(self, platform: str) -> None:
        self.platform = platform

    async def fetch_reviews(self) -> PlatformReviewSnapshot:
        raise ReviewAdapterNotReadyError(
            f"{self.platform} 评价列表接口尚未接入（等待逆向），未抓取任何数据。"
        )

    async def fetch_history_replies(self, limit: int = 200) -> list[HistoryReply]:
        raise ReviewAdapterNotReadyError(
            f"{self.platform} 历史回复接口尚未接入（等待逆向），未导入任何模板。"
        )

    async def submit_reply(self, review_id: str, reply_content: str) -> dict:
        raise ReviewAdapterNotReadyError(
            f"{self.platform} 提交回复接口尚未接入（等待逆向），未提交任何内容。"
        )


_ADAPTERS: dict[str, ReviewPlatformAdapter] = {}
# (platform, session_port) -> (注册原型, 按店铺端口构建的适配器)。同店铺复用以
# 保留 content cache 等会话内状态；原型被重新注册（测试/热替换）时缓存失效。
_PORT_ADAPTERS: dict[
    tuple[str, int], tuple[ReviewPlatformAdapter, ReviewPlatformAdapter]
] = {}


def register_review_adapter(adapter: ReviewPlatformAdapter) -> None:
    _ADAPTERS[adapter.platform] = adapter


def _maybe_register_real_adapters() -> None:
    """按 settings 开关注册真实适配器；未开启的平台保持 NotReady。"""
    try:
        from app.core.settings import get_settings

        settings = get_settings()
        if settings.review_ctrip_real_adapter_enabled:
            from .ctrip_review_adapter import CtripReviewAdapter

            register_review_adapter(CtripReviewAdapter())
        if settings.review_meituan_real_adapter_enabled:
            from .meituan_review_adapter import MeituanReviewAdapter

            register_review_adapter(MeituanReviewAdapter())
        if settings.review_fliggy_real_adapter_enabled:
            from .fliggy_review_adapter import FliggyReviewAdapter

            register_review_adapter(FliggyReviewAdapter())
    except Exception:
        # 适配器注册失败不应阻断应用启动；该平台回退 NotReady，但要留排查线索。
        import logging

        logging.getLogger(__name__).warning(
            "register real review adapter failed; falling back to NotReady",
            exc_info=True,
        )


_maybe_register_real_adapters()


def resolve_review_session_port(platform: str, browser_session_id: str | None) -> int | None:
    """把店铺（EBK 浏览器账号）解析为 CDP 调试端口。

    browser_session_id 为空返回 None（平台默认端口，旧行为）；账号不存在 /
    平台不符 / 不是 EBK 账号时抛 ReviewAdapterError——绝不能静默回落默认端口，
    否则会把抓取、尤其是回复提交写到错误的店铺。
    """
    normalized = str(browser_session_id or "").strip()
    if not normalized:
        return None
    from app.domain.browser.sessions import get_browser_session_store

    try:
        session = get_browser_session_store().get(normalized)
    except KeyError as exc:
        raise ReviewAdapterError(
            f"店铺浏览器账号（{normalized}）不存在，请先在「账号与浏览器」确认后重试。"
        ) from exc
    if session.platform != platform:
        raise ReviewAdapterError(
            f"店铺浏览器账号（{normalized}）是 {session.platform} 账号，与评价平台 {platform} 不符。",
        )
    if session.intended_use != "ebk":
        raise ReviewAdapterError(
            f"店铺浏览器账号（{normalized}）不是 EBK 账号，不能用于评价管理。",
        )
    port = int(session.session_port)
    if not 1024 <= port <= 65535:
        raise ReviewAdapterError(
            f"店铺浏览器账号（{normalized}）的调试端口配置无效，请检查账号配置。",
        )
    return port


def default_store_browser_session_ids() -> dict[str, str]:
    """各平台默认端口对应的店铺账号 id（用于旧数据回填店铺归属）。

    返回 {platform: browser_session_id}；找不到匹配账号的平台不出现。
    """
    from contextlib import suppress

    from app.core.settings import get_settings

    from app.domain.browser.sessions import get_browser_session_store

    try:
        sessions = get_browser_session_store().list()
    except Exception:
        return {}
    settings = get_settings()
    ports = {
        "ctrip": getattr(settings, "ctrip_ebooking_default_session_port", 9221),
        "meituan": getattr(settings, "meituan_ebooking_default_session_port", 9521),
        "fliggy": getattr(settings, "fliggy_ebooking_default_session_port", 9531),
    }
    resolved: dict[str, str] = {}
    for session in sessions:
        if getattr(session, "intended_use", "") != "ebk":
            continue
        platform = getattr(session, "platform", "")
        with suppress(KeyError, ValueError, AttributeError):
            if int(session.session_port) == int(ports[platform]):
                resolved[platform] = session.browser_session_id
    return resolved


def get_review_adapter(
    platform: str,
    browser_session_id: str | None = None,
) -> ReviewPlatformAdapter:
    """取平台适配器；指定店铺时按该店铺浏览器的 CDP 端口构建独立实例。"""
    port = resolve_review_session_port(platform, browser_session_id)
    registered = _ADAPTERS.get(platform)
    if registered is None:
        return NotReadyReviewAdapter(platform)
    if port is None:
        return registered
    key = (platform, port)
    cached = _PORT_ADAPTERS.get(key)
    if cached is not None and cached[0] is registered:
        return cached[1]
    adapter = _build_port_scoped_adapter(registered, port)
    _PORT_ADAPTERS[key] = (registered, adapter)
    return adapter


def _build_port_scoped_adapter(
    prototype: ReviewPlatformAdapter, port: int
) -> ReviewPlatformAdapter:
    """按端口克隆已注册适配器；构造器不支持 session_port 时（fake/占位）原样返回。"""
    import inspect

    try:
        params = inspect.signature(prototype.__init__).parameters
    except (TypeError, ValueError):
        return prototype
    if "session_port" not in params:
        return prototype
    return type(prototype)(session_port=port)


def list_ready_platforms() -> list[str]:
    """已注册真实适配器的平台（供前端展示接口接入状态）。"""
    return sorted(_ADAPTERS)
