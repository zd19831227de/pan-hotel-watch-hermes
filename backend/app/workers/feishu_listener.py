from __future__ import annotations

from app.core.asyncio_runtime import configure_windows_subprocess_event_loop_policy
from app.core.settings import get_settings
from app.domain.notifications.feishu_listener import FeishuLongConnectionListener


def main() -> None:
    configure_windows_subprocess_event_loop_policy()
    settings = get_settings()
    print(f"{settings.app_name} Feishu long-connection listener")
    print(f"environment={settings.environment}")
    mode = (getattr(settings, "feishu_agent_mode", "hermes") or "hermes").strip().lower()
    if mode == "hermes":
        print("feishu_agent_mode=hermes：飞书入口已由 Hermes Gateway 接管。")
        print("内置 feishu-listener 不再启动，避免与 Hermes 同时监听同一飞书应用。")
        print("如需使用后端内置大模型回复，请在 .env 设置 FEISHU_AGENT_MODE=builtin。")
        return
    print(f"app_id_configured={bool(settings.feishu_app_id)}")
    print("waiting for Feishu @bot messages and pricing approval card actions...")
    FeishuLongConnectionListener().start()


if __name__ == "__main__":
    main()
