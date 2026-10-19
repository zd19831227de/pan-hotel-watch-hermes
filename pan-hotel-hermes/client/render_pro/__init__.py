# -*- coding: utf-8 -*-
"""高档文档渲染引擎（方案A，2026-10-09）：收编 pptx-builder / pdf-builder
skill 的「HTML → 原生文件」编译管线。

链路：markdown-lite → 固定预设样式 HTML（markdown_to_html）→ Playwright
测量（extract*.py）→ 编译（build_pptx.py / build_pdf.py，原生可编辑形状
/ 矢量文本）→ verify 校验。任何一步失败返回 None，由 document_render
回退轻量渲染器（天然回滚：PAN_HOTEL_DOC_PRO_DISABLED=1 可整体停用）。

vendored 脚本来自 C:\\Users\\Administrator\\.codex\\skills\\{pptx-builder,
pdf-builder}（MIT, Julien Febry），仅 build_pdf.py 打了中文字体表补丁。
依赖：playwright + lxml + python-pptx/reportlab（+pymupdf 仅校验用）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent
_SUPPORTED = ("pptx", "pdf")
_SUBPROCESS_TIMEOUT = 240


def pro_disabled() -> bool:
    return str(os.environ.get("PAN_HOTEL_DOC_PRO_DISABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _prepare_sections(markdown: str, title: str) -> list[dict[str, Any]]:
    from client.document_render import split_sections

    sections = split_sections(markdown)
    if not sections:
        sections = [{"title": title or "内容", "bullets": [], "paragraph": str(markdown or "").strip()}]
    if (
        sections
        and str(sections[0].get("title") or "").strip() == str(title or "").strip()
        and not (sections[0].get("table") or sections[0].get("bullets"))
        and not str(sections[0].get("paragraph") or "").strip()
    ):
        sections = sections[1:]
    return [s for s in sections if s.get("table") or s.get("bullets") or str(s.get("paragraph") or "").strip()]


def _run(script: str, *args: str, workdir: str) -> None:
    result = subprocess.run(
        [sys.executable, str(_DIR / script), *[str(a) for a in args]],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_SUBPROCESS_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{script} failed rc={result.returncode}: {(result.stderr or result.stdout or '')[:600]}"
        )
    stderr = (result.stderr or "").strip()
    if stderr:
        logger.info("render_pro %s warnings: %s", script, stderr[:400])


def render_document_pro(markdown: str, *, doc_type: str, title: str = "") -> bytes | None:
    """成功返回文件 bytes；未启用/不支持/任何失败返回 None（调用方回退轻量渲染）。"""
    doc = str(doc_type or "").strip().lower()
    if doc not in _SUPPORTED or pro_disabled():
        return None
    try:
        from client.render_pro.markdown_to_html import DEFAULT_PRESET, build_html

        preset = str(os.environ.get("PAN_HOTEL_DOC_PRO_PRESET") or DEFAULT_PRESET).strip().upper()
        sections = _prepare_sections(markdown, title)
        if not sections:
            return None
        html = build_html(sections, doc_type=doc, title=title or "运营文档", preset=preset)
        workdir = tempfile.mkdtemp(prefix="panhotel-renderpro-")
        try:
            html_path = os.path.join(workdir, "doc.html")
            json_path = os.path.join(workdir, "doc.json")
            images_dir = os.path.join(workdir, "images")
            out_path = os.path.join(workdir, "out." + doc)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write(html)
            extractor = "extract_pdf.py" if doc == "pdf" else "extract.py"
            builder = "build_pdf.py" if doc == "pdf" else "build_pptx.py"
            verifier = "verify_pdf.py" if doc == "pdf" else "verify_pptx.py"
            _run(extractor, html_path, json_path, images_dir, workdir=workdir)
            _run(builder, json_path, out_path, workdir=workdir)
            _run(verifier, out_path, workdir=workdir)
            data = Path(out_path).read_bytes()
            head = data[:4]
            if len(data) < 2000 or (head != b"%PDF" if doc == "pdf" else head != b"PK\x03\x04"):
                raise RuntimeError(f"suspicious output head={head!r} len={len(data)}")
            return data
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    except Exception as exc:
        logger.warning("render_document_pro(%s) failed, fallback to lightweight: %s", doc, exc)
        return None
