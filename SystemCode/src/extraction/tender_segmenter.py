"""
Tender Document Structural Segmenter & Candidate Clause Extractor (No LLM API).

Reads a tender PDF (e.g., `招标文件正文.pdf`) and segments it into self-contained
candidate requirement units `(page, section, text, raw_text, source_type)` by:
1. Skipping Table of Contents (TOC) pages and pure cover pages.
2. Tracking chapter headings (`第X章 ...`), section headings (`第X节 ...`),
   form titles in Chapter 8/9 (`一、投标函`, `投标诚信承诺书`, `商务文件封面`),
   and numeric clause identifiers (`1.4.3`, `3.5.2`, `一、`, `（一）`).
3. Extracting table rows via PyMuPDF `find_tables()`, propagating multi-row merged
   headers, filtering out unchecked checkbox options (`□...` when `☑` is present),
   retaining short checked cells (e.g. `☑总价`, `☑不允许`) by combining with their
   row header, splitting multi-item table cells into atomic sub-clauses, and
   prepending the table's factor/clause name as context prefix.
4. Parsing non-table body text with a 2-level parent-child prefix tree so that
   sub-items (`（1）`, `⑴`, `①`, `a.`) and Chapter 8/9 form fill-in/seal lines
   automatically inherit the subject/form header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pymupdf


@dataclass
class CandidateClause:
    """A single candidate clause unit segmented from a tender PDF."""

    candidate_id: str
    doc_name: str
    page: int
    chapter: str
    section: str
    text: str
    raw_text: str
    source_type: str  # "table" or "body"
    bbox: Optional[Tuple[float, float, float, float]] = None

    def to_dict(self) -> Dict:
        return asdict(self)


# Regex patterns for structural hierarchy
_CHAPTER_RE = re.compile(r"^\s*(第[一二三四五六七八九十百]+章)\s*([^\n]{0,30})")
_SECTION_RE = re.compile(r"^\s*(第[一二三四五六七八九十]+节)\s*([^\n]{0,30})")
_APPENDIX_RE = re.compile(r"^\s*(附录\s*[0-9一二三四五六七八九十]+|附件\s*[0-9一二三四五六七八九十]+)\s*([^\n]{0,30})")
_FORM_TITLE_RE = re.compile(
    r"^\s*((?:[一二三四五六七八九十]+[、.．]\s*[^\n：:]{2,25})|(?:（[一二三四五六七八九十]+）\s*[^\n：:]{2,25})|(?:[^\n：:]{2,18}(?:承诺书|承诺函|投标函|协议书|授权委托书|身份证明|情况表|汇总表|简历表)))\s*$"
)
_TOC_LINE_RE = re.compile(r"\.{4,}\s*\d+\s*$|[…]{2,}\s*\d+\s*$")
_PAGE_NUMBER_ONLY_RE = re.compile(r"^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$")

# Clause numbering levels
_LEVEL1_NUM_RE = re.compile(
    r"^\s*((?:\d+\.\d+(?:\.\d+)*)|(?:[一二三四五六七八九十]+[、.．])|(?:（[一二三四五六七八九十]+）)|(?:\([一二三四五六七八九十]+\)))\s*(.+)"
)
_LEVEL2_NUM_RE = re.compile(
    r"^\s*((?:\d+[、.．\s])|(?:（\d+）)|(?:\(\d+\))|(?:[⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽⑾⑿⒀⒁⒂⒃⒄⒅⒆⒇]))\s*(.+)"
)
_LEVEL3_NUM_RE = re.compile(
    r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]|[a-zA-Z][.、．)])\s*(.+)"
)
_NOTE_RE = re.compile(r"^\s*(注[：:1-9一二三四五六七八九十]*|说明[：:]|备注[：:]|提示[：:]|注意[：:])\s*(.+)")
_SEAL_OR_FIELD_START_RE = re.compile(
    r"^\s*(投标人[：:（(]|法定代表人|委托代理人|担保人|基本账户|开户名|工程质量[：:]|安全目标[：:]|工期[：:])"
)


def _clean_whitespace(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _clean_checkbox_options(text: str) -> str:
    """
    If a text block contains checked options (`☑` or `■`), remove unchecked
    options (`□...`) so that inactive template branches do not pollute candidate clauses.
    """
    if "☑" not in text and "■" not in text:
        return text
    cleaned = re.sub(r"□[^☑■\n；;]*", "", text)
    return _clean_whitespace(cleaned)


def _is_toc_page(page_text: str, page_idx: int) -> bool:
    """Detect Table of Contents pages in the first 15 pages."""
    if page_idx > 15:
        return False
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    if not lines:
        return True
    joined = "".join(lines[:5])
    if "目录" in joined or "目 录" in joined:
        return True
    toc_lines = sum(1 for ln in lines if _TOC_LINE_RE.search(ln))
    return toc_lines >= 4


def _bboxes_overlap(
    b1: Tuple[float, float, float, float],
    b2: Tuple[float, float, float, float],
    threshold: float = 0.5,
) -> bool:
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return False
    inter = (x1 - x0) * (y1 - y0)
    area1 = max((b1[2] - b1[0]) * (b1[3] - b1[1]), 1.0)
    return (inter / area1) >= threshold


def _split_long_unit(text: str, min_cjk_len: int = 4) -> List[str]:
    """
    Split a cell or paragraph into sub-clauses if it contains explicit numbered
    sub-items `(1)`, `①`, etc., or multiple independent sentences separated by `；` / `。`.
    """
    text = _clean_checkbox_options(text)
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    # Join soft-wrapped lines inside the unit
    merged_lines: List[str] = []
    for ln in lines:
        if _PAGE_NUMBER_ONLY_RE.match(ln):
            continue
        is_new_item = bool(
            _LEVEL1_NUM_RE.match(ln)
            or _LEVEL2_NUM_RE.match(ln)
            or _LEVEL3_NUM_RE.match(ln)
            or _NOTE_RE.match(ln)
            or _SEAL_OR_FIELD_START_RE.match(ln)
            or ln.startswith(("☑", "□", "■", "格式一", "格式二"))
        )
        if not merged_lines or is_new_item or merged_lines[-1].endswith(("。", "；", "：", ":", "!", "！")):
            # If the previous line was a standalone speaker/actor label like `投标人：` or `法定代表人：`
            # and the current line is `（盖单位电子印章）`, merge them onto the same line!
            if (
                merged_lines
                and re.fullmatch(r"(投标人|法定代表人|委托代理人|担保人|单位名称)[：:]?", merged_lines[-1])
                and ("章" in ln or "签字" in ln)
            ):
                merged_lines[-1] = merged_lines[-1] + ln
            else:
                merged_lines.append(ln)
        else:
            merged_lines[-1] = merged_lines[-1] + ln

    # Propagate colon-ending intro line to numbered sub-items
    results: List[str] = []
    active_intro = ""
    for ln in merged_lines:
        if (
            ln.endswith(("：", ":"))
            and len(ln) <= 85
            and not _SEAL_OR_FIELD_START_RE.match(ln)
            and "章" not in ln
            and "签字" not in ln
        ):
            active_intro = ln.rstrip("：:")
            continue
        inline_parts = re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩]|（\d+）|\(\d+\)|[⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽])", ln)
        inline_parts = [p.strip() for p in inline_parts if p.strip()]
        if len(inline_parts) > 1:
            first_part = inline_parts[0]
            sub_intro = active_intro
            if first_part.endswith(("：", ":")) or (
                not re.match(r"^([①②③④⑤⑥⑦⑧⑨⑩]|（\d+）|\(\d+\)|[⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽])", first_part)
                and len(first_part) <= 60
            ):
                sub_intro = f"{active_intro} {first_part.rstrip('：:')}".strip() if active_intro else first_part.rstrip("：:")
                inline_parts = inline_parts[1:]
            for pt in inline_parts:
                for sent in _split_sentences(pt):
                    full = f"{sub_intro}：{sent}" if sub_intro and sub_intro not in sent else sent
                    results.append(full)
        else:
            for sent in _split_sentences(ln):
                full = f"{active_intro}：{sent}" if active_intro and active_intro not in sent else sent
                results.append(full)

    return [r.strip() for r in results if len(re.findall(r"[\u4e00-\u9fff0-9]", r)) >= min_cjk_len]


def _split_sentences(text: str) -> List[str]:
    """Split a long clause by `。` or `；` when each part is a substantive statement."""
    text = text.strip()
    # Special handling for multi-field fill-in lines like "3. 工程质量： ；安全目标： ；工期： 日历天。"
    if "；" in text and text.count("：") >= 2 and len(text) <= 90:
        sub_fields = [p.strip() for p in re.split(r"[；;]\s*", text) if p.strip()]
        if len(sub_fields) >= 2:
            return sub_fields

    if len(text) <= 90:
        return [text]
    parts = [p.strip() for p in re.split(r"[。；;]\s*", text) if p.strip()]
    if len(parts) <= 1:
        return [text]
    out: List[str] = []
    buf = ""
    for p in parts:
        if len(buf) + len(p) < 35:
            buf = f"{buf}；{p}" if buf else p
        else:
            if buf:
                out.append(buf + "。")
            buf = p
    if buf:
        out.append(buf + ("。" if not buf.endswith("。") else ""))
    return out


def segment_tender_pdf(pdf_path: Path, doc_label: Optional[str] = None) -> List[CandidateClause]:
    """
    Segment a tender PDF file into structured `CandidateClause` objects.
    """
    pdf_path = Path(pdf_path)
    doc_name = doc_label or pdf_path.name
    doc = pymupdf.open(str(pdf_path))

    candidates: List[CandidateClause] = []
    current_chapter = "第一章"
    current_section = ""
    current_form_title = ""
    current_clause_no = ""

    last_table_col0 = ""
    last_table_col1 = ""

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num = page_idx + 1
        raw_page_text = page.get_text("text") or ""

        if _is_toc_page(raw_page_text, page_idx):
            continue

        # Detect chapter/form headers on this page
        page_lines = [ln.strip() for ln in raw_page_text.splitlines() if ln.strip()]
        for sline in page_lines:
            m_chap = _CHAPTER_RE.match(sline)
            if m_chap and not _TOC_LINE_RE.search(sline):
                current_chapter = f"{m_chap.group(1)} {m_chap.group(2).strip()}".strip()
                current_section = ""
                current_form_title = ""
                current_clause_no = ""
                break

        # Detect cover page in Chapter 8 / Chapter 9 (e.g. 投标文件（商务文件）... 投标人：（盖单位章）)
        joined_page = " ".join(page_lines)
        if ("第八章" in current_chapter or "第九章" in current_chapter or "格式" in current_chapter):
            if "商务文件" in joined_page and len(page_lines) <= 15:
                current_form_title = "商务文件封面"
            elif "报价文件" in joined_page and len(page_lines) <= 15:
                current_form_title = "报价文件封面"
            elif "技术文件" in joined_page and len(page_lines) <= 15:
                current_form_title = "技术文件封面"

        # 1. Extract tables on this page
        table_bboxes: List[Tuple[float, float, float, float]] = []
        try:
            tabs = page.find_tables()
            tables_list = tabs.tables if tabs else []
        except Exception:
            tables_list = []

        for tab in tables_list:
            tbbox = tuple(tab.bbox)
            rows = tab.extract()
            if not rows:
                continue
            table_bboxes.append(tbbox)

            for r_idx, row in enumerate(rows):
                cells = [_clean_whitespace(str(c or "")) for c in row if c is not None and str(c) != "None"]
                # Keep original positional cells too
                pos_cells = [_clean_whitespace(str(c or "")) if (c is not None and str(c) != "None") else "" for c in row]
                non_empty = [c for c in pos_cells if c]
                if not non_empty:
                    continue

                joined_row = " ".join(non_empty)
                if r_idx == 0 and any(h in joined_row for h in ("条款号", "评审因素", "评审标准", "序号", "条款名称", "编列内容")):
                    if len(joined_row) < 40:
                        continue

                if len(pos_cells) >= 3:
                    c0 = pos_cells[0] or last_table_col0
                    c1 = pos_cells[1] or last_table_col1
                    if pos_cells[0]:
                        last_table_col0 = pos_cells[0]
                    if pos_cells[1]:
                        last_table_col1 = pos_cells[1]
                    content_cells = [c for c in pos_cells[2:] if c]
                    if content_cells:
                        header_prefix = " ".join(x for x in (c0, c1) if x).strip()
                        sec_id = c0 if re.match(r"^\d+(\.\d+)*$", c0) else (c1[:12] if c1 else current_clause_no)
                    else:
                        header_prefix = c0
                        content_cells = [c1] if c1 else []
                        sec_id = c0 if re.match(r"^\d+(\.\d+)*$", c0) else current_clause_no
                elif len(pos_cells) == 2:
                    c0 = pos_cells[0] or last_table_col0
                    if pos_cells[0]:
                        last_table_col0 = pos_cells[0]
                    header_prefix = c0
                    content_cells = [pos_cells[1]] if pos_cells[1] else []
                    sec_id = c0[:12]
                else:
                    header_prefix = ""
                    content_cells = non_empty
                    sec_id = current_clause_no

                body_str = "\n".join(content_cells).strip()
                if not body_str:
                    continue

                # Skip pure cross-reference stubs like "详见招标公告" or "无"
                if re.fullmatch(r"(无|/|详见[^\n。；]{1,15}|见[^\n。；]{1,15}|同[^\n。；]{1,15})[。.]?", body_str):
                    continue

                # Allow short 1-3 char values (like "☑总价", "☑不允许", "☑采用") when header_prefix is present!
                min_len = 1 if header_prefix else 4
                sub_units = _split_long_unit(body_str, min_cjk_len=min_len)
                for unit in sub_units:
                    if header_prefix and header_prefix not in unit:
                        full_text = f"【{header_prefix}】{unit}"
                    else:
                        full_text = unit
                    if len(re.findall(r"[\u4e00-\u9fff0-9]", full_text)) < 4:
                        continue
                    sec_label = f"{doc_name} / {current_chapter.split()[0]} {sec_id}".strip()
                    candidates.append(
                        CandidateClause(
                            candidate_id=f"C{len(candidates)+1:05d}",
                            doc_name=doc_name,
                            page=page_num,
                            chapter=current_chapter,
                            section=sec_label,
                            text=full_text,
                            raw_text=unit,
                            source_type="table",
                            bbox=tbbox,
                        )
                    )

        # 2. Extract non-table body blocks
        blocks = page.get_text("blocks") or []
        body_lines: List[Tuple[str, Tuple[float, float, float, float]]] = []
        for b in blocks:
            if len(b) < 5 or b[6] != 0:
                continue
            bb = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            if any(_bboxes_overlap(bb, tb, threshold=0.45) for tb in table_bboxes):
                continue
            b_text = str(b[4] or "")
            for ln in b_text.splitlines():
                sln = _clean_whitespace(ln)
                if not sln or _PAGE_NUMBER_ONLY_RE.match(sln):
                    continue
                body_lines.append((sln, bb))

        # Group body lines into logical paragraphs/clauses
        paragraphs: List[Tuple[str, Tuple[float, float, float, float]]] = []
        for sln, bb in body_lines:
            m_chap = _CHAPTER_RE.match(sln)
            if m_chap and not _TOC_LINE_RE.search(sln):
                current_chapter = f"{m_chap.group(1)} {m_chap.group(2).strip()}".strip()
                current_form_title = ""
                continue
            m_sec = _SECTION_RE.match(sln)
            if m_sec:
                current_section = f"{m_sec.group(1)} {m_sec.group(2).strip()}".strip()
                continue
            m_app = _APPENDIX_RE.match(sln)
            if m_app:
                current_section = f"{m_app.group(1)} {m_app.group(2).strip()}".strip()
                continue
            m_form = _FORM_TITLE_RE.match(sln)
            if m_form and ("第八章" in current_chapter or "第九章" in current_chapter or "格式" in current_chapter):
                current_form_title = m_form.group(1).strip()
                continue

            # Merge standalone speaker label ("投标人：" / "法定代表人：") with next line ("（盖单位电子印章）")
            if (
                paragraphs
                and re.fullmatch(r"(投标人|法定代表人|委托代理人|担保人|单位名称)[：:]?", paragraphs[-1][0])
                and ("章" in sln or "签字" in sln)
            ):
                prev_txt, prev_bb = paragraphs[-1]
                merged_bb = (
                    min(prev_bb[0], bb[0]),
                    min(prev_bb[1], bb[1]),
                    max(prev_bb[2], bb[2]),
                    max(prev_bb[3], bb[3]),
                )
                paragraphs[-1] = (prev_txt + sln, merged_bb)
                continue

            # Merge multi-line account info ("投标人基本账户开户名：" + "账号：" + "开户行：")
            if (
                paragraphs
                and ("开户名" in paragraphs[-1][0] or "账号" in paragraphs[-1][0])
                and any(k in sln for k in ("账号", "开户行", "基本账户", "基本帐户"))
            ):
                prev_txt, prev_bb = paragraphs[-1]
                merged_bb = (
                    min(prev_bb[0], bb[0]),
                    min(prev_bb[1], bb[1]),
                    max(prev_bb[2], bb[2]),
                    max(prev_bb[3], bb[3]),
                )
                paragraphs[-1] = (f"{prev_txt} {sln}", merged_bb)
                continue

            is_new_para = bool(
                _LEVEL1_NUM_RE.match(sln)
                or _LEVEL2_NUM_RE.match(sln)
                or _LEVEL3_NUM_RE.match(sln)
                or _NOTE_RE.match(sln)
                or _SEAL_OR_FIELD_START_RE.match(sln)
            )
            if not paragraphs or is_new_para or paragraphs[-1][0].endswith(("。", "；", "：", ":", "！")):
                paragraphs.append((sln, bb))
            else:
                prev_txt, prev_bb = paragraphs[-1]
                merged_bb = (
                    min(prev_bb[0], bb[0]),
                    min(prev_bb[1], bb[1]),
                    max(prev_bb[2], bb[2]),
                    max(prev_bb[3], bb[3]),
                )
                paragraphs[-1] = (prev_txt + sln, merged_bb)

        # Parent-child prefix state machine for body paragraphs
        parent_l1_prefix = ""
        parent_l2_prefix = ""
        for p_txt, p_bb in paragraphs:
            p_txt = _clean_checkbox_options(p_txt)
            if not p_txt:
                continue

            m1 = _LEVEL1_NUM_RE.match(p_txt)
            m2 = _LEVEL2_NUM_RE.match(p_txt)

            if m1:
                current_clause_no = m1.group(1).strip()
                parent_l2_prefix = ""
                if (
                    p_txt.endswith(("：", ":"))
                    and len(p_txt) <= 100
                    and not _SEAL_OR_FIELD_START_RE.match(p_txt)
                ):
                    parent_l1_prefix = p_txt.rstrip("：:")
                    continue
                else:
                    parent_l1_prefix = ""
            elif m2:
                if (
                    p_txt.endswith(("：", ":"))
                    and len(p_txt) <= 100
                    and not _SEAL_OR_FIELD_START_RE.match(p_txt)
                ):
                    parent_l2_prefix = p_txt.rstrip("：:")
                    continue

            active_prefix = " ".join(x for x in (current_form_title, parent_l1_prefix, parent_l2_prefix) if x).strip()
            for sent in _split_long_unit(p_txt):
                if active_prefix and active_prefix not in sent:
                    full_sent = f"【{active_prefix}】{sent}" if current_form_title else f"{active_prefix}：{sent}"
                else:
                    full_sent = sent
                sec_label = f"{doc_name} / {current_chapter.split()[0]} {current_form_title or current_clause_no}".strip()
                candidates.append(
                    CandidateClause(
                        candidate_id=f"C{len(candidates)+1:05d}",
                        doc_name=doc_name,
                        page=page_num,
                        chapter=current_chapter,
                        section=sec_label,
                        text=full_sent,
                        raw_text=sent,
                        source_type="body",
                        bbox=p_bb,
                    )
                )

    return candidates
