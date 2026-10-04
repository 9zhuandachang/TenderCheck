"""
organize_projects.py - Standardize and classify civil & hydraulic engineering bid project folders.

Zero-Deletion Principle:
  All unneeded / proprietary / drawing files are safely moved to '04_不需要的文件/' - NO files are deleted.

Standard 4-tier Directory Layout:
  01_招标文件/          - 招标文件正文、招标公告、答疑与补遗、工程量清单、初步设计
  02_投标文件/          - 投标文件全本PDF、分章节文件/ (投标函、资审、技术标/施组、资质证件等)
  03_参考与归档/        - 人工评审单、投标报告、开标记录表、投标人名单、未中标说明等
  04_不需要的文件/      - CAD蓝图/施工图压缩包、招投标专有加密工程包、中间过程Word草稿等
"""

import os
import sys
import shutil
import argparse
import json
import re
from pathlib import Path
from datetime import datetime

# Proprietary software extensions across Hefei, Ma'anshan, Wuhu, Xuancheng, Jiangsu, etc.
PROPRIETARY_EXTS = {
    '.etbp', '.hfzf', '.nhftf', '.hftf',
    '.maszf', '.nmastf', '.mastf', '.masslzb', '.massltb',
    '.whzf', '.18whzf', '.whslzb', '.whsltb', '.18whcf', '.n18whtf', '.18whtf',
    '.nxctf', '.xctf', '.xcglzb', '.xczf',
    '.emahx', '.tbcz', '.tbwj', '.zbwj', '.czzb'
}

STANDARD_DIRS = ['01_招标文件', '02_投标文件', '03_参考与归档', '04_不需要的文件']


def is_already_organized(project_path: Path) -> bool:
    """Check if the project directory is already fully organized into the 4 standard folders."""
    existing_dirs = [d.name for d in project_path.iterdir() if d.is_dir()]
    if all(sd in existing_dirs for sd in STANDARD_DIRS[:2]):
        loose_items = [
            f for f in project_path.iterdir()
            if f.name not in STANDARD_DIRS and f.name != 'file_movement_manifest.json'
        ]
        return len(loose_items) == 0
    return False


