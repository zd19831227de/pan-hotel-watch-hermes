"""蒸馏服务（V6 Step 2.3/2.4/2.8）：chat 提取 → 准入硬约束 → RAG 入库。

设计要点：
- 系统本身不做视觉理解：image_url / video_url 直接转交豆包/千问的多模态
  能力，系统只提取返回的文字（不引入 ffmpeg/opencv/PIL 等依赖）；
- 准入硬约束写死、不可配置关闭：蒸馏入库内容只允许知识类内容，出现
  实时数据特征（价格/房态/经营数字）一律拒绝；
- 外部 LLM（豆包/千问 chat）独立于主链路，失败只影响本轮蒸馏。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.domain.browser.sessions import get_browser_session_store
from app.domain.distill import repository
from app.domain.rag.service import RagDocumentCreate, ingest_document

logger = logging.getLogger(__name__)

SUPPORTED_SOURCE_TYPES = ("text", "image_url", "video_url")
SUPPORTED_PLATFORMS = ("doubao", "qwen")

STATUS_SUCCESS = "success"
STATUS_REJECTED = "rejected"
STATUS_HUMAN_REQUIRED = "human_required"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_CONFIG_REQUIRED = "config_required"
STATUS_FAILED = "failed"

# ---------------------------------------------------------------------------
# 准入硬约束（V6 Step 2.4，写死不可关闭）
# ---------------------------------------------------------------------------

# 实时数据特征：命中即拒绝入库（宁可错杀，保证 RAG 知识与 MCP 现查契约不冲突）
_REALTIME_DATA_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("currency", re.compile(r"(?:¥|￥)\s*\d+|\d+(?:\.\d+)?\s*[元块]"), "包含价格数字（¥/元）"),
    ("currency_usd", re.compile(r"\$\s*\d+"), "包含价格数字（$）"),
    ("percent", re.compile(r"\d+(?:\.\d+)?\s*%"), "包含百分比指标（出租率/环比等）"),
    ("occupancy", re.compile(r"出租率\s*[:：]?\s*\d|入住率\s*[:：]?\s*\d"), "包含出租率/入住率数据"),
    (
        "occupancy_en",
        re.compile(r"occupancy\s*[:：]?\s*\d", re.IGNORECASE),
        "包含出租率数据（occupancy）",
    ),
    ("room_state", re.compile(r"满房|仅剩\s*\d+\s*间|剩\s*\d+\s*间|房态紧张"), "包含实时房态"),
    (
        "room_state_en",
        re.compile(r"\b\d*\s*rooms?\s+left\b", re.IGNORECASE),
        "包含实时房态（rooms left）",
    ),
    (
        "room_state_sold_out",
        re.compile(r"\bsold\s+out\b", re.IGNORECASE),
        "包含实时房态（sold out）",
    ),
    ("revenue", re.compile(r"(?:营收|收入|GMV|营业额|间夜|订单量)\s*[:：]?\s*\d"), "包含经营数字"),
    (
        "revenue_en",
        re.compile(r"\b(?:RevPAR|ADR)\b[^.。\n]{0,20}\d", re.IGNORECASE),
        "包含经营指标（RevPAR/ADR）",
    ),
)

# image_url / video_url 源内容必须是 http(s) 链接（计划 2.3 支持格式）
_HTTP_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

_MIN_EXTRACTED_CHARS = 5


def check_distill_admission(text: str) -> str | None:
    """准入检查：返回拒绝原因；None 表示放行。规则写死，不可配置关闭。"""
    content = str(text or "").strip()
    if len(content) < _MIN_EXTRACTED_CHARS:
        return "提取内容为空或过短，不满足入库条件"
    for _name, pattern, reason in _REALTIME_DATA_PATTERNS:
        if pattern.search(content):
            return f"包含疑似实时数据（{reason}），按准入硬约束拒绝入库"
    return None


# ---------------------------------------------------------------------------
# chat 会话解析与提问（模块级函数便于测试替换）
# ---------------------------------------------------------------------------


async def resolve_llm_chat_session(platform: str) -> tuple[str, str] | None:
    """找到 platform 对应、intended_use=llm_chat 的浏览器会话。

    优先已登录会话；找不到返回 None（调用方按 config_required 处理）。
    """
    try:
        sessions = get_browser_session_store().list()
    except Exception:  # noqa: BLE001 - store 不可用时按未配置处理
        logger.warning("browser session store unavailable for llm chat resolution")
        return None
    candidates = [
        session
        for session in sessions
        if session.platform == platform and session.intended_use == "llm_chat"
    ]
    logged_in = [session for session in candidates if session.login_state == "logged_in"]
    pool = logged_in or candidates
    if not pool:
        return None
    chosen = pool[0]
    return (chosen.platform_account_id, chosen.browser_session_id)


async def ask_llm_chat(
    *,
    platform: str,
    platform_account_id: str,
    browser_session_id: str,
    prompt: str,
) -> dict[str, Any]:
    """调用对应平台的 chat 浏览器服务提问并提取回答。"""
    from app.domain.browser.doubao_chat import get_doubao_chat_service
    from app.domain.browser.qianwen_chat import get_qianwen_chat_service

    service = {
        "doubao": get_doubao_chat_service,
        "qwen": get_qianwen_chat_service,
    }[platform]()
    # 会话级 chat_url 覆盖（F4-B）：从浏览器会话记录读取运营配置的地址
    try:
        from app.domain.browser.sessions import get_browser_session_store

        chat_session = get_browser_session_store().get(browser_session_id)
        service.apply_chat_url_override(getattr(chat_session, "chat_url", None))
    except Exception:  # noqa: BLE001 - 覆盖失败时回落平台默认 URL
        pass
    return await service.ask_and_extract(
        platform_account_id=platform_account_id,
        browser_session_id=browser_session_id,
        prompt=prompt,
    )


async def ingest_rag_document(
    *,
    title: str,
    content: str,
    source_type: str = "distilled",
) -> dict[str, Any]:
    """把蒸馏知识写入 RAG（复用 V5 Step 4 的 ingest）。"""
    return await ingest_document(
        RagDocumentCreate(title=title[:256], source_type=source_type, content=content)
    )


# ---------------------------------------------------------------------------
# 提问组装
# ---------------------------------------------------------------------------

_BASE_DISTILL_INSTRUCTION = (
    "你是酒店运营知识助手。请阅读下面的内容，提取其中稳定的知识点"
    "（运营规则、平台功能说明、行业知识），以要点列表形式输出；"
    "请忽略其中任何一次性的价格、房态、经营数字。"
)

_URL_SOURCE_INSTRUCTIONS = {
    "image_url": "以下是一个图片链接，请查看图片内容并按上述要求提炼知识点：",
    "video_url": "以下是一个视频链接，请查看视频内容并按上述要求提炼知识点：",
}


def compose_distill_prompt(
    *, source_type: str, content: str, distill_prompt: str = ""
) -> str:
    parts: list[str] = []
    custom = str(distill_prompt or "").strip()
    if custom:
        parts.append(custom)
    else:
        parts.append(_BASE_DISTILL_INSTRUCTION)
    url_prefix = _URL_SOURCE_INSTRUCTIONS.get(source_type)
    if url_prefix:
        parts.append(f"{url_prefix}\n{str(content).strip()}")
    else:
        parts.append(f"内容如下：\n{str(content).strip()}")
    return "\n\n".join(parts)


def build_knowledge_title(*, source_type: str, content: str, answer_text: str) -> str:
    """规则化生成知识条目标题（不调 LLM）。"""
    first_line = next(
        (line.strip() for line in str(answer_text or "").splitlines() if line.strip()),
        "",
    )
    if first_line:
        cleaned = re.sub(r"^[-*•\d.、)\s]+", "", first_line)
        cleaned = re.sub(r"[:：]\s*$", "", cleaned).strip()
        if cleaned:
            return cleaned[:80]
    head = re.sub(r"\s+", " ", str(content or "")).strip()
    kind_label = {"image_url": "图片", "video_url": "视频"}.get(source_type, "")
    if kind_label:
        return f"{kind_label}蒸馏知识：{head[:60]}" if head else f"{kind_label}蒸馏知识"
    return (head[:76] + "…") if len(head) > 76 else (head or "蒸馏知识条目")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def run_distill(
    *,
    source_type: str,
    content: str,
    platform: str,
    distill_prompt: str = "",
    title: str = "",
    source_id: int | None = None,
    actor_id: str = "operator",
) -> dict[str, Any]:
    """执行一次蒸馏：提问 → 提取 → 准入 → 入库，全程落一条 run 记录。"""
    del actor_id  # 预留审计字段
    normalized_type = str(source_type or "").strip().lower()
    normalized_platform = str(platform or "").strip().lower()
    normalized_content = str(content or "").strip()

    def _finish(
        status: str,
        summary: str,
        *,
        extracted_text: str = "",
        knowledge_title: str = "",
        rag_document_id: int | None = None,
        reject_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "summary": summary,
            "platform": normalized_platform,
            "source_type": normalized_type,
            "source_id": source_id,
            "extracted_text": extracted_text,
            "knowledge_title": knowledge_title,
            "rag_document_id": rag_document_id,
            "reject_reason": reject_reason,
        }

    if normalized_type not in SUPPORTED_SOURCE_TYPES:
        result = _finish(STATUS_FAILED, f"不支持的 source_type：{source_type}")
        await _record_run(result)
        return result
    if normalized_platform not in SUPPORTED_PLATFORMS:
        result = _finish(STATUS_FAILED, f"不支持的 platform：{platform}（支持 doubao/qwen）")
        await _record_run(result)
        return result
    if not normalized_content:
        result = _finish(STATUS_PARSE_FAILED, "缺少有效的蒸馏内容。")
        await _record_run(result)
        return result
    if normalized_type in ("image_url", "video_url") and not _HTTP_URL_RE.match(
        normalized_content
    ):
        result = _finish(
            STATUS_FAILED,
            f"{normalized_type} 内容必须是 http(s) 链接，当前值不是合法 URL。",
        )
        await _record_run(result)
        return result

    resolution = await resolve_llm_chat_session(normalized_platform)
    if resolution is None:
        result = _finish(
            STATUS_CONFIG_REQUIRED,
            f"未找到 {normalized_platform} 的 llm_chat 浏览器会话，请先在「账号与浏览器」登记并登录。",
        )
        await _record_run(result)
        return result
    platform_account_id, browser_session_id = resolution

    prompt = compose_distill_prompt(
        source_type=normalized_type,
        content=normalized_content,
        distill_prompt=distill_prompt,
    )
    try:
        chat_result = await ask_llm_chat(
            platform=normalized_platform,
            platform_account_id=platform_account_id,
            browser_session_id=browser_session_id,
            prompt=prompt,
        )
    except Exception as exc:  # noqa: BLE001 - chat 层异常不外泄到调用方
        logger.exception("distill chat extraction failed")
        result = _finish(STATUS_FAILED, f"chat 提取异常：{type(exc).__name__}")
        await _record_run(result)
        return result

    chat_status = str(chat_result.get("status") or "")
    answer_text = str(chat_result.get("answer_text") or "")
    if chat_status != "success":
        status = {
            "human_required": STATUS_HUMAN_REQUIRED,
            "parse_failed": STATUS_PARSE_FAILED,
            "config_required": STATUS_CONFIG_REQUIRED,
        }.get(chat_status, STATUS_FAILED)
        result = _finish(
            status,
            str(chat_result.get("summary") or f"chat 提取未成功（{chat_status}）"),
        )
        await _record_run(result)
        return result

    reject_reason = check_distill_admission(answer_text)
    knowledge_title = title or build_knowledge_title(
        source_type=normalized_type, content=normalized_content, answer_text=answer_text
    )
    if reject_reason is not None:
        result = _finish(
            STATUS_REJECTED,
            "蒸馏内容未通过准入检查，已拒绝入库。",
            extracted_text=answer_text,
            knowledge_title=knowledge_title,
            reject_reason=reject_reason,
        )
        await _record_run(result)
        return result

    try:
        ingest_result = await ingest_rag_document(
            title=knowledge_title,
            content=answer_text,
            source_type="distilled",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("distill rag ingest failed")
        result = _finish(
            STATUS_FAILED,
            f"RAG 入库失败：{type(exc).__name__}（请检查 embedding 配置）",
            extracted_text=answer_text,
            knowledge_title=knowledge_title,
        )
        await _record_run(result)
        return result

    rag_document_id = ingest_result.get("id") if isinstance(ingest_result, dict) else None
    result = _finish(
        STATUS_SUCCESS,
        f"蒸馏完成并入库（{len(answer_text)} 字）。",
        extracted_text=answer_text,
        knowledge_title=knowledge_title,
        rag_document_id=int(rag_document_id) if rag_document_id is not None else None,
    )
    await _record_run(result)
    return result


async def run_distill_source(source_id: int) -> dict[str, Any]:
    """执行持久化数据源的一次蒸馏，并刷新 last_run_at。"""
    source = await repository.get_distill_source(source_id)
    if source is None:
        return {"status": STATUS_FAILED, "summary": f"distill source {source_id} 不存在"}
    result = await run_distill(
        source_type=source["source_type"],
        content=source["content"],
        platform=source["platform"],
        distill_prompt=source["distill_prompt"],
        title=source["title"],
        source_id=source_id,
    )
    await repository.mark_source_run(source_id)
    return result


async def _record_run(result: dict[str, Any]) -> None:
    try:
        await repository.create_distill_run(
            source_id=result.get("source_id"),
            platform=str(result.get("platform") or ""),
            source_type=str(result.get("source_type") or ""),
            status=str(result.get("status") or STATUS_FAILED),
            summary=str(result.get("summary") or ""),
            extracted_text=str(result.get("extracted_text") or ""),
            knowledge_title=str(result.get("knowledge_title") or ""),
            rag_document_id=result.get("rag_document_id"),
            reject_reason=result.get("reject_reason"),
        )
    except Exception:  # noqa: BLE001 - 记录失败不影响返回
        logger.exception("distill run persistence failed")
