from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.domain.platform_ebk_watch.push_policy import (
    DEFAULT_FAILURE_ALERT_COOLDOWN_MINUTES,
    failure_alert_cooldown_minutes,
    platform_throttle_key,
    record_failure_alert,
    reset_failure_alert_state,
    should_send_failure_alert,
)


class PlatformThrottleKeyTests(unittest.TestCase):
    """节流 key 必须带平台前缀，避免与携程组 key 或跨平台同名组互串冷却。"""

    def test_key_contains_platform_prefix_and_group(self) -> None:
        self.assertEqual(
            platform_throttle_key("meituan", "store_a"),
            "platform_ebk:meituan:store_a",
        )
        self.assertEqual(
            platform_throttle_key("fliggy", "store_a"),
            "platform_ebk:fliggy:store_a",
        )

    def test_same_group_key_across_platforms_does_not_collide(self) -> None:
        self.assertNotEqual(
            platform_throttle_key("meituan", "store_a"),
            platform_throttle_key("fliggy", "store_a"),
        )

    def test_key_never_collides_with_bare_ctrip_group_key(self) -> None:
        # 携程组 key 形如 "store_a"（模式 ^[a-z0-9][a-z0-9_-]{0,63}$，不含冒号），
        # 平台前缀 key 不可能与裸携程 key 相同。
        self.assertNotEqual(platform_throttle_key("meituan", "store_a"), "store_a")

    def test_empty_inputs_return_empty_key(self) -> None:
        self.assertEqual(platform_throttle_key("", "store_a"), "")
        self.assertEqual(platform_throttle_key("meituan", ""), "")


class FailureAlertCooldownTests(unittest.TestCase):
    """失败态不推完整卡，只按冷却窗口发异常提醒（P0：登录失效推送风暴）。"""

    def setUp(self) -> None:
        reset_failure_alert_state()

    def tearDown(self) -> None:
        reset_failure_alert_state()

    def test_first_failure_alert_is_allowed(self) -> None:
        allowed, reason = should_send_failure_alert("platform_ebk:meituan:store_a")
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_alert_suppressed_inside_cooldown_window(self) -> None:
        key = "platform_ebk:meituan:store_a"
        now = datetime.now(tz=timezone.utc)
        record_failure_alert(key, at=now)
        allowed, reason = should_send_failure_alert(key, now=now + timedelta(minutes=30))
        self.assertFalse(allowed)
        self.assertIn("冷却", reason)

    def test_alert_allowed_again_after_cooldown(self) -> None:
        key = "platform_ebk:fliggy:store_a"
        now = datetime.now(tz=timezone.utc)
        record_failure_alert(key, at=now)
        allowed, reason = should_send_failure_alert(
            key,
            now=now + timedelta(minutes=DEFAULT_FAILURE_ALERT_COOLDOWN_MINUTES + 1),
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_cooldown_state_is_per_key(self) -> None:
        record_failure_alert("platform_ebk:meituan:store_a")
        allowed, _ = should_send_failure_alert("platform_ebk:fliggy:store_a")
        self.assertTrue(allowed)

    def test_record_failure_alert_updates_last_alert_time(self) -> None:
        key = "platform_ebk:meituan:store_a"
        first = datetime.now(tz=timezone.utc)
        record_failure_alert(key, at=first)
        record_failure_alert(key, at=first + timedelta(minutes=5))
        allowed, _ = should_send_failure_alert(key, now=first + timedelta(minutes=30))
        # 以最近一次记录（first+5min）起算，30min 时仍在 60min 冷却内。
        self.assertFalse(allowed)

    def test_cooldown_minutes_from_settings_default(self) -> None:
        self.assertGreater(failure_alert_cooldown_minutes(), 0)


if __name__ == "__main__":
    unittest.main()