def classify_file(file_path: Path, project_root: Path) -> Path:
    """
    Classify a single file into its target relative destination path under project_root.
    Returns the target relative Path (e.g., Path('01_招标文件/招标文件正文.pdf')).
    """
    name = file_path.name
    ext = file_path.suffix.lower()
    parent_names = [p.name for p in file_path.parents if p != project_root]
    parent_str = '/'.join(parent_names)

    # 1. 判定 04_不需要的文件
    # 1.1 平台专有加密包
    if ext in PROPRIETARY_EXTS:
        return Path('04_不需要的文件') / name
    
    # 1.2 施工图纸 / CAD 压缩包 / CAD 原件
    if any(k in name for k in ['图纸', '施工图', 'CAD', '绿化提升']) and ext in ['.zip', '.rar', '.7z', '.dwg']:
        return Path('04_不需要的文件') / name
    
    # 1.3 控制价压缩包 / 造价清单数据包
    if any(k in name for k in ['控制价', '清单']) and ext in ['.zip', '.rar', '.7z']:
        return Path('04_不需要的文件') / name
    
    # 1.4 Office 临时锁定文件
    if name.startswith('~$'):
        return Path('04_不需要的文件') / name
    
    # 1.5 极小的封面草稿文件 (小于 50KB 的 doc/docx 封面)
    if ext in ['.doc', '.docx'] and any(k in name for k in ['封面', '目录']) and file_path.stat().st_size < 50 * 1024:
        return Path('04_不需要的文件') / name

    # 1.6 清单子项拆分 Excel (非开标汇总表)
    if ext in ['.xls', '.xlsx'] and not any(k in name for k in ['开标', '评审', '汇总', '得分', '名单', '投标报告']):
        if any(k in name for k in ['清单', '工程', '管道', '泵房', '水池', '水厂', '土石方', '电气', '自控']):
            return Path('04_不需要的文件') / name

    # 2. 判定 01_招标文件
    # 2.1 答疑、补遗、变更、澄清
    if any(k in name for k in ['补疑', '答疑', '变更', '澄清']) and ext == '.pdf':
        return Path('01_招标文件') / '答疑与补遗' / name
    
    # 2.2 招标文件正文、公告、清单、初步设计
    if '招标文件' in name and ext == '.pdf':
        if '正文' in name or name.endswith('（招标文件）.pdf') or name == '招标文件.pdf':
            return Path('01_招标文件') / '招标文件正文.pdf'
        return Path('01_招标文件') / name
    
    if '招标公告' in name and ext == '.pdf':
        return Path('01_招标文件') / '招标公告.pdf'
    
    if any(k in name for k in ['工程量清单', '清单']) and ext == '.pdf':
        return Path('01_招标文件') / name

    if any(k in name for k in ['初步设计', '勘察报告', '地质勘察', '招标文件技术标准']) and ext == '.pdf':
        return Path('01_招标文件') / name

    # 3. 判定 03_参考与归档
    if any(k in name for k in ['评审单', '投标报告', '开标记录', '开标', '未中标', '中标', '自营化', '复盘', '情况说明', '名单', '得分']):
        return Path('03_参考与归档') / name
    
    # 如果父目录就是标前资料/参考归档，且是图片
    if any('标前' in p or '评审' in p for p in parent_names) and ext in ['.jpg', '.jpeg', '.png']:
        return Path('03_参考与归档') / name

    # 4. 判定 02_投标文件
    # 4.1 投标文件最终完整版全本 (体积较大 > 15MB 或直接位于根目录且名称带投标文件)
    is_root_level = (file_path.parent == project_root)
    if '投标文件' in name and ext == '.pdf':
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 15.0 or (is_root_level and not any(k in name for k in ['资审', '技术标', '商务标', '清单'])):
            return Path('02_投标文件') / name

    # 4.2 投标人资质证件 (企业名称命名的证件PDF，如 安徽华水建设有限公司.pdf)
    if any(k in name for k in ['公司', '资质', '执照', '许可证', '证书']) and ext == '.pdf':
        return Path('02_投标文件') / '分章节文件' / name
    
    # 4.3 分章节文件 (位于软件版/资审/施组目录下，或者文件名包含章节关键词)
    chapter_keywords = [
        '封面', '投标函', '标函', '法定', '授权', '联合体', '保证金', '机构', '组成表',
        '分包', '资审', '资格', '基本情况', '财务', '业绩', '信誉', '简历',
        '承诺', '其他材料', '其他资料', '技术标', '施组', '施工组织', '说明文件',
        '索引', '报价', '工资', '扫描件', '原件', '复印件', '商务文件', '方案', '维保'
    ]
    in_chapter_folder = any(k in parent_str for k in ['资审', '软件', '施组', '报价', '分章节', '商务', '技术', '维保'])
    if ext in ['.pdf', '.doc', '.docx']:
        if in_chapter_folder or any(k in name for k in chapter_keywords):
            return Path('02_投标文件') / '分章节文件' / name

    # 5. 兜底回退 (Fallback): 未知非文本或非标文件统一送入 04_不需要的文件，确保绝对零丢失
    return Path('04_不需要的文件') / name


