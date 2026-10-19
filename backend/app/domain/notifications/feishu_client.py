from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.domain.notifications.feishu_app_credentials import (
    FeishuAppCredentials,
    resolve_feishu_app_credentials,
)
from app.domain.pricing_safety import strip_sensitive


class FeishuBotClient:
    def __init__(
        self,
        *,
        chat_id: str | None = None,
        app_profile: str | None = None,
    ) -> None:
        self.chat_id = str(chat_id or "").strip() or None
        self.app_profile = str(app_profile or "").strip() or None

    def _credentials(self, *, chat_id: str | None = None) -> FeishuAppCredentials:
        return resolve_feishu_app_credentials(
            chat_id=self.chat_id or (str(chat_id or "").strip() or None),
            app_profile=self.app_profile,
        )

    def _scoped_for_chat(self, chat_id: str) -> FeishuBotClient:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id or self.chat_id == normalized_chat_id:
            return self
        return FeishuBotClient(chat_id=normalized_chat_id, app_profile=self.app_profile)

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        return await self._reply_message(
            message_id,
            msg_type="text",
            content={"text": text},
        )

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        client = self._scoped_for_chat(chat_id)
        return await client._send_message(
            receive_id_type="chat_id",
            receive_id=chat_id,
            msg_type="text",
            content={"text": text},
        )

    async def reply_interactive_card(
        self,
        message_id: str,
        card: dict[str, Any],
        *,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        prepared = await resolve_feishu_card_images(card, client=self)
        result = await self._reply_message(
            message_id,
            msg_type="interactive",
            content=prepared,
        )
        if result["status"] == "success" or not fallback_text:
            return result
        fallback = await self.reply_text(message_id, fallback_text)
        fallback.setdefault("evidence", {})["card_reply"] = strip_sensitive(result)
        fallback.setdefault("data", {})["fallback_from"] = "interactive"
        return fallback

    async def send_interactive_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        *,
        fallback_text: str | None = None,
    ) -> dict[str, Any]:
        client = self._scoped_for_chat(chat_id)
        prepared = await resolve_feishu_card_images(card, client=client)
        result = await client._send_message(
            receive_id_type="chat_id",
            receive_id=chat_id,
            msg_type="interactive",
            content=prepared,
        )
        if result["status"] == "success" or not fallback_text:
            return result
        fallback = await client.send_text(chat_id, fallback_text)
        fallback.setdefault("evidence", {})["card_send"] = strip_sensitive(result)
        fallback.setdefault("data", {})["fallback_from"] = "interactive"
        return fallback

    async def _reply_message(
        self,
        message_id: str,
        *,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        credentials = self._credentials()
        if not credentials.configured():
            return _config_required(credentials.public_payload())
        token = await self._tenant_access_token(credentials)
        if token["status"] != "success":
            return token
        payload = {
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                    headers={"Authorization": f"Bearer {token['tenant_access_token']}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": "Feishu message reply failed.",
                "evidence": {"error": str(exc), "message_id_present": bool(message_id)},
                "next_action": "Check Feishu app permissions and network access.",
            }
        return _message_result(response, msg_type=msg_type, delivery="reply")

    async def _send_message(
        self,
        *,
        receive_id_type: str,
        receive_id: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        fallback_chat_id = receive_id if receive_id_type == "chat_id" else None
        credentials = self._credentials(chat_id=fallback_chat_id)
        if not credentials.configured():
            return _config_required(credentials.public_payload())
        token = await self._tenant_access_token(credentials)
        if token["status"] != "success":
            return token
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages"
                    f"?receive_id_type={receive_id_type}",
                    headers={"Authorization": f"Bearer {token['tenant_access_token']}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": "Feishu message send failed.",
                "evidence": {"error": str(exc), "receive_id_type": receive_id_type},
                "next_action": "Check Feishu app permissions and network access.",
            }
        return _message_result(response, msg_type=msg_type, delivery="send")

    async def upload_image(self, image_bytes: bytes, *, image_type: str = "message") -> dict[str, Any]:
        credentials = self._credentials()
        if not credentials.configured():
            return _config_required(credentials.public_payload())
        token = await self._tenant_access_token(credentials)
        if token["status"] != "success":
            return token
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/images",
                    headers={"Authorization": f"Bearer {token['tenant_access_token']}"},
                    data={"image_type": image_type},
                    files={"image": ("chart.png", image_bytes, "image/png")},
                )
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": "Feishu image upload failed.",
                "evidence": {"error": str(exc)},
            }
        payload = _safe_json(response)
        img_key = payload.get("data", {}).get("image_key") if isinstance(payload.get("data"), dict) else None
        code = payload.get("code") if isinstance(payload, dict) else None
        is_success = response.is_success and code in {None, 0} and img_key
        return {
            "status": "success" if is_success else "failed",
            "summary": "Feishu image uploaded." if is_success else "Feishu image upload failed.",
            "img_key": img_key,
            "data": {"http_status": response.status_code, "feishu_code": code},
            "evidence": {"response_preview": response.text[:300]},
        }

    async def upload_file(self, file_bytes: bytes, *, filename: str) -> dict[str, Any]:
        """上传普通文件（im/v1/files, file_type=stream），返回 file_key。"""
        credentials = self._credentials()
        if not credentials.configured():
            return _config_required(credentials.public_payload())
        token = await self._tenant_access_token(credentials)
        if token["status"] != "success":
            return token
        safe_name = str(filename or "document.bin").strip() or "document.bin"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/files",
                    headers={"Authorization": f"Bearer {token['tenant_access_token']}"},
                    data={"file_type": "stream", "file_name": safe_name},
                    files={"file": (safe_name, file_bytes, "application/octet-stream")},
                )
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": "Feishu file upload failed.",
                "evidence": {"error": str(exc)},
            }
        payload = _safe_json(response)
        file_key = payload.get("data", {}).get("file_key") if isinstance(payload.get("data"), dict) else None
        code = payload.get("code") if isinstance(payload, dict) else None
        is_success = response.is_success and code in {None, 0} and file_key
        return {
            "status": "success" if is_success else "failed",
            "summary": "Feishu file uploaded." if is_success else "Feishu file upload failed.",
            "file_key": file_key,
            "data": {"http_status": response.status_code, "feishu_code": code},
            "evidence": {"response_preview": response.text[:300]},
        }

    async def send_file(self, *, chat_id: str, file_key: str) -> dict[str, Any]:
        """向群发送文件消息（msg_type=file）。"""
        return await self._send_message(
            receive_id_type="chat_id",
            receive_id=chat_id,
            msg_type="file",
            content={"file_key": file_key},
        )

    async def download_message_resource(
        self,
        *,
        message_id: str,
        file_key: str,
        resource_type: str = "file",
    ) -> dict[str, Any]:
        """下载消息里的资源（file/image），返回原始字节（群资料归档用）。"""
        credentials = self._credentials()
        if not credentials.configured():
            return _config_required(credentials.public_payload())
        token = await self._tenant_access_token(credentials)
        if token["status"] != "success":
            return {
                "status": "failed",
                "summary": "Feishu tenant_access_token unavailable.",
            }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(
                    f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
                    f"/resources/{file_key}",
                    params={"type": resource_type},
                    headers={"Authorization": f"Bearer {token['tenant_access_token']}"},
                )
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": "Feishu resource download failed.",
                "evidence": {"error": str(exc)},
            }
        if not response.is_success:
            return {
                "status": "failed",
                "summary": "Feishu resource download failed.",
                "evidence": {
                    "http_status": response.status_code,
                    "response_preview": response.text[:200],
                },
            }
        return {"status": "success", "data": response.content}

    async def _tenant_access_token(self, credentials: FeishuAppCredentials) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": credentials.app_id,
                        "app_secret": credentials.app_secret,
                    },
                )
        except httpx.HTTPError as exc:
            return {
                "status": "failed",
                "summary": "Feishu tenant_access_token request failed.",
                "evidence": {"error": str(exc)},
                "next_action": "Check FEISHU_APP_ID and FEISHU_APP_SECRET.",
            }
        payload = _safe_json(response)
        access_token = payload.get("tenant_access_token") if isinstance(payload, dict) else None
        if response.is_success and access_token:
            return {
                "status": "success",
                "summary": "Feishu tenant_access_token acquired.",
                "tenant_access_token": access_token,
            }
        return {
            "status": "failed",
            "summary": "Feishu tenant_access_token request failed.",
            "data": {"http_status": response.status_code},
            "evidence": {"response_preview": response.text[:300]},
            "next_action": "Check the Feishu app credentials and app status.",
        }


