"""预算 / demand forecast 导入解析：CSV 与 XLSX → 校验后的行结构。

只做解析与校验，不落库（落库与版本/审计在 service 层）。文件必须由
operator 通过受保护端点上传；本模块不接触任何路径之外的宿主信息。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

MAX_ERRORS_KEPT = 20

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "stay_date": ("入住日期", "stay_date", "日期", "date"),
    "room_type": ("房型", "room_type", "房型名称"),
    "budget_room_nights": ("预算间夜", "预算间夜量", "budget_room_nights"),
    "budget_revenue": ("预算收入", "预算营收", "budget_revenue"),
    "budget_adr": ("预算均价", "预算平均房价", "budget_adr"),
    "demand_forecast_room_nights": ("预测间夜", "需求预测间夜", "demand_forecast_room_nights"),
    "demand_forecast_revenue": ("预测收入", "需求预测收入", "demand_forecast_revenue"),
}

_VALUE_FIELDS = (
    "budget_room_nights",
    "budget_revenue",
    "budget_adr",
    "demand_forecast_room_nights",
    "demand_forecast_revenue",
)


@dataclass(frozen=True)
class BudgetRow:
    stay_date: date
    room_type: str  # 空串 = 全店汇总口径
    budget_room_nights: int | None = None
    budget_revenue: float | None = None
    budget_adr: float | None = None
    demand_forecast_room_nights: int | None = None
    demand_forecast_revenue: float | None = None


@dataclass
class ParsedImport:
    rows: list[BudgetRow] = field(default_factory=list)
    rows_total: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def rows_valid_count(self) -> int:
        return len(self.rows)

    def reject(self, row_index: int, reason: str) -> None:
        if len(self.errors) < MAX_ERRORS_KEPT:
            self.errors.append(f"第{row_index}行：{reason}")


def _normalize_header(value: Any) -> str:
    return str(value or "").strip()


def _header_to_field(headers: list[str]) -> dict[int, str]:
    """列位置 → 标准字段名（每个字段取第一个命中列）。"""
    mapping: dict[int, str] = {}
    used: set[int] = set()
    for field_name, aliases in _COLUMN_ALIASES.items():
        wanted = {alias.lower() for alias in aliases}
        for position, header in enumerate(headers):
            if position in used:
                continue
            if _normalize_header(header).lower() in wanted:
                mapping[position] = field_name
                used.add(position)
                break
    return mapping


def _parse_date_cell(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_number_cell(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "").replace("元", "")
    try:
        return float(text)
    except ValueError:
        return None


def _decode_csv_bytes(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        # WPS/Excel 中文导出常见 GBK 系编码。
        return payload.decode("gb18030")


def _read_csv_rows(payload: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(_decode_csv_bytes(payload), newline=""))
    return [dict(row) for row in reader]


def _read_xlsx_rows(payload: bytes) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    if not rows:
        return []
    headers = [_normalize_header(cell) for cell in rows[0]]
    dict_rows: list[dict[str, Any]] = []
    for values in rows[1:]:
        if all(value is None or str(value).strip() == "" for value in values):
            continue
        dict_rows.append(
            {headers[i]: (values[i] if i < len(values) else None) for i in range(len(headers))}
        )
    return dict_rows


def parse_budget_forecast_bytes(payload: bytes, *, filename: str) -> ParsedImport:
    """解析预算/forecast 文件为校验后的行集合。

    - 文件类型按扩展名分派（.csv / .xlsx）；其余扩展名直接拒绝。
    - 缺「入住日期」列视为致命错误（ValueError）。
    - 行级问题（非法日期、负数、非数值、全空值列）拒绝该行并留错误样本。
    """
    name = str(filename or "").strip().lower()
    if name.endswith(".csv"):
        raw_rows = _read_csv_rows(payload)
    elif name.endswith(".xlsx"):
        raw_rows = _read_xlsx_rows(payload)
    else:
        raise ValueError("仅支持 .csv 或 .xlsx 文件。")

    parsed = ParsedImport()
    if not raw_rows:
        raise ValueError("文件内容为空。")
    headers = list(raw_rows[0].keys())
    field_by_header: dict[str, str] = {
        headers[position]: field_name
        for position, field_name in _header_to_field(headers).items()
    }
    if "stay_date" not in field_by_header.values():
        raise ValueError("缺少「入住日期 / stay_date」列，无法导入。")

    def _cell(raw_row: dict[str, Any], wanted_field: str) -> Any:
        return next(
            (value for header, value in raw_row.items() if field_by_header.get(header) == wanted_field),
            None,
        )

    for index, raw_row in enumerate(raw_rows, start=2):
        parsed.rows_total += 1
        stay_date = _parse_date_cell(_cell(raw_row, "stay_date"))
        if stay_date is None:
            parsed.reject(index, "入住日期非法或缺失")
            continue
        room_raw = _cell(raw_row, "room_type")
        room_type = str(room_raw).strip() if room_raw is not None else ""
        room_type = "" if room_type == "全店" else room_type

        values: dict[str, float | None] = {}
        has_bad_number = False
        for field_name in _VALUE_FIELDS:
            number = _parse_number_cell(_cell(raw_row, field_name))
            if number is not None and number < 0:
                parsed.reject(index, f"{stay_date} {field_name} 为负数，预算类数值必须非负")
                has_bad_number = True
                break
            values[field_name] = number
        if has_bad_number:
            continue
        if all(value is None for value in values.values()):
            parsed.reject(index, f"{stay_date} 未提供任何预算/预测数值列")
            continue

        nights = values.get("budget_room_nights")
        forecast_nights = values.get("demand_forecast_room_nights")
        parsed.rows.append(
            BudgetRow(
                stay_date=stay_date,
                room_type=room_type,
                budget_room_nights=int(nights) if nights is not None else None,
                budget_revenue=values.get("budget_revenue"),
                budget_adr=values.get("budget_adr"),
                demand_forecast_room_nights=(
                    int(forecast_nights) if forecast_nights is not None else None
                ),
                demand_forecast_revenue=values.get("demand_forecast_revenue"),
            )
        )
    return parsed
