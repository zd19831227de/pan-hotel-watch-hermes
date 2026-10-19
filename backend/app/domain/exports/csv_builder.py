"""通用 CSV 构造器（UI-Fix-F2AC）。

- UTF-8 带 BOM（utf-8-sig）：Excel 双击直开中文不乱码
- RFC 4180 转义：逗号/引号/换行（由标准库 csv writer 保证）
"""

from __future__ import annotations

import csv
import io
from typing import Any


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_csv_bytes(
    *,
    headers: list[str],
    rows: list[list[Any]] | list[dict[str, Any]],
) -> bytes:
    """构造 utf-8-sig 编码的 CSV 字节流。

    ``rows`` 支持两种形态：
    - ``list[list[Any]]``：与 ``headers`` 顺序对齐的行
    - ``list[dict[str, Any]]``：按 ``headers`` 键名取值（缺失键输出空串）
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([_stringify(header) for header in headers])
    for row in rows:
        if isinstance(row, dict):
            writer.writerow([_stringify(row.get(header)) for header in headers])
        else:
            writer.writerow([_stringify(cell) for cell in row])
    return buffer.getvalue().encode("utf-8-sig")