def _config_required(public_credentials: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {"feishu_app_configured": False}
    if public_credentials is not None:
        evidence["feishu_app_credentials"] = public_credentials
    return {
        "status": "config_required",
        "summary": "Feishu app_id/app_secret is not configured for this chat.",
        "evidence": evidence,
        "next_action": "Configure the Feishu app credentials for the current chat_id in .env.",
    }


def _message_result(response: httpx.Response, *, msg_type: str, delivery: str) -> dict[str, Any]:
    payload = _safe_json(response)
    code = payload.get("code") if isinstance(payload, dict) else None
    is_success = response.is_success and code in {None, 0}
    return {
        "status": "success" if is_success else "failed",
        "summary": "Feishu message sent." if is_success else "Feishu message send failed.",
        "data": {
            "http_status": response.status_code,
            "msg_type": msg_type,
            "delivery": delivery,
            "feishu_code": code,
        },
        "evidence": {"response_preview": response.text[:300]},
        "next_action": None if is_success else "Check Feishu API response and app permissions.",
    }


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def resolve_feishu_card_images(
    card: dict[str, Any],
    *,
    client: FeishuBotClient | None = None,
) -> dict[str, Any]:
    """Upload inline card images and inject Feishu img elements."""
    uploads = card.get("_feishu_image_uploads")
    fallback_elements = card.get("_feishu_fallback_elements")
    cleaned = {key: value for key, value in card.items() if not str(key).startswith("_feishu_")}
    if not isinstance(uploads, list) or not uploads:
        return cleaned

    elements = list(cleaned.get("elements") or [])
    image_client = client or FeishuBotClient()
    inserted = 0
    for item in uploads:
        if not isinstance(item, dict):
            continue
        raw_base64 = str(item.get("data_base64") or "").strip()
        if not raw_base64:
            continue
        try:
            image_bytes = base64.b64decode(raw_base64)
        except (ValueError, TypeError):
            continue
        upload = await image_client.upload_image(image_bytes)
        img_key = upload.get("img_key")
        if not img_key:
            continue
        alt_text = str(item.get("alt") or "image").strip() or "image"
        position = item.get("position")
        insert_at = int(position) if isinstance(position, int) else len(elements)
        insert_at = max(0, min(insert_at, len(elements)))
        elements.insert(
            insert_at,
            {
                "tag": "img",
                "img_key": img_key,
                "alt": {"tag": "plain_text", "content": alt_text[:80]},
                "mode": "fit_horizontal",
                "preview": True,
            },
        )
        inserted += 1

    if inserted == 0 and isinstance(fallback_elements, list):
        elements.extend(fallback_elements)
    cleaned["elements"] = elements
    return cleaned
