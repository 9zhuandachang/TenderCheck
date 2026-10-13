#!/usr/bin/env python3
"""
TenderCheck 第一阶段 PDF 文本分块脚本

功能：
1. 使用 PyMuPDF 逐页提取带坐标的文字行；
2. 自动识别商务文件、报价文件和技术文件封面；
3. 删除重复页眉、页脚和页码；
4. 标记目录、评审索引、表格、证书和疑似扫描页；
5. 对普通正文进行标题感知的初步分块；
6. 输出 pages.jsonl、chunks.jsonl 和 stats.json。

安装依赖：
    pip install pymupdf

运行示例：
    python TenderCheck_Stage1_Chunker.py "投标文件.pdf" --output-dir stage1_output

快速测试前 50 页：
    python TenderCheck_Stage1_Chunker.py "投标文件.pdf" \
        --output-dir stage1_test --start-page 1 --end-page 50

说明：
- 第一阶段不执行 OCR，只把需要 OCR 的页面标记为 needs_ocr=true。
- 第一阶段不重建复杂表格，疑似表格页会按页面保留并标记。
- 默认使用字符数控制分块，不依赖特定大模型的 tokenizer。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF


PAGE_NUMBER_RE = re.compile(
    r"^\s*第?\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?\s*$"
)
ONLY_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇]+章\s*.+")
ATTACHMENT_RE = re.compile(r"^附件[一二三四五六七八九十百零〇\d]+[：:]?\s*.+")
SENTENCE_END_RE = re.compile(r"(?<=[。！？；!?;])")

TABLE_TITLE_KEYWORDS = (
    "工程量清单",
    "计价表",
    "汇总表",
    "单价分析表",
    "工程项目总价表",
    "设备表",
    "仪器设备表",
    "劳动力计划表",
    "项目管理机构表",
    "人员表",
    "评审表",
)

CERTIFICATE_KEYWORDS = (
    "营业执照",
    "资质证书",
    "注册证书",
    "建造师证",
    "安全生产许可证",
    "身份证",
    "职称证书",
    "获奖证书",
    "社保证明",
    "完工验收",
)

COMMITMENT_KEYWORDS = (
    "投标函",
    "我方承诺",
    "承诺书",
    "授权委托书",
    "联合体协议书",
)


@dataclass
class TextLine:
    text: str
    bbox: tuple[float, float, float, float]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def y1(self) -> float:
        return self.bbox[3]


@dataclass
class PageRecord:
    pdf_page: int
    width: float
    height: float
    volume: str
    printed_page: int | None
    raw_lines: list[TextLine]
    image_count: int
    is_volume_cover: bool = False
    clean_lines: list[TextLine] = field(default_factory=list)
    content_type: str = "narrative"
    needs_ocr: bool = False

    @property
    def raw_text(self) -> str:
        return "\n".join(line.text for line in self.raw_lines)

    @property
    def clean_text(self) -> str:
        return "\n".join(line.text for line in self.clean_lines)


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def normalize_repeated_line(text: str) -> str:
    """用于检测重复页眉页脚，不改变最终保存的文字。"""
    text = compact_text(text)
    text = text.replace("—", "-").replace("–", "-")
    return text


def detect_volume_cover(text: str) -> str | None:
    compact = compact_text(text)
    exact_lines = {compact_text(line) for line in text.splitlines() if line.strip()}
    # 封面必须把“投标文件”和分册名称作为独立文本行，避免正文中偶然
    # 出现“投标文件”一词时被误判为新封面。
    if "投标文件" not in exact_lines or len(compact) > 800:
        return None
    if "商务文件" in exact_lines:
        return "商务文件"
    if "报价文件" in exact_lines:
        return "报价文件"
    if "技术文件" in exact_lines:
        return "技术文件"
    return None


def extract_printed_page(text: str, pdf_page: int) -> int | None:
    """优先提取文件内部页码，排除“第X页/共Y页”的PDF总页码。"""
    total_spans: list[tuple[int, int]] = []
    total_re = re.compile(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页")
    for match in total_re.finditer(text):
        total_spans.append(match.span())

    candidates: list[int] = []
    for match in re.finditer(r"第\s*(\d+)\s*页", text):
        if any(start <= match.start() < end for start, end in total_spans):
            continue
        candidates.append(int(match.group(1)))

    candidates = [number for number in candidates if number != pdf_page]
    return min(candidates) if candidates else None


def extract_text_lines(page: fitz.Page) -> list[TextLine]:
    page_dict = page.get_text("dict", sort=True)
    result: list[TextLine] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            bbox = tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0)))
            result.append(TextLine(text=text, bbox=bbox))

    result.sort(key=lambda item: (round(item.y0, 1), item.bbox[0]))
    return result


def scan_pdf(
    pdf_path: Path,
    start_page: int,
    end_page: int | None,
) -> list[PageRecord]:
    document = fitz.open(pdf_path)
    last_page = min(end_page or len(document), len(document))
    if start_page < 1 or start_page > last_page:
        raise ValueError("start-page 必须在有效页码范围内")

    pages: list[PageRecord] = []
    current_volume = "前置目录"

    for pdf_page in range(start_page, last_page + 1):
        page = document[pdf_page - 1]
        lines = extract_text_lines(page)
        text = "\n".join(line.text for line in lines)
        cover_volume = detect_volume_cover(text)
        if cover_volume:
            current_volume = cover_volume

        pages.append(
            PageRecord(
                pdf_page=pdf_page,
                width=float(page.rect.width),
                height=float(page.rect.height),
                volume=current_volume,
                printed_page=extract_printed_page(text, pdf_page),
                raw_lines=lines,
                image_count=len(page.get_images(full=True)),
                is_volume_cover=cover_volume is not None,
            )
        )

    document.close()
    return pages


def find_repeated_margin_lines(pages: list[PageRecord]) -> dict[str, set[str]]:
    """按分册统计高频页眉页脚，避免不同分册相互干扰。"""
    grouped_pages: dict[str, list[PageRecord]] = defaultdict(list)
    for page in pages:
        grouped_pages[page.volume].append(page)

    repeated_by_volume: dict[str, set[str]] = {}

    for volume, volume_pages in grouped_pages.items():
        counter: Counter[str] = Counter()
        for page in volume_pages:
            seen_on_page: set[str] = set()
            for line in page.raw_lines:
                in_top = line.y1 <= page.height * 0.15
                in_bottom = line.y0 >= page.height * 0.86
                if not (in_top or in_bottom):
                    continue

                normalized = normalize_repeated_line(line.text)
                if 2 <= len(normalized) <= 120:
                    seen_on_page.add(normalized)

            counter.update(seen_on_page)

        threshold = max(5, math.ceil(len(volume_pages) * 0.18))
        repeated_by_volume[volume] = {
            line for line, count in counter.items() if count >= threshold
        }

    return repeated_by_volume


def clean_pages(
    pages: list[PageRecord],
    repeated_by_volume: dict[str, set[str]],
) -> None:
    for page in pages:
        repeated = repeated_by_volume.get(page.volume, set())
        clean_lines: list[TextLine] = []

        for line in page.raw_lines:
            stripped = line.text.strip()
            normalized = normalize_repeated_line(stripped)
            in_margin = line.y1 <= page.height * 0.15 or line.y0 >= page.height * 0.86

            if PAGE_NUMBER_RE.match(stripped):
                continue
            if in_margin and ONLY_NUMBER_RE.match(stripped):
                continue
            if in_margin and normalized in repeated:
                continue

            clean_lines.append(line)

        page.clean_lines = clean_lines
        body_chars = len(compact_text(page.clean_text))
        has_explicit_empty_answer = any(
            compact_text(line.text) in {"无", "无。", "不适用", "不适用。"}
            for line in clean_lines
        )
        page.needs_ocr = (
            body_chars < 80
            and page.image_count > 0
            and not page.is_volume_cover
            and not has_explicit_empty_answer
        )
        page.content_type = classify_page(page)


def classify_page(page: PageRecord) -> str:
    text = page.clean_text
    compact = compact_text(text)

    if page.is_volume_cover:
        return "cover"
    if any(
        compact_text(line.text) in {"无", "无。", "不适用", "不适用。"}
        for line in page.clean_lines
    ):
        return "short_statement"
    if "评审因素索引表" in compact:
        return "index"
    numbered_toc_lines = sum(
        bool(re.match(r"^\s*\d+[.、]", line.text)) for line in page.clean_lines
    )
    if "目录" in compact and (
        text.count("...") >= 2
        or len(re.findall(r"\.{5,}", text)) >= 2
        or len(re.findall(r"\d+\.\d+", text)) >= 6
        or numbered_toc_lines >= 5
    ):
        return "toc"
    if any(keyword in compact for keyword in CERTIFICATE_KEYWORDS) and page.image_count > 0:
        return "certificate"
    if page.needs_ocr:
        return "scanned"
    if any(keyword in compact for keyword in COMMITMENT_KEYWORDS):
        return "commitment"

    nonempty_lines = [line.text for line in page.clean_lines if line.text.strip()]
    head_compact = compact_text("\n".join(nonempty_lines[:8]))
    table_keyword = any(keyword in head_compact for keyword in TABLE_TITLE_KEYWORDS)
    numeric_lines = sum(bool(re.search(r"\d", line)) for line in nonempty_lines)
    numeric_ratio = numeric_lines / max(1, len(nonempty_lines))
    if table_keyword or (len(nonempty_lines) >= 10 and numeric_ratio >= 0.55):
        return "table"

    return "narrative"


def heading_level(text: str) -> int | None:
    text = text.strip().lstrip("★►●◆■□").strip()
    if len(text) > 100:
        return None
    if CHAPTER_RE.match(text) or ATTACHMENT_RE.match(text):
        return 1
    # 编号与标题之间必须存在空格。这样“1.5m以上”等工程数值不会被
    # 误判为“1.5”小节标题。五级及更深标题统一归入第四级元数据。
    numbered = re.match(r"^(\d+(?:\.\d+){1,5})\s+(.+)$", text)
    if numbered:
        return min(numbered.group(1).count(".") + 1, 4)
    return None


def split_long_text(text: str, max_chars: int) -> list[str]:
    """优先按段落和句号切分；极长句最后才按字符硬切。"""
    text = text.strip()
    if not text:
        return []
    if len(compact_text(text)) <= max_chars:
        return [text]

    units: list[str] = []
    for paragraph in re.split(r"\n{2,}", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = [part.strip() for part in SENTENCE_END_RE.split(paragraph) if part.strip()]
        units.extend(sentences or [paragraph])

    pieces: list[str] = []
    current = ""

    for unit in units:
        if len(compact_text(unit)) > max_chars:
            if current:
                pieces.append(current.strip())
                current = ""
            compact_unit = unit.strip()
            while len(compact_text(compact_unit)) > max_chars:
                pieces.append(compact_unit[:max_chars].strip())
                compact_unit = compact_unit[max_chars:].strip()
            if compact_unit:
                current = compact_unit
            continue

        candidate = f"{current}\n{unit}".strip() if current else unit
        if len(compact_text(candidate)) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current.strip())
            current = unit

    if current:
        pieces.append(current.strip())
    return pieces


def title_path(headings: dict[int, str]) -> list[str]:
    return [headings[level] for level in sorted(headings) if headings[level]]


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def make_chunk(
    document_id: str,
    chunk_index: int,
    volume: str,
    content_type: str,
    headings: dict[int, str],
    text: str,
    page_start: int,
    page_end: int,
    printed_page_start: int | None,
    printed_page_end: int | None,
    needs_ocr: bool = False,
    structure_pending: bool = False,
) -> dict[str, Any]:
    path = title_path(headings)
    prefix = "\n".join(path)
    embedding_text = f"{prefix}\n\n{text}".strip() if prefix else text.strip()
    parent_key = stable_id(document_id, volume, *path) if path else stable_id(document_id, volume)

    return {
        "chunk_id": f"{document_id}_chunk_{chunk_index:06d}",
        "parent_id": f"parent_{parent_key}",
        "document_id": document_id,
        "volume": volume,
        "content_type": content_type,
        "title_path": path,
        "chapter": headings.get(1),
        "section": headings.get(2),
        "subsection": headings.get(3),
        "subsubsection": headings.get(4),
        "pdf_page_start": page_start,
        "pdf_page_end": page_end,
        "printed_page_start": printed_page_start,
        "printed_page_end": printed_page_end,
        "needs_ocr": needs_ocr,
        "structure_pending": structure_pending,
        "char_count": len(compact_text(text)),
        "text": text.strip(),
        "embedding_text": embedding_text,
    }


def build_chunks(
    pages: list[PageRecord],
    document_id: str,
    target_chars: int,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    headings: dict[int, str] = {}
    current_volume: str | None = None
    buffer_parts: list[str] = []
    buffer_page_start: int | None = None
    buffer_page_end: int | None = None
    buffer_printed_start: int | None = None
    buffer_printed_end: int | None = None
    buffer_content_type = "narrative"
    chunk_index = 1

    def buffer_text() -> str:
        return "\n".join(part for part in buffer_parts if part).strip()

    def flush_buffer(keep_overlap: bool = False) -> None:
        nonlocal chunk_index
        nonlocal buffer_parts, buffer_page_start, buffer_page_end
        nonlocal buffer_printed_start, buffer_printed_end, buffer_content_type

        text = buffer_text()
        if text and len(compact_text(text)) >= min_chars:
            chunks.append(
                make_chunk(
                    document_id=document_id,
                    chunk_index=chunk_index,
                    volume=current_volume or "未识别分册",
                    content_type=buffer_content_type,
                    headings=headings,
                    text=text,
                    page_start=buffer_page_start or 1,
                    page_end=buffer_page_end or buffer_page_start or 1,
                    printed_page_start=buffer_printed_start,
                    printed_page_end=buffer_printed_end,
                )
            )
            chunk_index += 1

        overlap = ""
        overlap_page = buffer_page_end
        overlap_printed = buffer_printed_end
        if keep_overlap and text and overlap_chars > 0:
            overlap = text[-overlap_chars:].strip()

        buffer_parts = [overlap] if overlap else []
        buffer_page_start = overlap_page if overlap else None
        buffer_page_end = overlap_page if overlap else None
        buffer_printed_start = overlap_printed if overlap else None
        buffer_printed_end = overlap_printed if overlap else None

    special_types = {
        "cover",
        "toc",
        "index",
        "table",
        "certificate",
        "scanned",
        "short_statement",
    }

    for page in pages:
        if current_volume != page.volume:
            flush_buffer(keep_overlap=False)
            headings = {}
            current_volume = page.volume

        if page.content_type in special_types:
            flush_buffer(keep_overlap=False)
            text = page.clean_text.strip()
            if not text:
                continue

            # “无/不适用”等短声明通常以页面第一行为该部分标题，需要
            # 清除上一附件的标题路径，避免元数据串到前一节。
            if page.content_type == "short_statement" and page.clean_lines:
                headings = {1: page.clean_lines[0].text.strip()}

            # 技术文件中的页面可能同时包含小节标题和表格。仅在技术分册
            # 更新这类编号标题；报价表中的“1.1.7 项目名称”不能被当作
            # 施工组织设计的小节标题。
            if page.content_type == "table" and page.volume == "技术文件":
                for line in page.clean_lines:
                    level = heading_level(line.text)
                    if level is None:
                        continue
                    headings[level] = line.text.strip()
                    for deeper_level in list(headings):
                        if deeper_level > level:
                            headings.pop(deeper_level, None)

            # OCR 尚未执行时，不把只有页眉或标题的扫描页放进检索索引。
            # 这些页面仍会完整保存在 pages.jsonl，并标记 needs_ocr=true。
            if page.needs_ocr and len(compact_text(text)) < min_chars:
                continue

            # 对扫描件与证书暂不破坏页面完整性；复杂表格只做初步页级保存。
            structure_pending = page.content_type in {"table", "certificate", "scanned"}
            parts = split_long_text(text, max_chars=max_chars)
            for part in parts:
                if len(compact_text(part)) < min_chars and page.content_type not in {
                    "cover",
                    "short_statement",
                }:
                    continue
                chunks.append(
                    make_chunk(
                        document_id=document_id,
                        chunk_index=chunk_index,
                        volume=page.volume,
                        content_type=page.content_type,
                        headings=headings,
                        text=part,
                        page_start=page.pdf_page,
                        page_end=page.pdf_page,
                        printed_page_start=page.printed_page,
                        printed_page_end=page.printed_page,
                        needs_ocr=page.needs_ocr,
                        structure_pending=structure_pending,
                    )
                )
                chunk_index += 1
            continue

        # 普通正文和承诺文本：遇到标题立即结束上一块，防止跨章节混合。
        text_units: list[tuple[str, int | None]] = []
        pending_lines: list[str] = []

        for line in page.clean_lines:
            level = heading_level(line.text)
            if level is not None:
                if pending_lines:
                    text_units.append(("\n".join(pending_lines), None))
                    pending_lines = []
                text_units.append((line.text.strip(), level))
            else:
                pending_lines.append(line.text.strip())

        if pending_lines:
            text_units.append(("\n".join(pending_lines), None))

        for unit_text, level in text_units:
            if level is not None:
                flush_buffer(keep_overlap=False)
                headings[level] = unit_text
                for deeper_level in list(headings):
                    if deeper_level > level:
                        headings.pop(deeper_level, None)
                continue

            for piece in split_long_text(unit_text, max_chars=max_chars):
                current_text = buffer_text()
                candidate = f"{current_text}\n{piece}".strip() if current_text else piece

                if current_text and len(compact_text(candidate)) > max_chars:
                    flush_buffer(keep_overlap=True)

                if buffer_page_start is None:
                    buffer_page_start = page.pdf_page
                    buffer_printed_start = page.printed_page
                buffer_page_end = page.pdf_page
                buffer_printed_end = page.printed_page
                buffer_content_type = page.content_type
                buffer_parts.append(piece)

                if len(compact_text(buffer_text())) >= target_chars:
                    flush_buffer(keep_overlap=True)

    flush_buffer(keep_overlap=False)
    return chunks


def page_to_json(page: PageRecord) -> dict[str, Any]:
    return {
        "pdf_page": page.pdf_page,
        "printed_page": page.printed_page,
        "volume": page.volume,
        "content_type": page.content_type,
        "needs_ocr": page.needs_ocr,
        "image_count": page.image_count,
        "raw_char_count": len(compact_text(page.raw_text)),
        "clean_char_count": len(compact_text(page.clean_text)),
        "clean_text": page.clean_text,
    }


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_stats(
    pdf_path: Path,
    pages: list[PageRecord],
    chunks: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    page_types = Counter(page.content_type for page in pages)
    volumes = Counter(page.volume for page in pages)
    chunk_types = Counter(chunk["content_type"] for chunk in chunks)
    chunk_lengths = [chunk["char_count"] for chunk in chunks]

    return {
        "source_pdf": pdf_path.name,
        "processed_page_count": len(pages),
        "chunk_count": len(chunks),
        "ocr_candidate_pages": sum(page.needs_ocr for page in pages),
        "structure_pending_chunks": sum(chunk["structure_pending"] for chunk in chunks),
        "page_types": dict(page_types),
        "volumes": dict(volumes),
        "chunk_types": dict(chunk_types),
        "chunk_char_count": {
            "min": min(chunk_lengths) if chunk_lengths else 0,
            "median": statistics.median(chunk_lengths) if chunk_lengths else 0,
            "mean": round(statistics.mean(chunk_lengths), 2) if chunk_lengths else 0,
            "max": max(chunk_lengths) if chunk_lengths else 0,
        },
        "parameters": parameters,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TenderCheck 第一阶段 PDF 文本分块"
    )
    parser.add_argument("pdf", type=Path, help="输入PDF路径")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("stage1_output"),
        help="输出目录，默认 stage1_output",
    )
    parser.add_argument("--start-page", type=int, default=1, help="起始PDF页码")
    parser.add_argument("--end-page", type=int, default=None, help="结束PDF页码")
    parser.add_argument(
        "--target-chars",
        type=int,
        default=800,
        help="普通正文目标字符数，默认800",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=1100,
        help="普通正文最大字符数，默认1100",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=100,
        help="同一小节相邻Chunk重叠字符数，默认100",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=80,
        help="保存Chunk的最小字符数，默认80",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"找不到PDF：{pdf_path}")
    if args.target_chars > args.max_chars:
        raise ValueError("target-chars 不能大于 max-chars")
    if args.overlap_chars >= args.target_chars:
        raise ValueError("overlap-chars 必须小于 target-chars")

    output_dir.mkdir(parents=True, exist_ok=True)
    document_id = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", pdf_path.stem).strip("_")

    print("[1/5] 扫描PDF并提取页面文字……")
    pages = scan_pdf(pdf_path, args.start_page, args.end_page)

    print("[2/5] 检测并清理重复页眉页脚……")
    repeated = find_repeated_margin_lines(pages)
    clean_pages(pages, repeated)

    print("[3/5] 执行标题感知的初步分块……")
    chunks = build_chunks(
        pages=pages,
        document_id=document_id,
        target_chars=args.target_chars,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
        min_chars=args.min_chars,
    )

    print("[4/5] 写出JSONL结果……")
    write_jsonl(output_dir / "pages.jsonl", (page_to_json(page) for page in pages))
    write_jsonl(output_dir / "chunks.jsonl", chunks)

    parameters = {
        "start_page": args.start_page,
        "end_page": args.end_page,
        "target_chars": args.target_chars,
        "max_chars": args.max_chars,
        "overlap_chars": args.overlap_chars,
        "min_chars": args.min_chars,
    }
    stats = build_stats(pdf_path, pages, chunks, parameters)
    with (output_dir / "stats.json").open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    print("[5/5] 完成")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"页面文件：{output_dir / 'pages.jsonl'}")
    print(f"分块文件：{output_dir / 'chunks.jsonl'}")
    print(f"统计文件：{output_dir / 'stats.json'}")


if __name__ == "__main__":
    main()