def organize_project(project_path: Path, dry_run: bool = False) -> dict:
    """Organize a single project directory."""
    project_path = project_path.resolve()
    print(f"\n==================================================")
    print(f"处理项目: {project_path.name}")
    print(f"==================================================")

    if is_already_organized(project_path):
        print(f"  [跳过] 该项目已处于规范的四分类结构中。")
        return {'status': 'skipped', 'project': project_path.name}

    all_files = [f for f in project_path.rglob('*') if f.is_file() and f.name != 'file_movement_manifest.json']
    if not all_files:
        print(f"  [跳过] 项目目录为空。")
        return {'status': 'empty', 'project': project_path.name}

    moves = []
    category_counts = {sd: 0 for sd in STANDARD_DIRS}
    
    for f in all_files:
        target_rel = classify_file(f, project_path)
        src_rel = f.relative_to(project_path)
        
        main_cat = target_rel.parts[0]
        if main_cat in category_counts:
            category_counts[main_cat] += 1
            
        moves.append({
            'source_rel': str(src_rel),
            'target_rel': str(target_rel),
            'size_bytes': f.stat().st_size
        })

    # Print summary of planned moves
    print(f"  待分类文件总数: {len(moves)}")
    for cat, count in category_counts.items():
        print(f"    - {cat}: {count} 个文件")

    if dry_run:
        print("\n  [DRY-RUN 模式] 计划执行的移动操作清单:")
        for m in moves[:15]:
            print(f"    {m['source_rel']} -> {m['target_rel']}")
        if len(moves) > 15:
            print(f"    ... 以及其余 {len(moves) - 15} 个文件")
        return {'status': 'dry_run', 'project': project_path.name, 'moves': moves}

    # Execute moves safely
    manifest_records = []
    for m in moves:
        src = project_path / m['source_rel']
        dst = project_path / m['target_rel']
        
        if src.resolve() == dst.resolve():
            continue
            
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Avoid clobbering if a file with same name exists at destination
        if dst.exists():
            stem = dst.stem
            suffix = dst.suffix
            counter = 1
            while dst.exists():
                dst = dst.parent / f"{stem}_{counter}{suffix}"
                counter += 1
                
        shutil.move(str(src), str(dst))
        manifest_records.append({
            'source': m['source_rel'],
            'dest': str(dst.relative_to(project_path)),
            'timestamp': datetime.now().isoformat()
        })

    # Clean up empty parent directories left behind
    for root, dirs, files in os.walk(project_path, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            if dir_path.name not in STANDARD_DIRS:
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                except OSError:
                    pass

    # Save manifest for auditing and rollback
    manifest_path = project_path / 'file_movement_manifest.json'
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest_records, f, ensure_ascii=False, indent=2)

    print(f"  [完成] 成功规范化整理，已写入审计清单: file_movement_manifest.json")
    return {'status': 'success', 'project': project_path.name, 'moves_count': len(manifest_records)}


def find_all_projects(base_dir: Path) -> list:
    """Find all individual project directories across 2022-2026."""
    projects = []
    for year_dir in sorted(base_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.endswith('年投标项目'):
            continue
        for child in sorted(year_dir.iterdir()):
            if not child.is_dir():
                continue
            # If it's a batch folder like 202601, 202602
            if re.match(r'^\d{6}$', child.name):
                for p in sorted(child.iterdir()):
                    if p.is_dir():
                        projects.append(p)
            else:
                projects.append(child)
    return projects


def rollback_project(project_path: Path):
    """Roll back file movements using file_movement_manifest.json."""
    manifest_path = project_path / 'file_movement_manifest.json'
    if not manifest_path.exists():
        print(f"错误: 未找到审计清单 {manifest_path}，无法回滚。")
        return
    with open(manifest_path, 'r', encoding='utf-8') as f:
        records = json.load(f)
    print(f"正在回滚 {len(records)} 个文件...")
    for r in reversed(records):
        src = project_path / r['dest']
        dst = project_path / r['source']
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    manifest_path.unlink()
    print("回滚完成。")


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="工程项目招投标文件夹标准化整理工具 (Zero-Deletion)")
    parser.add_argument('--project_dir', type=str, help="单个项目文件夹路径")
    parser.add_argument('--year_dir', type=str, help="某年度文件夹路径 (如 2023年投标项目)")
    parser.add_argument('--all', action='store_true', help="处理全库所有年份的全部工程项目")
    parser.add_argument('--dry-run', action='store_true', help="模拟运行模式，不实际移动文件")
    parser.add_argument('--rollback', action='store_true', help="根据 manifest 回滚还原")
    args = parser.parse_args()

    base_dir = Path('data_extracted')

    if args.project_dir:
        p = Path(args.project_dir)
        if args.rollback:
            rollback_project(p)
        else:
            organize_project(p, dry_run=args.dry_run)
    elif args.year_dir:
        yp = Path(args.year_dir)
        # Check if year_dir contains batch folders like 202601
        for child in sorted(yp.iterdir()):
            if not child.is_dir(): continue
            if re.match(r'^\d{6}$', child.name):
                for p in sorted(child.iterdir()):
                    if p.is_dir():
                        organize_project(p, dry_run=args.dry_run)
            else:
                organize_project(child, dry_run=args.dry_run)
    elif args.all:
        projects = find_all_projects(base_dir)
        print(f"全库共扫描到 {len(projects)} 个实际工程项目。")
        for p in projects:
            organize_project(p, dry_run=args.dry_run)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
