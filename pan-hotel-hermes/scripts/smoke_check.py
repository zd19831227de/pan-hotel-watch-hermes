from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8100"
CHAT = "oc_22222222222222222222222222222222"


def fetch(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    errors: list[str] = []

    health = fetch("/api/v1/ops/runtime-health")
    if health.get("summary", {}).get("status") != "fresh":
        errors.append(f"runtime-health not fresh: {health.get('summary')}")

    ctx = fetch(f"/api/v1/ops/feishu-chat-context?chat_id={CHAT}")
    if ctx.get("binding_mode") != "multi_store":
        errors.append(f"expected multi_store, got {ctx.get('binding_mode')}")
    if len(ctx.get("competitor_groups") or []) < 2:
        errors.append("expected >=2 competitor groups bound to chat")

    jingli = fetch(
        f"/api/v1/ops/feishu-chat-context?chat_id={CHAT}&store_hint=%E6%86%AC%E9%BB%8E"
    )
    resolution = jingli.get("resolution") or {}
    if resolution.get("status") != "resolved":
        errors.append(f"jingli resolution failed: {resolution}")
    if jingli.get("ebk_watch_group_key") != "ctrip_ebooking_data_center":
        errors.append(f"unexpected ebk key: {jingli.get('ebk_watch_group_key')}")
    if jingli.get("competitor_watch_group_key") != "watch_1782109266413":
        errors.append(f"unexpected competitor key: {jingli.get('competitor_watch_group_key')}")

    ebk = fetch("/api/v1/ctrip-ebooking/data-center-watch-groups/ctrip_ebooking_data_center/latest")
    latest = ebk.get("run") or ebk.get("latest_run") or {}
    if latest.get("status") != "success":
        errors.append(f"ebk latest run not success: {latest.get('status')}")

    print("SMOKE OK")
    print(f"  binding_mode={ctx.get('binding_mode')} competitor_groups={len(ctx.get('competitor_groups') or [])}")
    print(f"  jingli resolved ebk={jingli.get('ebk_watch_group_key')} competitor={jingli.get('competitor_watch_group_key')}")
    print(f"  ebk_latest={latest.get('status')} captured_at={latest.get('captured_at')}")

    if errors:
        print("SMOKE FAIL", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
