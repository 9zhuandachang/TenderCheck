import os
import sys
import zipfile
import urllib.request
from pathlib import Path
from tqdm import tqdm

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CONTRACT_NLI_URL = "https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip"
CUAD_URL = "https://zenodo.org/record/4595826/files/CUAD_v1.zip?download=1"

def download_file(url: str, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] Downloading {url} -> {output_path}")
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    
    with urllib.request.urlopen(req) as response:
        total_size = int(response.headers.get("Content-Length", 0))
        with open(output_path, "wb") as out_file:
            with tqdm(total=total_size, unit="B", unit_scale=True, unit_divisor=1024, desc=output_path.name) as pbar:
                while True:
                    chunk = response.read(1024 * 64)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    pbar.update(len(chunk))
    print(f"[OK] Download completed: {output_path} ({output_path.stat().st_size / (1024*1024):.2f} MB)")

def extract_zip(zip_path: Path, extract_to: Path):
    extract_to.mkdir(parents=True, exist_ok=True)
    print(f"[*] Extracting {zip_path} -> {extract_to}")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_to)
    print(f"[OK] Extraction completed to {extract_to}")

def setup_contract_nli(data_dir: Path):
    raw_dir = data_dir / "raw"
    contract_nli_dir = raw_dir / "contract_nli"
    zip_path = raw_dir / "contract-nli.zip"
    
    # Check if already extracted
    if (contract_nli_dir / "train.json").exists() and (contract_nli_dir / "test.json").exists():
        print(f"[OK] ContractNLI already exists at {contract_nli_dir}")
        return contract_nli_dir

    if not zip_path.exists():
        download_file(CONTRACT_NLI_URL, zip_path)
    
    extract_zip(zip_path, contract_nli_dir)
    return contract_nli_dir

def setup_cuad(data_dir: Path):
    raw_dir = data_dir / "raw"
    cuad_dir = raw_dir / "cuad"
    zip_path = raw_dir / "CUAD_v1.zip"
    
    if (cuad_dir / "CUAD_v1.json").exists():
        print(f"[OK] CUAD already exists at {cuad_dir}")
        return cuad_dir
        
    if not zip_path.exists():
        download_file(CUAD_URL, zip_path)
        
    extract_zip(zip_path, cuad_dir)
    return cuad_dir

if __name__ == "__main__":
    base_data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    dataset_name = sys.argv[1] if len(sys.argv) > 1 else "contract_nli"
    
    if dataset_name in ("contract_nli", "all"):
        setup_contract_nli(base_data_dir)
    if dataset_name in ("cuad", "all"):
        setup_cuad(base_data_dir)
