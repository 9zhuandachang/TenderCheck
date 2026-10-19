"""Exercise cache preservation, resume and failed-output rejection without GPU weights."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _rag_experiment_utils import (directory_hashes, read_json, recover_completed_attempt,
                                   require_separate_directories, write_json)
from repair_rag_pages import main
from src.rag.vision import DiskCachedVisionBackend, PageVisionResult


class RepairWorkflowTests(unittest.TestCase):
    def test_reject_overlapping_directories(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            for first, second in ((folder, folder), (folder, Path(folder) / "child"),
                                  (Path(folder) / "child", folder)):
                with self.assertRaises(ValueError):
                    require_separate_directories(first, second)

    def test_incomplete_saved_ocr_is_not_repaired(self):
        audit = dict(pdf_page=1, ocr_attempts=[dict(box=[0, 0, 10, 10], text="partial", finish_reason="length")])
        self.assertIsNone(recover_completed_attempt(audit, ""))

    def test_normalize_resume_and_preserve_source(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            root = Path(folder)
            pdf, source, target, output = root / "synthetic.pdf", root / "source", root / "target", root / "output"
            with fitz.open() as doc:
                doc.new_page()
                doc.new_page()
                doc.save(pdf)
            with fitz.open(pdf) as doc:
                key = DiskCachedVisionBackend._doc_cache_key(doc)
            success = PageVisionResult(pdf_page=1, doc_category="其他投标材料", ocr_text="原成功页面",
                                       backend_name="qwen2.5_vl").to_dict()
            raw = PageVisionResult(pdf_page=2, doc_category="其他投标材料", ocr_text="未完成",
                                   backend_name="qwen2.5_vl_raw").to_dict()
            write_json(source / f"{key}.json", {"1": success, "2": raw})
            before = directory_hashes(source)
            args = ["--bid_input", str(pdf), "--source_cache_dir", str(source),
                    "--output_cache_dir", str(target), "--output_dir", str(output), "--normalize_only"]
            self.assertEqual(main(args + ["--dry_run"]), 0)
            self.assertFalse(target.exists())
            # Missing audited output must remain failed, never counted as a recovery.
            self.assertEqual(main(args), 2)
            metadata = dict(doc_category="授权委托书", has_official_seal=False, has_signature=True,
                            seal_company_name=None, person_name="合成示例")
            write_json(output / "page_recovery" / f"{key}_page0002.json", dict(
                pdf_page=2, status="failed", rotation_degrees_ccw=0,
                ocr_attempts=[dict(box=[0, 0, 10, 10], text="合成正文", finish_reason="eos")],
                metadata_attempts=[dict(text=json.dumps(metadata, ensure_ascii=False), finish_reason="eos")]))
            self.assertEqual(main(args), 0)
            recovered = read_json(target / f"{key}.json")
            self.assertEqual(recovered["1"], success)
            self.assertEqual(recovered["2"]["ocr_text"], "合成正文")
            self.assertEqual(recovered["2"]["extracted_kv"], {"person_name": "合成示例"})
            target_hashes = directory_hashes(target)
            self.assertEqual(main(args), 0)
            self.assertEqual(directory_hashes(target), target_hashes)
            self.assertEqual(directory_hashes(source), before)
            progress = read_json(output / "recovery_progress.json")
            self.assertEqual((progress["preserved"], progress["recovered"], progress["failed"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
