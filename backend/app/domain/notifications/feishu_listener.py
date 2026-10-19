from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from app.core.settings import get_settings
from app.domain.notifications.feishu_bot import handle_event
from app.domain.ops.worker_heartbeat import write_worker_heartbeat

logger = logging.getLogger(__name__)

WORKER_NAME = "feishu_listener"
_HEARTBEAT_INTERVAL_SECONDS = 60
_PayloadHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class FeishuLongConnectionListener:
    def __init__(self, payload_handler: _PayloadHandler | None = None) -> None:
        self._payload_handler = payload_handler or handle_event
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feishu-bot")
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_loop_thread: threading.Thread | None = None
        self._async_loop_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            import lark_oapi as lark
        except ImportError as exc:
            raise RuntimeError("lark-oapi is not installed. Run scripts/install.ps1 first.") from exc

        settings = get_settings()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required.")

        event_handler = (
            lark.EventDispatcherHandler.builder(
                settings.feishu_encrypt_key,
                settings.feishu_verification_token,
            )
            .register_p2_im_message_receive_v1(self._handle_im_message_receive_v1)
            .register_p2_card_action_trigger(self._handle_card_action_trigger)
            .build()
        )
        client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=event_handler,
        )
        _record_listener_heartbeat("started")
        self._start_heartbeat_thread()
        client.start()

    def _handle_im_message_receive_v1(self, data: Any) -> None:
        payload = _sdk_event_to_payload(data)
        message = payload.get("event", {}).get("message", {})
        _record_listener_heartbeat(
            "event_received",
            tick_summary={"last_event_type": payload.get("header", {}).get("event_type")},
        )
        print(
            "received im.message.receive_v1 "
            f"event_id={payload.get('header', {}).get('event_id')} "
            f"message_id={message.get('message_id')} "
            f"chat_id={message.get('chat_id')}",
            flush=True,
        )
        self._executor.submit(self._process_payload, payload)
        print(
            "queued feishu event "
            f"event_id={payload.get('header', {}).get('event_id')} "
            f"queued_at={datetime.now(UTC).isoformat()}",
            flush=True,
        )

    def _handle_card_action_trigger(self, data: Any) -> Any:
        payload = _sdk_event_to_payload(data)
        _record_listener_heartbeat(
            "event_received",
            tick_summary={"last_event_type": payload.get("header", {}).get("event_type")},
        )
        print(
            "received card.action.trigger "
            f"event_id={payload.get('header', {}).get('event_id')}",
            flush=True,
        )
        if not _card_action_needs_sync_response(payload):
            self._executor.submit(self._process_payload, payload)
            return _p2_card_action_trigger_response(
                {"toast": {"type": "info", "content": "已收到，正在处理。"}}
            )
        try:
            result = self._run_async_handler(payload)
        except Exception as exc:
            _record_listener_heartbeat(
                "event_failed",
                tick_summary={"last_event_type": payload.get("header", {}).get("event_type")},
                error=exc,
            )
            print(
                "failed to handle card.action.trigger "
                f"event_id={payload.get('header', {}).get('event_id')} "
                f"error_type={type(exc).__name__} "
                f"error={str(exc)[:300]}",
                flush=True,
            )
            return _p2_card_action_trigger_response(
                {
                    "toast": {
                        "type": "error",
                        "content": "卡片操作处理失败，请稍后重试或重新发送调价指令。",
                    }
                }
            )
        self._log_handled_payload(payload, result)
        callback_response = result.get("card_callback_response")
        if isinstance(callback_response, dict):
            return _p2_card_action_trigger_response(callback_response)
        return _p2_card_action_trigger_response(
            {"toast": {"type": "success", "content": result.get("summary") or "已收到"}}
        )

    def _process_payload(self, payload: dict[str, Any]) -> None:
        try:
            result = self._run_async_handler(payload)
        except Exception as exc:
            _record_listener_heartbeat(
                "event_failed",
                tick_summary={"last_event_type": payload.get("header", {}).get("event_type")},
                error=exc,
            )
            print(
                "failed to handle feishu event "
                f"event_id={payload.get('header', {}).get('event_id')} "
                f"error_type={type(exc).__name__} "
                f"error={str(exc)[:300]}",
                flush=True,
            )
            return
        self._log_handled_payload(payload, result)

    def _log_handled_payload(
        self,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        _record_listener_heartbeat(
            "event_handled",
            tick_summary={
                "last_event_type": payload.get("header", {}).get("event_type"),
                "last_handler_status": result.get("status"),
                "last_reply_status": (result.get("feishu_push") or {}).get("status"),
            },
        )
        print(
            "handled feishu event "
            f"status={result.get('status')} "
            f"execute_triggered={evidence.get('execute_triggered')} "
            f"reply_status={(result.get('feishu_push') or {}).get('status')} "
            f"summary={result.get('summary')}",
            flush=True,
        )

    def _run_async_handler(self, payload: dict[str, Any]) -> dict[str, Any]:
        loop = self._ensure_async_loop()
        future = asyncio.run_coroutine_threadsafe(self._payload_handler(payload), loop)
        return future.result()

    def _ensure_async_loop(self) -> asyncio.AbstractEventLoop:
        with self._async_loop_lock:
            if self._async_loop is not None and self._async_loop.is_running():
                return self._async_loop
            loop = asyncio.new_event_loop()
            self._async_loop = loop
            self._async_loop_thread = threading.Thread(
                target=self._run_async_loop_forever,
                args=(loop,),
                name="feishu-listener-async-loop",
                daemon=True,
            )
            self._async_loop_thread.start()
            return loop

    @staticmethod
    def _run_async_loop_forever(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _start_heartbeat_thread(self) -> None:
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="feishu-listener-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
            _record_listener_heartbeat("heartbeat")


def _sdk_event_to_payload(data: Any) -> dict[str, Any]:
    payload = _to_plain_data(data)
    if isinstance(payload, dict) and payload.get("schema") == "2.0":
        return payload

    header = payload.get("header", {}) if isinstance(payload, dict) else {}
    event = payload.get("event", {}) if isinstance(payload, dict) else {}
    event_type = _pick(header, "event_type", "eventType")
    if not event_type and isinstance(event, dict) and "action" in event:
        event_type = "card.action.trigger"
    return {
        "schema": "2.0",
        "header": {
            "event_id": _pick(header, "event_id", "eventId"),
            "event_type": event_type or "im.message.receive_v1",
            "app_id": _pick(header, "app_id", "appId"),
            "tenant_key": _pick(header, "tenant_key", "tenantKey"),
        },
        "event": event,
    }


def _to_plain_data(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _to_plain_data(value.model_dump(mode="json"))
    if hasattr(value, "to_dict"):
        return _to_plain_data(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _to_plain_data(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    try:
        return json.loads(json.dumps(value, default=lambda item: vars(item)))
    except (TypeError, ValueError):
        return str(value)


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _card_action_needs_sync_response(payload: dict[str, Any]) -> bool:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value = action.get("value") if isinstance(action.get("value"), dict) else {}
    workflow = str(value.get("workflow") or "")
    action_name = str(value.get("action") or "")
    return workflow == "pricing_custom_price" or (
        workflow == "pricing_approval" and action_name == "custom_price"
    )


def _p2_card_action_trigger_response(payload: dict[str, Any]) -> Any:
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
    except ImportError:
        return payload
    return P2CardActionTriggerResponse(payload)


def _record_listener_heartbeat(
    event: str,
    *,
    tick_summary: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    try:
        write_worker_heartbeat(
            WORKER_NAME,
            event,
            tick_summary=tick_summary or {},
            error=error,
        )
    except Exception:
        logger.warning("failed to write Feishu listener heartbeat", exc_info=True)
