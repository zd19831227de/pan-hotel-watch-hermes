from __future__ import annotations

import re
from typing import Any


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _name_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        text = _normalize_text(value)
        if not text:
            continue
        for token in re.split(r"[^\w\u4e00-\u9fff]+", text):
            piece = token.strip()
            if len(piece) >= 2:
                tokens.add(piece)
        if len(text) >= 2:
            tokens.add(text)
    return tokens


def _match_score(ebk_group: dict[str, Any], competitor_group: dict[str, Any]) -> int:
    ebk_account = _normalize_text(ebk_group.get("platform_account_id"))
    pricing_account = _normalize_text(competitor_group.get("pricing_platform_account_id"))
    if ebk_account and pricing_account and ebk_account == pricing_account:
        return 100

    ebk_tokens = _name_tokens(ebk_group.get("name"), ebk_group.get("brief_title"))
    comp_tokens = _name_tokens(
        competitor_group.get("name"),
        competitor_group.get("brief_title"),
        *(competitor_group.get("own_hotels") or []),
    )
    if not ebk_tokens or not comp_tokens:
        return 0
    overlap = ebk_tokens & comp_tokens
    if not overlap:
        return 0
    return 10 + len(overlap) * 5


def build_store_catalog(
    *,
    ebk_groups: list[dict[str, Any]],
    competitor_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    used_ebk_keys: set[str] = set()
    used_competitor_keys: set[str] = set()

    # P6：shop_key 精确配对优先（事实来源，不受命名影响）；未归属的旧组
    # 走下方 pricing_account/单店上下文兜底，待店铺归属补齐后自然退役。
    ebk_by_shop: dict[str, dict[str, Any]] = {}
    for ebk in ebk_groups:
        shop_key = str(ebk.get("shop_key") or "").strip()
        ebk_key = str(ebk.get("group_key") or "").strip()
        if shop_key and ebk_key and ebk_key not in used_ebk_keys:
            ebk_by_shop.setdefault(shop_key, ebk)
    for competitor in competitor_groups:
        shop_key = str(competitor.get("shop_key") or "").strip()
        comp_key = str(competitor.get("group_key") or "").strip()
        if not shop_key or not comp_key or comp_key in used_competitor_keys:
            continue
        ebk = ebk_by_shop.get(shop_key)
        if ebk is None:
            continue
        ebk_key = str(ebk.get("group_key") or "").strip()
        used_ebk_keys.add(ebk_key)
        used_competitor_keys.add(comp_key)
        own_hotels = [
            str(item).strip()
            for item in (competitor.get("own_hotels") or [])
            if str(item or "").strip()
        ]
        catalog.append(
            {
                "store_label": (
                    own_hotels[0]
                    if own_hotels
                    else str(competitor.get("name") or comp_key)
                ),
                "shop_key": shop_key,
                "ebk_watch_group_key": ebk_key,
                "ebk_group_name": ebk.get("name"),
                "ebk_platform_account_id": ebk.get("platform_account_id"),
                "competitor_watch_group_key": comp_key,
                "competitor_group_name": competitor.get("name"),
                "competitor_platform_account_id": competitor.get("platform_account_id"),
                "own_hotels": own_hotels,
                "competitor_hotels": [
                    str(item).strip()
                    for item in (competitor.get("competitor_hotels") or [])
                    if str(item or "").strip()
                ],
                "pair_score": 100,
                "pair_source": "shop_key",
                "pair_complete": True,
            }
        )

    single_context_fallback = len(ebk_groups) == 1 and len(competitor_groups) == 1

    for competitor in competitor_groups:
        comp_key = str(competitor.get("group_key") or "").strip()
        if not comp_key or comp_key in used_competitor_keys:
            continue
        best_ebk: dict[str, Any] | None = None
        best_score = 0
        for ebk in ebk_groups:
            ebk_key = str(ebk.get("group_key") or "").strip()
            if not ebk_key or ebk_key in used_ebk_keys:
                continue
            score = _match_score(ebk, competitor)
            if score > best_score:
                best_score = score
                best_ebk = ebk
        pair_source = "pricing_account" if best_score >= 100 else "name_match" if best_ebk else None
        if best_ebk is None and single_context_fallback:
            candidate = ebk_groups[0]
            candidate_key = str(candidate.get("group_key") or "").strip()
            if candidate_key and candidate_key not in used_ebk_keys:
                # A Feishu ops group normally represents one hotel; this fallback is read-only
                # context pairing and does not grant pricing write permission.
                best_ebk = candidate
                best_score = 40
                pair_source = "single_group_context"

        own_hotels = [
            str(item).strip()
            for item in (competitor.get("own_hotels") or [])
            if str(item or "").strip()
        ]
        store_label = own_hotels[0] if own_hotels else str(competitor.get("name") or comp_key)
        catalog.append(
            {
                "store_label": store_label,
                "shop_key": str(competitor.get("shop_key") or "").strip() or None,
                "ebk_watch_group_key": best_ebk.get("group_key") if best_ebk else None,
                "ebk_group_name": best_ebk.get("name") if best_ebk else None,
                "ebk_platform_account_id": best_ebk.get("platform_account_id") if best_ebk else None,
                "competitor_watch_group_key": comp_key,
                "competitor_group_name": competitor.get("name"),
                "competitor_platform_account_id": competitor.get("platform_account_id"),
                "own_hotels": own_hotels,
                "competitor_hotels": [
                    str(item).strip()
                    for item in (competitor.get("competitor_hotels") or [])
                    if str(item or "").strip()
                ],
                "pair_score": best_score,
                "pair_source": pair_source,
                "pair_complete": bool(best_ebk),
            }
        )
        if best_ebk is not None:
            used_ebk_keys.add(str(best_ebk.get("group_key") or ""))

    for ebk in ebk_groups:
        ebk_key = str(ebk.get("group_key") or "").strip()
        if not ebk_key or ebk_key in used_ebk_keys:
            continue
        catalog.append(
            {
                "store_label": str(ebk.get("name") or ebk_key),
                "shop_key": str(ebk.get("shop_key") or "").strip() or None,
                "ebk_watch_group_key": ebk_key,
                "ebk_group_name": ebk.get("name"),
                "ebk_platform_account_id": ebk.get("platform_account_id"),
                "competitor_watch_group_key": None,
                "competitor_group_name": None,
                "competitor_platform_account_id": None,
                "own_hotels": [],
                "competitor_hotels": [],
                "pair_score": 0,
                "pair_source": None,
                "pair_complete": False,
            }
        )
    return catalog


def infer_binding_mode(
    *,
    ebk_groups: list[dict[str, Any]],
    competitor_groups: list[dict[str, Any]],
    store_catalog: list[dict[str, Any]],
) -> str:
    if not ebk_groups and not competitor_groups:
        return "unbound"
    complete_pairs = [item for item in store_catalog if item.get("pair_complete")]
    if len(complete_pairs) == 1 and len(store_catalog) == 1:
        return "single_store"
    if len(complete_pairs) >= 2 or len(store_catalog) >= 2:
        return "multi_store"
    if len(ebk_groups) <= 1 and len(competitor_groups) <= 1:
        return "single_store"
    return "multi_store"


def _store_hint_matches(store: dict[str, Any], store_hint: str) -> bool:
    hint = _normalize_text(store_hint)
    if not hint:
        return False
    candidates = [
        store.get("store_label"),
        store.get("ebk_group_name"),
        store.get("competitor_group_name"),
        store.get("ebk_watch_group_key"),
        store.get("competitor_watch_group_key"),
        *(store.get("own_hotels") or []),
    ]
    for candidate in candidates:
        text = _normalize_text(candidate)
        if not text:
            continue
        if hint in text or text in hint:
            return True
        if any(token in text for token in _name_tokens(store_hint) if len(token) >= 2):
            return True
    hint_tokens = _name_tokens(store_hint)
    label_tokens = _name_tokens(store.get("store_label"), *(store.get("own_hotels") or []))
    return bool(hint_tokens & label_tokens)


def resolve_store_selection(
    *,
    store_catalog: list[dict[str, Any]],
    store_hint: str | None = None,
) -> dict[str, Any]:
    complete_pairs = [item for item in store_catalog if item.get("pair_complete")]
    if not store_catalog:
        return {
            "status": "unbound",
            "reason": "unbound",
            "summary": "运营问答上下文群未绑定：当前飞书群还没有绑定到任何 EBK 数据中心监控组或竞品监控组。",
        }

    hint = str(store_hint or "").strip()
    if hint:
        matches = [item for item in store_catalog if _store_hint_matches(item, hint)]
        if len(matches) == 1:
            store = matches[0]
            return {
                "status": "resolved",
                "reason": "name_match",
                "store_label": store.get("store_label"),
                "shop_key": store.get("shop_key"),
                "ebk_watch_group_key": store.get("ebk_watch_group_key"),
                "competitor_watch_group_key": store.get("competitor_watch_group_key"),
                "summary": f"已按「{hint}」匹配到门店：{store.get('store_label')}。",
            }
        if len(matches) > 1:
            return {
                "status": "ambiguous",
                "reason": "multiple_name_matches",
                "summary": f"「{hint}」匹配到多个门店，请说得更具体一些。",
                "store_candidates": [_candidate_row(item) for item in matches],
            }
        return {
            "status": "ambiguous",
            "reason": "name_not_found",
            "summary": f"本群未找到与「{hint}」匹配的门店。",
            "store_candidates": [_candidate_row(item) for item in (complete_pairs or store_catalog)],
        }

    if len(complete_pairs) == 1:
        store = complete_pairs[0]
        return {
            "status": "resolved",
            "reason": "single_pair_auto",
            "store_label": store.get("store_label"),
            "shop_key": store.get("shop_key"),
            "ebk_watch_group_key": store.get("ebk_watch_group_key"),
            "competitor_watch_group_key": store.get("competitor_watch_group_key"),
            "summary": f"本群仅绑定 1 家门店：{store.get('store_label')}。",
        }

    if len(store_catalog) == 1:
        store = store_catalog[0]
        return {
            "status": "resolved",
            "reason": "single_group_auto",
            "store_label": store.get("store_label"),
            "shop_key": store.get("shop_key"),
            "ebk_watch_group_key": store.get("ebk_watch_group_key"),
            "competitor_watch_group_key": store.get("competitor_watch_group_key"),
            "summary": f"当前运营问答上下文群仅绑定 1 个监控组：{store.get('store_label')}。",
        }

    if complete_pairs or len(store_catalog) >= 2:
        return {
            "status": "ambiguous",
            "reason": "multi_store_requires_hint",
            "summary": "当前运营问答上下文群绑定了多家门店，请在问题里带上店名，例如「静里 上周简报」或「澜江路泉吉 竞品价格」。",
            "store_candidates": [_candidate_row(item) for item in (complete_pairs or store_catalog)],
        }

    return {
        "status": "ambiguous",
        "reason": "incomplete_pairs",
        "summary": "当前运营问答上下文群的监控组尚未完成 EBK 与竞品配对，请在控制台检查绑定。",
        "store_candidates": [_candidate_row(item) for item in store_catalog],
    }


def _candidate_row(store: dict[str, Any]) -> dict[str, Any]:
    return {
        "store_label": store.get("store_label"),
        "shop_key": store.get("shop_key"),
        "ebk_watch_group_key": store.get("ebk_watch_group_key"),
        "competitor_watch_group_key": store.get("competitor_watch_group_key"),
        "own_hotels": store.get("own_hotels") or [],
        "competitor_hotels": store.get("competitor_hotels") or [],
    }
