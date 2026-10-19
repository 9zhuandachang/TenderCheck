"""Selectively recover raw Qwen cache entries, preserving the source cache."""
import argparse
import shutil
import time
from pathlib import Path

from _rag_experiment_utils import (directory_hashes, read_json, record_sha256,
                                   recover_completed_attempt, require_separate_directories,
                                   set_offline, write_json)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bid_input", type=Path, required=True)
    parser.add_argument("--source_cache_dir", type=Path, required=True)
    parser.add_argument("--output_cache_dir", type=Path, required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    parser.add_argument("--vlm_model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--vlm_quantization", choices=["none", "int8", "nf4"], default="nf4")
    parser.add_argument("--orientation_plan", type=Path,
                        help="JSON list: cache (cache filename without .json), page, rotation_degrees_ccw.")
    parser.add_argument("--normalize_only", action="store_true", help="Reuse completed audited outputs without loading Qwen.")
    parser.add_argument("--dry_run", action="store_true", help="List workload without changing files or loading Qwen.")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    require_separate_directories(args.source_cache_dir, args.output_cache_dir)
    require_separate_directories(args.source_cache_dir, args.output_dir)
    require_separate_directories(args.output_cache_dir, args.output_dir)
    set_offline(args.offline)
    import fitz
    from src.rag.vision import DiskCachedVisionBackend, QwenVLBackend

    if not args.source_cache_dir.is_dir():
        parser.error("source_cache_dir does not exist")
    paths = [args.bid_input] if args.bid_input.is_file() else sorted(
        p for p in args.bid_input.rglob("*") if p.suffix.lower() == ".pdf")
    if not paths:
        parser.error("No source PDFs found")
    pdfs = {}
    for path in paths:
        with fitz.open(path) as doc:
            key = DiskCachedVisionBackend._doc_cache_key(doc)
        if key in pdfs:
            raise ValueError(f"Ambiguous PDF cache key: {key}")
        pdfs[key] = path
    rotations = {}
    for row in read_json(args.orientation_plan) if args.orientation_plan else []:
        angle = int(row["rotation_degrees_ccw"])
        if angle not in (0, 90, 180, 270):
            raise ValueError("Invalid rotation; use counterclockwise 0/90/180/270")
        rotations[(row["cache"], str(row["page"]))] = angle

    source_hashes = directory_hashes(args.source_cache_dir)
    sources = {p.name: read_json(p) for p in sorted(args.source_cache_dir.glob("*.json"))}
    jobs, preserved = [], []
    for name, original in sources.items():
        for page, record in original.items():
            if record.get("backend_name") == "qwen2.5_vl_raw":
                if Path(name).stem not in pdfs:
                    raise ValueError(f"Missing PDF for failed cache: {name}")
                jobs.append((name, page))
            else:
                preserved.append({"cache": name, "page": page, "sha256": record_sha256(record)})
    print(f"WORKLOAD preserved={len(preserved)} failed={len(jobs)}", flush=True)
    if args.dry_run:
        return 0
    args.output_cache_dir.mkdir(parents=True, exist_ok=True)
    audits = args.output_dir / "page_recovery"
    audits.mkdir(parents=True, exist_ok=True)
    for name, original in sources.items():
        dest = args.output_cache_dir / name
        if not dest.exists():
            write_json(dest, original)

    def verify_preservation():
        if directory_hashes(args.source_cache_dir) != source_hashes:
            raise RuntimeError("Source cache changed during recovery")
        for row in preserved:
            current = read_json(args.output_cache_dir / row["cache"])[row["page"]]
            if record_sha256(current) != row["sha256"]:
                raise RuntimeError(f"Previously successful page changed: {row['cache']}:{row['page']}")

    verify_preservation()
    write_json(args.output_dir / "recovery_manifest.json", dict(
        source_file_sha256=source_hashes, preserved_pages=preserved,
        failed_pages=[{"cache": name, "page": page} for name, page in jobs],
        settings={"model": args.vlm_model, "quantization": args.vlm_quantization,
                  "normalize_only": args.normalize_only, "orientation_plan": str(args.orientation_plan)}))
    progress = dict(status="running", started_at=time.time(), total=len(jobs), preserved=len(preserved),
                    completed=0, recovered=0, failed=0)
    backend = None
    try:
        for name, page in jobs:
            path = pdfs[Path(name).stem]
            angle = rotations.get((Path(name).stem, page), 0)
            dest = args.output_cache_dir / name
            current = read_json(dest)
            audit_path = audits / f"{Path(name).stem}_page{int(page):04d}.json"
            audit = read_json(audit_path) if audit_path.exists() else {}
            rotation_matches = audit.get("rotation_degrees_ccw", 0) == angle
            if current[page].get("backend_name") == "qwen2.5_vl_recovered" and audit.get("status") == "recovered" and rotation_matches:
                progress["recovered"] += 1
            else:
                result = recover_completed_attempt(audit, path.stem) if audit and rotation_matches else None
                if result is None and not args.normalize_only:
                    if backend is None:
                        backend = QwenVLBackend(args.vlm_model, quantization=args.vlm_quantization)
                    if audit_path.exists():
                        history = audits / "history"
                        history.mkdir(exist_ok=True)
                        shutil.copy2(audit_path, history / f"{audit_path.stem}_{time.time_ns()}.json")
                    started = time.perf_counter()
                    with fitz.open(path) as doc:
                        result, audit = backend.analyze_page_recovery(
                            doc, int(page), heading_hint=path.stem, rotation_degrees=angle,
                            crop_main_scan=bool(angle))
                    audit.update(source_pdf=str(path.resolve()), total_seconds=time.perf_counter() - started)
                if result is not None:
                    audit.update(source_cache=Path(name).stem, original_page_sha256=record_sha256(sources[name][page]))
                    write_json(audit_path, audit)
                    current[page] = result.to_dict()
                    write_json(dest, current)
                    progress["recovered"] += 1
                else:
                    if audit:
                        write_json(audit_path, audit)
                    progress["failed"] += 1
            progress["completed"] += 1
            progress["updated_at"] = time.time()
            write_json(args.output_dir / "recovery_progress.json", progress)
            print(f"RECOVERY {progress['completed']}/{len(jobs)} recovered={progress['recovered']} failed={progress['failed']}", flush=True)
        verify_preservation()
        progress.update(status="complete" if not progress["failed"] else "complete_with_failures",
                        source_unchanged=True, preserved_pages_unchanged=True)
    except Exception as exc:
        progress.update(status="failed", error=str(exc))
        raise
    finally:
        if backend is not None:
            backend.unload()
        progress["updated_at"] = time.time()
        write_json(args.output_dir / "recovery_progress.json", progress)
    return 0 if not progress["failed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
