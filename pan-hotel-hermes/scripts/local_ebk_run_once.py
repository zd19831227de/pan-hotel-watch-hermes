import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("WATCH_API_BASE_URL", "http://127.0.0.1:8010")

from client.watch_api import WatchApiClient


async def main() -> None:
    client = WatchApiClient()
    health = await client.get_json("/api/v1/health")
    print("health", health)
    result = await client.run_ebk_watch_once("ctrip_ebooking_data_center")
    print("status", result.get("status"))
    print("summary", result.get("summary"))
    print("operator_reply_note", result.get("operator_reply_note"))


if __name__ == "__main__":
    asyncio.run(main())
