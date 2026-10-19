from __future__ import annotations

import json
import os
from typing import Any

import httpx

from router.feishu_forward import route_target, should_forward_to_watch


class FeishuEventForwarder:
    def __init__(
        self,
        *,
        watch_base_url: str | None = None,
        feishu_events_path: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        base = watch_base_url or os.environ.get("WATCH_API_BASE_URL", "http://127.0.0.1:8000")
        self.watch_base_url = base.rstrip("/")
        path = feishu_events_path or os.environ.get(
            "WATCH_FEISHU_EVENTS_PATH",
            "/api/v1/feishu/events",
        )
        self.feishu_events_url = f"{self.watch_base_url}{path}"
        self.timeout = timeout

    async def forward(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        service_token = os.environ.get("WATCH_API_SHARED_SECRET", "").strip()
        if service_token:
            headers["X-Pan-Hotel-Service-Token"] = service_token
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.feishu_events_url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else {"data": body}

    async def route(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = route_target(payload)
        if target == "watch":
            result = await self.forward(payload)
            return {"route": "watch", "forwarded": True, "result": result}
        return {
            "route": "hermes",
            "forwarded": False,
            "summary": "事件交由 Hermes Agent 处理。",
        }


def classify_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "route": route_target(payload),
        "forward_to_watch": should_forward_to_watch(payload),
        "event_type": payload.get("header", {}).get("event_type") if isinstance(payload.get("header"), dict) else None,
        "payload_preview": json.dumps(payload, ensure_ascii=False)[:500],
    }
