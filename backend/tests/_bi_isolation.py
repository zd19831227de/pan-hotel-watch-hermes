"""bi_daily 测试共享隔离助手。

与 tests/_db_isolation.isolated_test_db 的差别仅在于：aiosqlite 在语句级
错误（如唯一约束 IntegrityError）后走连接失效路径，Windows 下会把临时库
句柄留到进程退出，TemporaryDirectory 清理因此抛 PermissionError。该异常
发生在断言之后、不影响测试结论，吞掉即可（%TEMP% 残留文件无害）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from backend.tests._db_isolation import isolated_test_db


@contextlib.contextmanager
def tolerant_isolated_db() -> Iterator[None]:
    try:
        with isolated_test_db():
            yield
    except PermissionError:
        pass
