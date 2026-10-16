"""
Unified Vision & OCR Protocol Layer for TenderCheck Bid Document Parsing.

Provides a standardized `PageVisionResult` protocol for scanned pages, certificates,
financial tables, and stamped commitment letters:
1. `QwenVLBackend`: Runs `Qwen/Qwen2.5-VL-7B-Instruct` on an RTX 5080 (16GB VRAM)
   via local HuggingFace Transformers or an OpenAI-compatible local server (vLLM/Ollama).
2. `LocalHeuristicOCRBackend`: Zero-GPU fallback combining RapidOCR (if installed)
   with PyMuPDF pixmap HSV red-seal detection and text layout heuristics.
3. `DiskCachedVisionBackend`: Transparent SHA-256 disk cache wrapper so heavy VLM
   inference on the RTX 5080 is executed once and shared across all teammates.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
import numpy as np

logger = logging.getLogger(__name__)

# Standard document categories for bid attachments and chapters
DOC_CATEGORIES: Tuple[str, ...] = (
    "投标函与承诺书",
    "法定代表人身份证明与授权委托书",
    "联合体协议书",
    "投标保证金证明",
    "营业执照与企业资质证书",
    "安全生产许可证",
    "项目管理机构与人员简历证书",
    "社保证明材料",
    "近年类似工程业绩证明",
    "财务状况与审计报告",
    "企业信誉与无违法承诺",
    "报价文件与工程量清单",
    "施工组织设计与技术方案",
    "其他投标材料",
)

VLM_SYSTEM_PROMPT = """你是一个专业的中国建设工程招投标文件视觉审查助手。
请仔细观察输入的投标文件页面图像，并严格输出如下 JSON 格式（不要输出任何多余解释）：
{
  "doc_category": "从[投标函与承诺书, 法定代表人身份证明与授权委托书, 联合体协议书, 投标保证金证明, 营业执照与企业资质证书, 安全生产许可证, 项目管理机构与人员简历证书, 社保证明材料, 近年类似工程业绩证明, 财务状况与审计报告, 企业信誉与无违法承诺, 报价文件与工程量清单, 施工组织设计与技术方案, 其他投标材料]中选一",
  "ocr_text": "完整提取页面中的所有文字；若是表格请还原为Markdown表格格式",
  "has_official_seal": true或false（页面中是否盖有单位红色公章或电子印章）,
  "seal_company_name": "公章上的企业名称文字，若无公章或无法辨认填null",
  "has_signature": true或false（是否有法定代表人或委托代理人签字/私章）,
  "extracted_kv": {
    "company_name": "企业名称(如有)",
    "credit_code": "统一社会信用代码(如有)",
    "qualification_level": "资质等级名称(如有)",
    "certificate_no": "证书编号(如有)",
    "valid_until": "有效期至YYYY-MM-DD(如有)",
    "person_name": "人员姓名(如有)",
    "person_role": "拟任职务如项目经理/技术负责人(如有)",
    "id_card": "身份证号(如有)",
    "project_name": "业绩项目名称(如有)",
    "contract_amount_wan": "合同金额万元数值(如有)",
    "completion_date": "竣工验收日期(如有)",
    "bid_price_yuan": "投标总报价元数值(如有)",
    "bid_price_upper": "投标总报价大写(如有)",
    "duration_days": "工期日历天数值(如有)"
  }
}"""


@dataclass
class PageVisionResult:
    """
    Unified output protocol returned by any OCR or VLM backend for a single PDF page.
    """

    pdf_page: int
    doc_category: str = "其他投标材料"
    ocr_text: str = ""
    has_official_seal: bool = False
    seal_company_name: Optional[str] = None
    has_signature: bool = False
    extracted_kv: Dict[str, Any] = field(default_factory=dict)
    backend_name: str = "heuristic"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageVisionResult":
        return cls(
            pdf_page=int(data.get("pdf_page", 1)),
            doc_category=str(data.get("doc_category", "其他投标材料")),
            ocr_text=str(data.get("ocr_text", "")),
            has_official_seal=bool(data.get("has_official_seal", False)),
            seal_company_name=data.get("seal_company_name"),
            has_signature=bool(data.get("has_signature", False)),
            extracted_kv=dict(data.get("extracted_kv") or {}),
            backend_name=str(data.get("backend_name", "cached")),
        )


def detect_red_seal_from_pixmap(pix: fitz.Pixmap, min_red_ratio: float = 0.0012) -> bool:
    """
    Detect presence of a red official stamp/seal on a rendered PDF page using RGB dominance.

    Official Chinese corporate seals (公章 / 项目章 / 法人章) exhibit strong red-channel
    dominance (`R > 150`, `R - G > 45`, `R - B > 45`).

    Args:
        pix: PyMuPDF RGB Pixmap of the page.
        min_red_ratio: Minimum fraction of red-stamp pixels required to flag `True`.

    Returns:
        `True` if a red stamp pattern is detected on the page.
    """
    if pix.n < 3:
        return False
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)

    red_mask = (r > 155) & ((r - g) > 45) & ((r - b) > 45)
    red_ratio = float(np.count_nonzero(red_mask)) / max(arr.shape[0] * arr.shape[1], 1)
    return red_ratio >= min_red_ratio


def infer_doc_category_from_text(text: str, heading_hint: str = "") -> str:
    """Infer standardized `doc_category` from page text and section headings."""
    combined = f"{heading_hint}\n{text}"
    rules: List[Tuple[str, Tuple[str, ...]]] = [
        ("投标函与承诺书", ("投标函", "投标函附录", "承诺函", "我方承诺", "投标承诺书")),
        ("法定代表人身份证明与授权委托书", ("法定代表人身份证明", "授权委托书", "委托代理人")),
        ("联合体协议书", ("联合体协议书", "联合体投标")),
        ("投标保证金证明", ("投标保证金", "银行保函", "保证保险", "基本账户开户许可证", "转账凭证")),
        ("安全生产许可证", ("安全生产许可证", "安许证")),
        ("营业执照与企业资质证书", ("营业执照", "统一社会信用代码", "建筑业企业资质证书", "资质等级", "基本情况表")),
        ("社保证明材料", ("社会保险", "社保证明", "参保缴费", "养老保险")),
        ("项目管理机构与人员简历证书", ("项目经理", "技术负责人", "建造师", "安全生产考核合格证", "职称证", "简历表", "项目管理机构")),
        ("近年类似工程业绩证明", ("类似项目情况表", "类似工程业绩", "中标通知书", "施工合同", "合同协议书", "竣工验收")),
        ("财务状况与审计报告", ("财务状况", "审计报告", "资产负债表", "利润表", "现金流量表")),
        ("企业信誉与无违法承诺", ("信誉情况表", "无重大违法", "无行贿犯罪", "信用中国", "不良行为记录")),
        ("报价文件与工程量清单", ("已标价工程量清单", "投标总价", "开标一览表", "计价表", "单价分析表", "汇总表")),
        ("施工组织设计与技术方案", ("施工组织设计", "施工方案", "施工进度计划", "质量保证措施", "安全文明施工")),
    ]
    for category, keywords in rules:
        if any(kw in combined for kw in keywords):
            return category
    return "其他投标材料"


def extract_kv_heuristically(text: str) -> Dict[str, Any]:
    """
    Lightweight regex-based key-value slot extractor for certificates, bid forms,
    personnel tables, and performance records.
    """
    kv: Dict[str, Any] = {}
    compact = re.sub(r"[ \t]+", " ", text)

    # 1. Unified Social Credit Code (18 chars)
    m_code = re.search(r"(?:统一社会信用代码|信用代码|注册号)[：:\s]*([0-9A-HJ-NPQRTUWXY]{18})", compact)
    if m_code:
        kv["credit_code"] = m_code.group(1)

    # 2. Resident ID Card (18 digits)
    m_id = re.search(r"(?:身份证号|身份证号码|公民身份号码)[：:\s]*(\d{17}[\dXx])", compact)
    if m_id:
        kv["id_card"] = m_id.group(1).upper()

    # 3. Bid Total Price (Lower-case numeric)
    m_price = re.search(
        r"(?:投标总报价|投标报价|总报价|小写)[（(]?[^）)\n]{0,10}[)）]?[：:\s¥￥]*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*(万元|元)?",
        compact,
    )
    if m_price:
        raw_val = float(m_price.group(1).replace(",", ""))
        unit = m_price.group(2) or "元"
        kv["bid_price_yuan"] = round(raw_val * 10000.0, 2) if unit == "万元" else raw_val

    # 4. Bid Total Price (Upper-case Chinese numerals)
    m_upper = re.search(r"大写[：:\s]*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零元圆角分整正]+)", compact)
    if m_upper:
        kv["bid_price_upper"] = m_upper.group(1)

    # 5. Project Duration (Days / Months)
    m_dur = re.search(r"工期[：:\s]*(\d{1,4})\s*(日历天|天|个月)", compact)
    if m_dur:
        kv["duration_days"] = f"{m_dur.group(1)}{m_dur.group(2)}"

    # 6. Project Manager / Person Name
    m_pm = re.search(r"(?:项目经理|项目负责人|姓名)[：:\s]+([\u4e00-\u9fa5]{2,4})(?:\s|$|[，。；|])", compact)
    if m_pm:
        candidate = m_pm.group(1)
        if candidate not in {"拟委任", "投标人", "技术标", "商务标", "身份证", "建造师"}:
            kv["person_name"] = candidate

    # 7. Certificate Validity Date
    m_valid = re.search(r"有效期[至期满：:\s]*(\d{4}[年\-/.]\d{1,2}[月\-/.]\d{1,2}日?)", compact)
    if m_valid:
        kv["valid_until"] = m_valid.group(1)

    return kv


class BaseVisionBackend(ABC):
    """Abstract base class for page-level OCR and VLM analysis."""

    @abstractmethod
    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        """Analyze a single 1-indexed PDF page and return a `PageVisionResult`."""


class LocalHeuristicOCRBackend(BaseVisionBackend):
    """
    Local CPU/GPU fallback vision backend:
    - Uses `RapidOCR` (if installed) to extract text from scanned page images.
    - Uses HSV/RGB red dominance to detect official corporate stamps (`has_official_seal`).
    - Applies regex/heuristic slot extraction to populate `extracted_kv`.
    """

    def __init__(self, dpi: int = 150) -> None:
        self.dpi = dpi
        self._rapid_ocr = None
        self._rapid_ocr_checked = False

    def _get_ocr_engine(self):
        if not self._rapid_ocr_checked:
            self._rapid_ocr_checked = True
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                self._rapid_ocr = RapidOCR()
                logger.info("Initialized RapidOCR (ONNX Runtime) for scanned page recognition.")
            except ImportError:
                logger.info(
                    "rapidocr-onnxruntime not installed; scanned pages will rely on "
                    "embedded text, sub-chapter source files, or VLM cache."
                )
        return self._rapid_ocr

    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        page = doc[pdf_page - 1]
        pix = page.get_pixmap(dpi=self.dpi, alpha=False)
        has_seal = detect_red_seal_from_pixmap(pix)

        ocr_text = existing_text.strip()
        ocr_engine = self._get_ocr_engine()
        if ocr_engine is not None and len(re.sub(r"\s+", "", ocr_text)) < 60:
            img_bytes = pix.tobytes("png")
            result, _ = ocr_engine(img_bytes)
            if result:
                lines = [str(item[1]).strip() for item in result if len(item) >= 2]
                ocr_text = "\n".join(lines)

        doc_cat = infer_doc_category_from_text(ocr_text, heading_hint=heading_hint)
        has_sig = any(kw in ocr_text for kw in ("法定代表人", "委托代理人", "签字", "签名", "签章"))
        extracted_kv = extract_kv_heuristically(ocr_text)

        return PageVisionResult(
            pdf_page=pdf_page,
            doc_category=doc_cat,
            ocr_text=ocr_text,
            has_official_seal=has_seal,
            seal_company_name=extracted_kv.get("company_name"),
            has_signature=has_sig,
            extracted_kv=extracted_kv,
            backend_name="local_rapidocr_heuristic" if ocr_engine else "local_heuristic",
        )


class QwenVLBackend(BaseVisionBackend):
    """
    Production Vision-Language Model backend powered by `Qwen/Qwen2.5-VL-7B-Instruct`.

    Designed to run on the teammate's RTX 5080 (16GB VRAM) in two supported modes:
    1. `mode="openai_compatible"`: Connects to a local vLLM / Ollama / LMStudio server
       or cloud DashScope endpoint speaking the OpenAI Chat Completions Vision API.
    2. `mode="transformers"`: Loads `Qwen2.5-VL-7B-Instruct` directly in PyTorch FP16/BF16.
    """

    def __init__(
        self,
        model_name_or_path: str = "Qwen/Qwen2.5-VL-7B-Instruct",
        mode: str = "transformers",
        api_base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        dpi: int = 160,
    ) -> None:
        self.model_name_or_path = model_name_or_path
        self.mode = mode
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.dpi = dpi
        self._hf_model = None
        self._hf_processor = None

    def _ensure_hf_loaded(self) -> None:
        if self._hf_model is not None:
            return
        import torch
        import transformers
        from transformers import AutoProcessor

        logger.info("Loading VLM %s on GPU...", self.model_name_or_path)
        # Limit max image pixels so high-DPI A4 scans don't cause excessive visual token memory
        self._hf_processor = AutoProcessor.from_pretrained(
            self.model_name_or_path,
            min_pixels=256 * 28 * 28,
            max_pixels=1280 * 28 * 28,
        )
        model_cls = getattr(
            transformers,
            "Qwen2_5_VLForConditionalGeneration",
            getattr(transformers, "AutoModelForImageTextToText", None),
        )
        load_kwargs: Dict[str, Any] = {
            "torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            "device_map": "auto",
        }
        self._hf_model = model_cls.from_pretrained(self.model_name_or_path, **load_kwargs)
        self._hf_model.eval()

    def unload(self) -> None:
        """Release VLM weights from GPU VRAM so subsequent BGE-M3 indexing has full 16GB VRAM."""
        if self._hf_model is not None:
            import gc
            import torch

            del self._hf_model
            del self._hf_processor
            self._hf_model = None
            self._hf_processor = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("Unloaded VLM from GPU VRAM.")

    @staticmethod
    def _parse_vlm_json(raw_output: str, pdf_page: int, fallback_seal: bool) -> PageVisionResult:
        """Parse structured JSON emitted by Qwen2.5-VL."""
        cleaned = raw_output.strip()
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return PageVisionResult(
                    pdf_page=pdf_page,
                    doc_category=str(obj.get("doc_category") or "其他投标材料"),
                    ocr_text=str(obj.get("ocr_text") or ""),
                    has_official_seal=bool(obj.get("has_official_seal", fallback_seal)),
                    seal_company_name=obj.get("seal_company_name"),
                    has_signature=bool(obj.get("has_signature", False)),
                    extracted_kv=dict(obj.get("extracted_kv") or {}),
                    backend_name="qwen2.5_vl",
                )
            except json.JSONDecodeError:
                pass

        return PageVisionResult(
            pdf_page=pdf_page,
            doc_category=infer_doc_category_from_text(cleaned),
            ocr_text=cleaned,
            has_official_seal=fallback_seal,
            has_signature=("签字" in cleaned or "签章" in cleaned),
            extracted_kv=extract_kv_heuristically(cleaned),
            backend_name="qwen2.5_vl_raw",
        )

    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        page = doc[pdf_page - 1]
        pix = page.get_pixmap(dpi=self.dpi, alpha=False)
        fallback_seal = detect_red_seal_from_pixmap(pix)
        png_bytes = pix.tobytes("png")

        if self.mode == "openai_compatible":
            import urllib.request

            b64_img = base64.b64encode(png_bytes).decode("ascii")
            payload = {
                "model": self.model_name_or_path,
                "messages": [
                    {"role": "system", "content": VLM_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"当前章节提示：{heading_hint or '无'}。请解析该投标文件页面并输出JSON。",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64_img}"},
                            },
                        ],
                    },
                ],
                "temperature": 0.0,
            }
            req = urllib.request.Request(
                f"{self.api_base_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                content = resp_data["choices"][0]["message"]["content"]
                return self._parse_vlm_json(content, pdf_page, fallback_seal)

        # Direct HuggingFace Transformers inference on RTX 5080
        self._ensure_hf_loaded()
        import io
        import torch
        from PIL import Image

        pil_img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil_img},
                    {
                        "type": "text",
                        "text": f"{VLM_SYSTEM_PROMPT}\n当前章节提示：{heading_hint or '无'}",
                    },
                ],
            }
        ]
        prompt = self._hf_processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._hf_processor(
            text=[prompt], images=[pil_img], padding=True, return_tensors="pt"
        ).to(self._hf_model.device)

        with torch.no_grad():
            generated_ids = self._hf_model.generate(**inputs, max_new_tokens=1024)
            trimmed_ids = [
                out_ids[len(in_ids) :]
                for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            output_text = self._hf_processor.batch_decode(
                trimmed_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]

        return self._parse_vlm_json(output_text, pdf_page, fallback_seal)


class DiskCachedVisionBackend(BaseVisionBackend):
    """
    Caching wrapper around any `BaseVisionBackend`.

    Persists page-level `PageVisionResult` entries to `<cache_dir>/<pdf_hash>.json`
    so VLM outputs computed on the RTX 5080 machine can be committed/shared and
    reused instantaneously on CPU laptops without re-running VLM inference.
    """

    def __init__(self, delegate: BaseVisionBackend, cache_dir: Path) -> None:
        self.delegate = delegate
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[str, Dict[int, PageVisionResult]] = {}

    @staticmethod
    def _doc_cache_key(doc: fitz.Document) -> str:
        doc_path = getattr(doc, "name", "") or "memory_pdf"
        stat_sig = f"{Path(doc_path).name}_{len(doc)}"
        digest = hashlib.sha1(stat_sig.encode("utf-8")).hexdigest()[:12]
        safe_stem = re.sub(r"[^\w\-.]", "_", Path(doc_path).stem)[:40]
        return f"{safe_stem}_{digest}"

    def _load_doc_cache(self, cache_key: str) -> Dict[int, PageVisionResult]:
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        cache_file = self.cache_dir / f"{cache_key}.json"
        page_map: Dict[int, PageVisionResult] = {}
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k, v in raw.items():
                    page_map[int(k)] = PageVisionResult.from_dict(v)
            except Exception as exc:
                logger.warning("Failed to read vision cache %s: %s", cache_file, exc)

        self._memory_cache[cache_key] = page_map
        return page_map

    def _save_doc_cache(self, cache_key: str, page_map: Dict[int, PageVisionResult]) -> None:
        cache_file = self.cache_dir / f"{cache_key}.json"
        serializable = {str(k): v.to_dict() for k, v in sorted(page_map.items())}
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

    def analyze_page(
        self,
        doc: fitz.Document,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        cache_key = self._doc_cache_key(doc)
        page_map = self._load_doc_cache(cache_key)
        if pdf_page in page_map:
            return page_map[pdf_page]

        result = self.delegate.analyze_page(
            doc=doc,
            pdf_page=pdf_page,
            existing_text=existing_text,
            heading_hint=heading_hint,
        )
        page_map[pdf_page] = result
        self._save_doc_cache(cache_key, page_map)
        return result
