"""自学习路由存储（2026-10-08）：兜底探索成功的问法 → 工具集映射。

定位（常规 agent 能力闭环的「记录/复用」半边）：
- 2026-10-09 路由翻转后：词表只剩查房价/手动调价两条安全通道，其余问法
  统一 generic_agent 全目录放行；学习库在该路由上携带「此前该问法用过
  哪些工具」的先验提示（软提示，白名单始终是全目录）。
- 通用轮成功作答后由网关插件把 (规范化问法, 本轮实际用过的只读工具)
  记入本模块管理的 JSON 存储；同一问法再问直接带先验，不再从零探索。
- 人工可随时删除/编辑该 JSON 文件（每轮对话实时读取，无缓存）。
- 一切读写失败都不影响对话（调用方 try/except 兜底）。

存储格式（.runtime/learned_routes.json）：
    {"<规范化问法>": {"tools": [...], "hits": 3, "updated_at": "..."}}
上限 _MAX_ENTRIES 条，超出按 (hits, updated_at) 淘汰最不常用条目。
"""

from __future__ import annotations

import json
import os
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# client/ → pan-hotel-hermes → 仓库根（与网关插件同基址，.runtime 下共享）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = _REPO_ROOT / ".runtime" / "learned_routes.json"

_MAX_ENTRIES = 500
_MIN_PHRASE_LEN = 2
# 规范化后剔除的中西文标点（问法不应因标点/空白/全半角差异错过复用）。
_STRIP_PUNCTUATION = "，。！？；：、,.!?;:\"'“”‘’()（）[]【】{}<>《》「」『』~～…—-*·\t\r\n "

# 多网关进程（每飞书应用一个）共享同一文件：读-改-写串行化靠进程内锁 +
# 原子替换；跨进程竞态最坏丢一条记录（写入频率为「兜底成功作答」级，
# 可接受），不会损坏文件（json 前先写临时文件再 os.replace）。
_PROCESS_LOCK = threading.Lock()


def learned_routes_enabled() -> bool:
    """人工停用开关：HERMES_LEARNED_ROUTES_DISABLED=1 时只查不记也不复用。"""
    return str(os.environ.get("HERMES_LEARNED_ROUTES_DISABLED") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }


def normalize_phrase(text: str) -> str:
    """问法规范化：NFKC（全半角归一）→ 小写 → 去空白与常见标点。"""
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    return "".join(ch for ch in value if ch not in _STRIP_PUNCTUATION).strip()


def _load_store() -> dict:
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STORE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp_path, STORE_PATH)


def lookup_learned_tools(user_text: str) -> tuple[str, ...] | None:
    """精确（规范化后）命中返回记录的工具名元组；未命中/未启用返回 None。"""
    if not learned_routes_enabled():
        return None
    key = normalize_phrase(user_text)
    if len(key) < _MIN_PHRASE_LEN:
        return None
    with _PROCESS_LOCK:
        entry = _load_store().get(key)
    if not isinstance(entry, dict):
        return None
    tools = entry.get("tools")
    if not isinstance(tools, list) or not tools:
        return None
    return tuple(str(tool) for tool in tools if str(tool or "").strip())


def record_learned_route(user_text: str, tools: list[str] | tuple[str, ...]) -> bool:
    """记录一条「问法 → 实际用过的工具」；同问法重复记录只更新时间/计数。

    返回是否写入成功；任何异常由调用方兜底，不抛出。
    """
    if not learned_routes_enabled():
        return False
    key = normalize_phrase(user_text)
    clean_tools = sorted({str(tool or "").strip() for tool in tools if str(tool or "").strip()})
    if len(key) < _MIN_PHRASE_LEN or not clean_tools:
        return False
    now_text = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        with _PROCESS_LOCK:
            store = _load_store()
            previous = store.get(key)
            hits = 1
            if isinstance(previous, dict) and isinstance(previous.get("hits"), int):
                hits = int(previous["hits"]) + 1
            store[key] = {"tools": clean_tools, "hits": hits, "updated_at": now_text}
            _evict_if_over_limit(store)
            _save_store(store)
    except Exception:
        return False
    return True


def _evict_if_over_limit(store: dict) -> None:
    """超过上限时淘汰 (hits 升序, updated_at 升序) 的最不常用条目。"""
    overflow = len(store) - _MAX_ENTRIES
    if overflow <= 0:
        return

    def _rank(item: tuple[str, dict]) -> tuple[int, str]:
        entry = item[1]
        hits = entry.get("hits") if isinstance(entry, dict) else None
        updated = entry.get("updated_at") if isinstance(entry, dict) else None
        return (int(hits) if isinstance(hits, int) else 0, str(updated or ""))

    for key, _ in sorted(store.items(), key=_rank)[:overflow]:
        store.pop(key, None)


def count_learned_routes() -> int:
    """当前学习库条数（巡检/测试用）。"""
    with _PROCESS_LOCK:
        return len(_load_store())
