import json, sys, os, collections
sys.stdout.reconfigure(encoding='utf-8')

LOCAL_ANNOTATIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'SystemCode', 'data', 'annotations')
GDRIVE = LOCAL_ANNOTATIONS if os.path.exists(LOCAL_ANNOTATIONS) else r'G:\我的云端硬盘\tender system'
FOLDERS = ['2022', '2025', '2026-03', '2026_01']
REQUIRED_KEYS = ['ID', 'Page', 'Section', 'Requirement Type', 'Requirement']

# Valid types per TenderCheck_Annotation_Guideline_v1_3.md Section 2 (8 types)
VALID_TYPES = {
    'Qualification', 'Document', 'Technical', 'Commercial',
    'Schedule', 'Consistency', 'Integrity', 'Scoring'
}

all_types = collections.Counter()
all_keys_seen = set()
files_checked = 0
errors = []
warnings = []
per_file_stats = []

for folder in FOLDERS:
    folder_path = os.path.join(GDRIVE, folder)
    if not os.path.exists(folder_path):
        continue
    for fname in sorted(os.listdir(folder_path)):
        if not fname.endswith('.json') or fname == 'desktop.ini':
            continue
        fpath = os.path.join(folder_path, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            errors.append(f"PARSE ERROR [{fname}]: {e}")
            continue

        if not isinstance(data, list):
            errors.append(f"FORMAT ERROR [{fname}]: root must be a list, got {type(data).__name__}")
            continue

        file_errors = []
        file_warnings = []
        type_dist = collections.Counter()
        page_str_count = 0
        short_req_count = 0
        id_gap_count = 0

        for i, item in enumerate(data):
            if not isinstance(item, dict):
                file_errors.append(f"  Item {i}: not a dict")
                continue

            all_keys_seen.update(item.keys())

            # Check required keys
            for k in REQUIRED_KEYS:
                if k not in item:
                    file_errors.append(f"  Item {i} [{item.get('ID','?')}]: missing key '{k}'")
                elif not str(item[k]).strip():
                    file_errors.append(f"  Item {i} [{item.get('ID','?')}]: empty value for '{k}'")

            # Check Requirement Type validity
            rt = item.get('Requirement Type', '')
            type_dist[rt] += 1
            all_types[rt] += 1
            if rt not in VALID_TYPES:
                file_warnings.append(f"  Item {i} [{item.get('ID','?')}]: unknown Requirement Type '{rt}'")

            # Check Page value and data type
            raw_page = item.get('Page')
            if isinstance(raw_page, str):
                page_str_count += 1
            page_str = str(raw_page or '')
            if page_str and not page_str.strip().lstrip('-').isdigit():
                file_warnings.append(f"  Item {i} [{item.get('ID','?')}]: Page='{page_str}' is not a plain integer")
            elif page_str.strip().lstrip('-').isdigit() and int(page_str.strip()) <= 0:
                file_warnings.append(f"  Item {i} [{item.get('ID','?')}]: Page={page_str} is <= 0")

            # Check ID format and sequential order (T0001, T0002...)
            id_val = str(item.get('ID', ''))
            expected_id = f"T{i+1:04d}"
            if id_val and not id_val.startswith('T'):
                file_warnings.append(f"  Item {i}: ID='{id_val}' does not start with 'T'")
            elif id_val != expected_id:
                id_gap_count += 1
                if id_gap_count <= 3:
                    file_warnings.append(f"  Item {i}: ID='{id_val}' (expected '{expected_id}')")

            # Check Requirement length
            req_text = str(item.get('Requirement', '')).strip()
            if 0 < len(req_text) < 10:
                short_req_count += 1
                file_warnings.append(f"  Item {i} [{id_val}]: Very short Requirement ({len(req_text)} chars): '{req_text}'")

        per_file_stats.append({
            'folder': folder,
            'file': fname,
            'count': len(data),
            'types': dict(type_dist),
            'errors': len(file_errors),
            'warnings': len(file_warnings),
            'page_str_count': page_str_count,
            'short_req_count': short_req_count,
            'id_gap_count': id_gap_count,
        })
        errors.extend([f"[{fname}] {e}" for e in file_errors])
        warnings.extend([f"[{fname}] {w}" for w in file_warnings])
        files_checked += 1

# ─── REPORT ───────────────────────────────────────────────────
print("=" * 70)
print(f"ANNOTATION QC REPORT  |  Files checked: {files_checked}")
print("=" * 70)

print(f"\n[1] ALL KEYS FOUND ACROSS FILES:")
print(f"    {sorted(all_keys_seen)}")

print(f"\n[2] REQUIREMENT TYPE DISTRIBUTION (all files combined):")
for t, cnt in all_types.most_common():
    valid_flag = "OK" if t in VALID_TYPES else "UNKNOWN ⚠️"
    print(f"    {t:20} : {cnt:5d}  [{valid_flag}]")

print(f"\n[3] PER-FILE SUMMARY:")
print(f"    {'Folder':<12} {'File':<55} {'Items':>6} {'Errors':>7} {'Warns':>6}")
print(f"    {'-'*95}")
for s in per_file_stats:
    fname_short = s['file'][:54]
    err_flag = ' ⚠️' if s['errors'] else ''
    print(f"    {s['folder']:<12} {fname_short:<55} {s['count']:>6} {s['errors']:>6}{err_flag} {s['warnings']:>5}")

print(f"\n[4] HARD ERRORS ({len(errors)}):")
if errors:
    for e in errors[:30]:
        print(f"    {e}")
    if len(errors) > 30:
        print(f"    ... and {len(errors)-30} more")
else:
    print("    None ✅")

print(f"\n[5] WARNINGS ({len(warnings)}):")
if warnings:
    for w in warnings[:30]:
        print(f"    {w}")
    if len(warnings) > 30:
        print(f"    ... and {len(warnings)-30} more")
else:
    print("    None ✅")
