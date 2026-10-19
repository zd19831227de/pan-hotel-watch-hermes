from __future__ import annotations

import asyncio
import importlib
import unittest
from typing import Any
from unittest import mock


def _reload_module():
    return importlib.reload(importlib.import_module("app.api.v1.feishu"))


class FeishuEventIdempotencyTests(unittest.TestCase):
    def test_duplicate_event_id_is_handled_once(self) -> None:
        module = _reload_module()
        calls: list[dict[str, Any]] = []

        async def fake_handle_event(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return {"status": "ok", "summary": "handled"}

        with mock.patch.object(module, "handle_event", new=fake_handle_event):
            payload = {
                "schema": "2.0",
                "header": {"event_id": "evt-dup-1", "event_type": "card.action.trigger"},
                "event": {},
            }
            first = asyncio.run(module.handle_feishu_event(_FakeRequest(payload)))
            second = asyncio.run(module.handle_feishu_event(_FakeRequest(payload)))
        self.assertEqual(first, {"status": "ok", "summary": "handled"})
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 1)

    def test_different_event_ids_are_both_handled(self) -> None:
        module = _reload_module()
        calls: list[dict[str, Any]] = []

        async def fake_handle_event(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return {"status": "ok"}

        with mock.patch.object(module, "handle_event", new=fake_handle_event):
            for event_id in ("evt-a", "evt-b"):
                asyncio.run(
                    module.handle_feishu_event(
                        _FakeRequest(
                            {
                                "schema": "2.0",
                                "header": {"event_id": event_id},
                                "event": {},
                            }
                        )
                    )
                )
        self.assertEqual(len(calls), 2)

    def test_card_callback_response_is_cached_verbatim(self) -> None:
        module = _reload_module()

        async def fake_handle_event(payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "card_callback_response": {"toast": {"type": "success", "content": "已通过"}},
            }

        with mock.patch.object(module, "handle_event", new=fake_handle_event):
            payload = {"header": {"event_id": "evt-card-1"}, "event": {}}
            first = asyncio.run(module.handle_feishu_event(_FakeRequest(payload)))
            second = asyncio.run(module.handle_feishu_event(_FakeRequest(payload)))
        self.assertEqual(
            first, {"toast": {"type": "success", "content": "已通过"}}
        )
        self.assertEqual(second, first)

    def test_payload_without_event_id_passes_through(self) -> None:
        module = _reload_module()
        calls: list[dict[str, Any]] = []

        async def fake_handle_event(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return {"status": "ok"}

        with mock.patch.object(module, "handle_event", new=fake_handle_event):
            asyncio.run(module.handle_feishu_event(_FakeRequest({"event": {}})))
            asyncio.run(module.handle_feishu_event(_FakeRequest({"event": {}})))
        self.assertEqual(len(calls), 2)


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


if __name__ == "__main__":
    unittest.main()
