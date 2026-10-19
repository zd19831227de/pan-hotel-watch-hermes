from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_TERMS = (
    "cookie",
    "password",
    "profile",
    "verification_code",
    "screenshot",
    "secret",
    "token",
    "webhook",
)
_FORBIDDEN_ACTION_TERMS = (
    "write_to_ctrip",
    "auto_execute",
    "confirm_price_change",
    "立即调价",
    "写入携程",
    "自动执行",
)
_MEITUAN_DATA_CENTER_TABS = (
    ("data_center_home", "首页"),
    ("data_center_trade", "交易分析"),
    ("data_center_traffic", "流量分析"),
    ("data_center_service", "服务分析"),
)


def sanitize_ebk_analysis_value(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for item_key, item in value.items():
            key_text = str(item_key)
            lower = key_text.lower()
            if any(term in lower for term in _SENSITIVE_KEY_TERMS):
                if isinstance(item, bool):
                    cleaned[key_text] = item
                else:
                    cleaned[key_text] = "***"
                continue
            cleaned[key_text] = sanitize_ebk_analysis_value(item, key=key_text)
        return cleaned
    if isinstance(value, list):
        return [sanitize_ebk_analysis_value(item, key=key) for item in value]
    if isinstance(value, str):
        lower_key = key.lower()
        if any(term in lower_key for term in _SENSITIVE_KEY_TERMS):
            return "***"
    return value


def build_ebk_joint_analysis_context(
    *,
    ctrip_snapshot: dict[str, Any] | None = None,
    meituan_snapshot: dict[str, Any] | None = None,
    fliggy_snapshot: dict[str, Any] | None = None,
    analysis_mode: str,
    ctrip_error: str | None = None,
    meituan_error: str | None = None,
    fliggy_error: str | None = None,
) -> dict[str, Any]:
    platforms: dict[str, Any] = {}
    missing_data: list[str] = []
    read_times: dict[str, Any] = {}
    operator: dict[str, Any] = {
        "usage_note": "只读分析与汇报；不会创建或触发 OTA 写价。",
        "platforms": platforms,
        "read_times": read_times,
        "missing_data": missing_data,
    }

    if analysis_mode in {"joint", "ctrip"}:
        if ctrip_snapshot:
            platforms["ctrip"] = {
                "status": "available",
                "platform_account_id": ctrip_snapshot.get("platform_account_id"),
                "snapshot_status": ctrip_snapshot.get("status"),
            }
            operator["ctrip_ebk"] = _summarize_ctrip(ctrip_snapshot)
            read_times["ctrip"] = _read_time(ctrip_snapshot)
        else:
            platforms["ctrip"] = {"status": "missing", "reason": ctrip_error or "no_snapshot"}
            missing_data.append("携程 EBK 最新 snapshot 缺失，无法纳入联合分析。")

    if analysis_mode in {"joint", "meituan"}:
        if meituan_snapshot:
            platforms["meituan"] = {
                "status": "available",
                "platform_account_id": meituan_snapshot.get("platform_account_id"),
                "snapshot_status": meituan_snapshot.get("status"),
            }
            workbench, promotion, reviews, data_center = _summarize_meituan(meituan_snapshot)
            operator["meituan_workbench"] = workbench
            operator["meituan_promotion_coupon"] = promotion
            operator["meituan_reviews"] = reviews
            if data_center:
                operator["meituan_data_center"] = data_center
            read_times["meituan"] = _read_time(meituan_snapshot)
            for label, section in (
                ("工作台", workbench),
                ("神券", promotion),
                ("评价", reviews),
                ("数据中心", data_center),
            ):
                if section.get("available") is False:
                    missing_data.append(f"美团{label}数据缺失或未读取成功。")
        else:
            platforms["meituan"] = {"status": "missing", "reason": meituan_error or "no_snapshot"}
            missing_data.append("美团 EBK 最新 snapshot 缺失，无法纳入联合分析。")

    if analysis_mode in {"joint", "fliggy"}:
        if fliggy_snapshot:
            platforms["fliggy"] = {
                "status": "available",
                "platform_account_id": fliggy_snapshot.get("platform_account_id"),
                "snapshot_status": fliggy_snapshot.get("status"),
            }
            fliggy_sections = _summarize_fliggy(fliggy_snapshot)
            operator.update(fliggy_sections)
            read_times["fliggy"] = _read_time(fliggy_snapshot)
            for label, key in (
                ("首页", "fliggy_home"),
                ("诊断报告", "fliggy_diagnose"),
                ("经营数据", "fliggy_operating"),
                ("营销活动", "fliggy_promotion"),
            ):
                section = fliggy_sections.get(key)
                if isinstance(section, dict) and section.get("available") is False:
                    missing_data.append(f"飞猪{label}数据缺失或未读取成功。")
        else:
            platforms["fliggy"] = {"status": "missing", "reason": fliggy_error or "no_snapshot"}
            missing_data.append("飞猪 EBK 最新 snapshot 缺失，无法纳入联合分析。请先在控制台读取并保存。")

    available = [
        key for key, meta in platforms.items() if isinstance(meta, dict) and meta.get("status") == "available"
    ]
    if not available:
        status = "missing"
    elif missing_data and analysis_mode == "joint":
        status = "partial"
    else:
        status = "success"

    insights = _build_joint_insights(
        operator,
        analysis_mode=analysis_mode,
        available=available,
        status=status,
        missing_data=missing_data,
    )
    operator["conclusion"] = insights["conclusion"]
    operator["findings"] = insights["findings"]
    operator["actions"] = insights["actions"]
    operator["comparison_caliber_note"] = (
        "各平台页面展示的昨日金额和订单字段定义可能不同，"
        "仅作渠道表现参考，不作为同口径财务对账。"
    )

    payload = {
        "status": status,
        "source": "hermes_ebk_joint_analysis",
        "analysis_mode": analysis_mode,
        "summary": insights["conclusion"],
        "operator_summary": operator,
    }
    sanitized = sanitize_ebk_analysis_value(payload)
    assert isinstance(sanitized, dict)
    serialized = str(sanitized)
    for forbidden in _FORBIDDEN_ACTION_TERMS:
        if forbidden in serialized:
            raise ValueError(f"EBK analysis payload must not include execution action term: {forbidden}")
    return sanitized


def _payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = snapshot.get("payload_json")
    return payload if isinstance(payload, dict) else {}


def _read_time(snapshot: dict[str, Any]) -> str | None:
    payload = _payload(snapshot)
    return (
        str(payload.get("read_at") or "").strip()
        or str(snapshot.get("created_at") or "").strip()
        or None
    )


def _summarize_ctrip(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(snapshot)
    datasets = payload.get("datasets") if isinstance(payload.get("datasets"), dict) else {}
    monitor = {}
    platform_data = payload.get("platform_data") if isinstance(payload.get("platform_data"), dict) else {}
    if isinstance(platform_data.get("monitor"), dict):
        monitor = platform_data["monitor"]
    dataset_summaries: dict[str, Any] = {}
    for dataset_id, item in datasets.items():
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        dataset_summaries[str(dataset_id)] = {
            "status": item.get("status"),
            "metrics": metrics or data,
        }
    return {
        "available": True,
        "status": snapshot.get("status") or payload.get("status"),
        "summary": payload.get("summary"),
        "monitor": monitor,
        "datasets": dataset_summaries,
        "read_at": _read_time(snapshot),
    }


def _summarize_meituan(
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = _payload(snapshot)
    datasets = payload.get("datasets") if isinstance(payload.get("datasets"), dict) else {}

    workbench_raw = datasets.get("workbench_home") if isinstance(datasets.get("workbench_home"), dict) else {}
    workbench_data = workbench_raw.get("data") if isinstance(workbench_raw.get("data"), dict) else {}
    workbench = {
        "available": workbench_raw.get("status") == "success",
        "status": workbench_raw.get("status"),
        "realtime_data": workbench_data.get("realtime_data") or {},
        "yesterday_data": workbench_data.get("yesterday_data") or {},
        "hos_score": workbench_data.get("hos_score") or {},
    }

    promo_raw = datasets.get("promotion_coupon") if isinstance(datasets.get("promotion_coupon"), dict) else {}
    promo_data = promo_raw.get("data") if isinstance(promo_raw.get("data"), dict) else {}
    activities = promo_data.get("coupon_activities")
    promotion = {
        "available": promo_raw.get("status") == "success",
        "status": promo_raw.get("status"),
        "activities": activities if isinstance(activities, list) else [],
    }

    review_raw = (
        datasets.get("comment_manage_home")
        if isinstance(datasets.get("comment_manage_home"), dict)
        else {}
    )
    review_data = review_raw.get("data") if isinstance(review_raw.get("data"), dict) else {}
    reviews = {
        "available": review_raw.get("status") == "success",
        "status": review_raw.get("status"),
        "review_score": review_data.get("review_score"),
        "review_total": review_data.get("review_total"),
        "pending_reply_count": review_data.get("pending_reply_count"),
        "review_dimensions": review_data.get("review_dimensions") or {},
        "review_status": review_data.get("review_status") or {},
        "review_periods": review_data.get("review_periods") or {},
        "review_filters": review_data.get("review_filters") or {},
    }

    data_center_tabs: dict[str, Any] = {}
    for dataset_id, label in _MEITUAN_DATA_CENTER_TABS:
        raw = datasets.get(dataset_id) if isinstance(datasets.get(dataset_id), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        metrics = _summarize_meituan_data_center_metrics(dataset_id, data)
        data_center_tabs[dataset_id] = {
            "label": label,
            "available": raw.get("status") == "success",
            "status": raw.get("status"),
            "metrics": metrics if isinstance(metrics, dict) else {},
        }
    home_tab = data_center_tabs.get("data_center_home", {})
    data_center = {
        "available": any(tab.get("available") is True for tab in data_center_tabs.values()),
        "status": home_tab.get("status"),
        "basic_operation_metrics": home_tab.get("metrics") or {},
        "tabs": data_center_tabs,
    }
    return workbench, promotion, reviews, data_center


def _summarize_fliggy(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(snapshot)
    datasets = payload.get("datasets") if isinstance(payload.get("datasets"), dict) else {}

    def _ds(key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = datasets.get(key) if isinstance(datasets.get(key), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        return raw, data

    home_raw, home_data = _ds("home_v1")
    diagnose_raw, diagnose_data = _ds("diagnose_report")
    operating_raw, operating_data = _ds("operating_data")
    promo_raw, promo_data = _ds("promotion_agent")
    return {
        "fliggy_home": {
            "available": home_raw.get("status") == "success",
            "status": home_raw.get("status"),
            "hotel_name": home_data.get("hotel_name"),
            "today_data": home_data.get("today_data") or {},
            "yesterday_data": home_data.get("yesterday_data") or {},
        },
        "fliggy_diagnose": {
            "available": diagnose_raw.get("status") == "success",
            "status": diagnose_raw.get("status"),
            "trade_overview": diagnose_data.get("trade_overview") or {},
            "guest_review_score": diagnose_data.get("guest_review_score"),
            "review_extras": diagnose_data.get("review_extras") or {},
            "mci_index": diagnose_data.get("mci_index"),
        },
        "fliggy_operating": {
            "available": operating_raw.get("status") == "success",
            "status": operating_raw.get("status"),
            "in_house_data": operating_data.get("in_house_data") or {},
            "booking_data": operating_data.get("booking_data") or {},
            "flow_data": operating_data.get("flow_data") or {},
        },
        "fliggy_promotion": {
            "available": promo_raw.get("status") == "success",
            "status": promo_raw.get("status"),
            "joined_activities": promo_data.get("joined_activities")
            if isinstance(promo_data.get("joined_activities"), list)
            else [],
        },
    }


def _summarize_meituan_data_center_metrics(dataset_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if dataset_id == "data_center_trade":
        metrics = data.get("business_performance") if isinstance(data.get("business_performance"), dict) else {}
        if metrics:
            return metrics
        fallback = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        if fallback:
            return fallback
        fallback = data.get("basic_operation_metrics") if isinstance(data.get("basic_operation_metrics"), dict) else {}
        return fallback
    if dataset_id == "data_center_traffic":
        metrics: dict[str, Any] = {}
        for key in ("traffic_trend", "loss_analysis", "metrics"):
            section = data.get(key)
            if isinstance(section, dict):
                metrics.update(section)
        return metrics
    if dataset_id == "data_center_service":
        metrics = data.get("service_metrics") if isinstance(data.get("service_metrics"), dict) else {}
        if metrics:
            return metrics
        fallback = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        if fallback:
            return fallback
        fallback = data.get("basic_operation_metrics") if isinstance(data.get("basic_operation_metrics"), dict) else {}
        return fallback
    metrics = data.get("basic_operation_metrics") if isinstance(data.get("basic_operation_metrics"), dict) else {}
    if metrics:
        return metrics
    fallback = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    return fallback


def _summary_text(status: str, analysis_mode: str, available: list[str]) -> str:
    if status == "success":
        if analysis_mode == "joint":
            if "fliggy" in available:
                return "携程、美团与飞猪 EBK 最新 snapshot 已汇总，可用于联合分析汇报。"
            return "携程与美团 EBK 最新 snapshot 已汇总，可用于联合分析汇报。"
        if analysis_mode == "ctrip":
            return "携程 EBK 最新 snapshot 已汇总。"
        if analysis_mode == "fliggy":
            return "飞猪 EBK 最新 snapshot 已汇总。"
        return "美团 EBK 最新 snapshot 已汇总。"
    if status == "partial":
        return f"部分平台可用（{', '.join(available) or '无'}），请查看 missing_data。"
    return "未获取到可用的 EBK snapshot。"


_PLATFORM_LABELS = {"ctrip": "携程", "meituan": "美团", "fliggy": "飞猪"}


def _parse_metric_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text or text in {"—", "-", "暂无", "无"}:
        return None
    wan = re.search(r"(-?\d+(?:\.\d+)?)\s*万", text)
    if wan:
        return float(wan.group(1)) * 10000
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def _metric_display(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("display_value", "value"):
            if value.get(key) is not None and str(value.get(key)).strip():
                return str(value.get(key)).strip()
        return None
    text = str(value).strip()
    return text or None


def _first_metric(mapping: Any, *keys: str) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            display = _metric_display(mapping.get(key))
            if display:
                return display
    for key, raw in mapping.items():
        key_text = str(key)
        if any(token in key_text for token in keys):
            display = _metric_display(raw)
            if display:
                return display
    return None


def _monitor_list_to_map(items: Any) -> dict[str, Any]:
    if isinstance(items, dict):
        return items
    if not isinstance(items, list):
        return {}
    mapped: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("key") or "").strip()
        display = _metric_display(item)
        if label and display:
            mapped[label] = display
    return mapped


def _collect_yesterday_sales(operator: dict[str, Any]) -> dict[str, str]:
    sales: dict[str, str] = {}
    meituan = operator.get("meituan_workbench") if isinstance(operator.get("meituan_workbench"), dict) else {}
    meituan_y = meituan.get("yesterday_data") if isinstance(meituan.get("yesterday_data"), dict) else {}
    meituan_sales = _first_metric(meituan_y, "销售额", "销售金额")
    if meituan_sales:
        sales["meituan"] = meituan_sales

    fliggy = operator.get("fliggy_home") if isinstance(operator.get("fliggy_home"), dict) else {}
    fliggy_y = fliggy.get("yesterday_data") if isinstance(fliggy.get("yesterday_data"), dict) else {}
    fliggy_sales = _first_metric(fliggy_y, "销售额", "销售金额")
    if fliggy_sales:
        sales["fliggy"] = fliggy_sales

    ctrip = operator.get("ctrip_ebk") if isinstance(operator.get("ctrip_ebk"), dict) else {}
    monitor = ctrip.get("monitor") if isinstance(ctrip.get("monitor"), dict) else {}
    revenue = monitor.get("revenue_overview") if isinstance(monitor.get("revenue_overview"), dict) else {}
    ctrip_y = _monitor_list_to_map(revenue.get("yesterday"))
    ctrip_sales = _first_metric(ctrip_y, "昨日离店销售额", "离店销售额", "销售额")
    if not ctrip_sales:
        datasets = ctrip.get("datasets") if isinstance(ctrip.get("datasets"), dict) else {}
        outline = datasets.get("business_outline") if isinstance(datasets.get("business_outline"), dict) else {}
        metrics = outline.get("metrics") if isinstance(outline.get("metrics"), dict) else {}
        ctrip_sales = _first_metric(metrics, "departure_sales_amount", "昨日离店销售额", "销售额")
    if ctrip_sales:
        sales["ctrip"] = ctrip_sales
    return sales


def _build_joint_insights(
    operator: dict[str, Any],
    *,
    analysis_mode: str,
    available: list[str],
    status: str,
    missing_data: list[str],
) -> dict[str, Any]:
    labels = [_PLATFORM_LABELS.get(key, key) for key in available]
    findings: list[dict[str, str]] = []
    actions: list[dict[str, str]] = []
    sales = _collect_yesterday_sales(operator)

    ranked: list[tuple[str, str, float]] = []
    for platform, display in sales.items():
        number = _parse_metric_number(display)
        if number is None:
            continue
        ranked.append((platform, display, number))
    ranked.sort(key=lambda item: item[2], reverse=True)

    if ranked:
        top_platform, top_display, _ = ranked[0]
        top_label = _PLATFORM_LABELS.get(top_platform, top_platform)
        findings.append(
            {
                "text": f"各平台页面昨日金额字段中，{top_label}较高：{top_display}",
                "evidence_refs": [f"ev_{top_platform}_sales"],
            }
        )
        if len(ranked) >= 2:
            second_platform, second_display, _ = ranked[1]
            second_label = _PLATFORM_LABELS.get(second_platform, second_platform)
            findings.append(
                {
                    "text": f"另一个可比页面金额：{second_label} {second_display}",
                    "evidence_refs": [f"ev_{second_platform}_sales"],
                }
            )

    meituan = operator.get("meituan_workbench") if isinstance(operator.get("meituan_workbench"), dict) else {}
    meituan_y = meituan.get("yesterday_data") if isinstance(meituan.get("yesterday_data"), dict) else {}
    meituan_orders = _first_metric(meituan_y, "支付订单数", "订单数")
    if meituan_orders:
        findings.append(
            {
                "text": f"美团昨日支付订单 {meituan_orders}",
                "evidence_refs": ["ev_meituan_orders"],
            }
        )

    fliggy = operator.get("fliggy_home") if isinstance(operator.get("fliggy_home"), dict) else {}
    fliggy_y = fliggy.get("yesterday_data") if isinstance(fliggy.get("yesterday_data"), dict) else {}
    fliggy_orders = _first_metric(fliggy_y, "预订订单量", "预订订单", "订单量")
    if fliggy_orders:
        findings.append(
            {
                "text": f"飞猪昨日预订订单 {fliggy_orders}",
                "evidence_refs": ["ev_fliggy_orders"],
            }
        )

    for item in missing_data[:2]:
        findings.append({"text": str(item), "evidence_refs": ["ev_missing"]})

    if not findings and available:
        findings.append(
            {
                "text": (
                    f"已取得{'、'.join(labels)}快照，"
                    "但缺少可直接比较的销售额或订单字段。"
                ),
                "evidence_refs": ["ev_platform_status"],
            }
        )

    if missing_data:
        actions.append({"text": "优先补读缺失平台 EBK，再做完整三端对比"})
    if ranked:
        weak = ranked[-1]
        if len(ranked) >= 2 and weak[2] < ranked[0][2] * 0.5:
            weak_label = _PLATFORM_LABELS.get(weak[0], weak[0])
            actions.append({"text": f"关注{weak_label}流量与转化，核对活动与定价是否拖累销售额"})
    if not actions:
        actions.append({"text": "只读分析；如需展开明细可说「查看三端明细」"})

    if status == "missing":
        conclusion = "未获取到可用的 EBK snapshot，暂无法形成三端经营结论。"
    elif ranked:
        top_label = _PLATFORM_LABELS.get(ranked[0][0], ranked[0][0])
        platform_text = "、".join(labels) if labels else "已读平台"
        if status == "partial":
            conclusion = (
                f"{platform_text}已形成初步对比：各平台页面昨日金额字段中以{top_label}较高"
                f"（{ranked[0][1]}）；仍有平台数据缺失，结论仅供参考。"
            )
        else:
            conclusion = (
                f"{platform_text}昨日经营已汇总：各平台页面金额字段中以{top_label}较高"
                f"（{ranked[0][1]}），建议结合预订与流量一起看转化。"
            )
    elif labels:
        conclusion = f"{'、'.join(labels)}数据已就绪，但关键销售额字段不足，请先看下方分端要点。"
    else:
        conclusion = _summary_text(status, analysis_mode, available)

    return {
        "conclusion": conclusion,
        "findings": findings[:5],
        "actions": actions[:3],
    }
