"""Shared test-package bootstrap.

2026-09-17 事故复盘：全量 unittest 从仓库根目录启动时，未隔离 browser-state 的
测试用例会把生产 .runtime/browser-state/browser_sessions.json 覆写（账号注册表
被清空、留下测试假会话）。默认把 BROWSER_STATE_DIR 指到进程级临时目录兜底；
测试内显式设置的环境变量仍然优先。
"""

import os
import tempfile

_TEST_BROWSER_STATE_PARENT = tempfile.mkdtemp(prefix="pan-hotel-tests-browser-state-")
os.environ.setdefault("BROWSER_STATE_DIR", os.path.join(_TEST_BROWSER_STATE_PARENT, "browser-state"))
