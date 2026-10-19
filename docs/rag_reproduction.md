# RAG 环境与复现

从仓库根目录运行。原始 PDF、模型下载、缓存、索引和完整评测保留在本地。当前主流程与评测见 [投标证据段落检索](passage_rag_evaluation.md)；[旧实验摘要](experiments/s604_results.md) 使用历史关键词/台账口径。

## 环境

本次实测：Windows、Python 3.11、RTX 5080 Laptop 16 GB；PyTorch 2.12.1+cu130、torchvision 0.27.1+cu130、transformers 5.20.1、accelerate 1.15.0、bitsandbytes 0.50.2、scikit-learn 1.9.0、NumPy 2.4.6、PyMuPDF 1.28.2。这是已使用的版本记录，不代表验证过所有最低版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r SystemCode/requirements-rag.txt
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

PyTorch 需要与 GPU、驱动匹配的 CUDA 构建。基础依赖仅规定较宽的最低版本，旧版 transformers 可能不支持 Qwen2.5-VL；复现时按实测版本准备环境。模型就位后可用 `--offline` 避免下载。

| 用途 | 目录 | 模型 ID |
| --- | --- | --- |
| 视觉解析 | `SystemCode/models/pretrained/Qwen2.5-VL-7B-Instruct` | `Qwen/Qwen2.5-VL-7B-Instruct` |
| 向量编码 | `SystemCode/models/pretrained/bge-m3` | `BAAI/bge-m3` |
| 重排序 | `SystemCode/models/pretrained/bge-reranker-v2-m3` | `BAAI/bge-reranker-v2-m3` |
| 机器学习八分类 | `SystemCode/models/tfidf_svm` | 项目训练产物 |
| 深度学习八分类 | `SystemCode/models/roberta_wwm_ext` | 项目训练产物 |

前三者下载到完整目录；八分类不能用未微调的基础 RoBERTa 替代。现有加载器会合并已提供的分段权重。NF4 只量化 Qwen 语言部分，视觉编码器和输出头保留 BF16。

## 1. 首次解析

```powershell
$bid = "data_extracted/2022年投标项目/11.11-S604皖南交旅融合（飞毕路美丽公路）项目/02_投标文件"
python SystemCode/run_rag_pipeline.py --mode ingest --bid_input "$bid" `
  --index_dir SystemCode/data/rag_index/s604 `
  --vision_cache_dir SystemCode/data/vision_cache/s604_qwen_nf4_greedy `
  --vlm_model SystemCode/models/pretrained/Qwen2.5-VL-7B-Instruct `
  --vlm_quantization nf4 --embedding_model SystemCode/models/pretrained/bge-m3 `
  --reranker_model SystemCode/models/pretrained/bge-reranker-v2-m3
```

常规解析保持 1024 token 输出预算。不能解析的结果记为 `qwen2.5_vl_raw`，下一步显式恢复。**缓存键来自 PDF 文件名和页数，不含内容哈希**；原 PDF 内容变化后须换新缓存目录。

## 2. 只恢复失败页

```powershell
python SystemCode/scripts/repair_rag_pages.py --bid_input "$bid" `
  --source_cache_dir SystemCode/data/vision_cache/s604_qwen_nf4_greedy `
  --output_cache_dir SystemCode/data/vision_cache/s604_qwen_nf4_repaired `
  --output_dir SystemCode/reports/rag_experiments/s604_recovery `
  --vlm_model SystemCode/models/pretrained/Qwen2.5-VL-7B-Instruct `
  --vlm_quantization nf4 --offline
```

原缓存不改写，成功页面逐条检查 SHA-256。只恢复原 raw 页面，先转录正文，再抽取短元数据；长度上限或重复停止触发最多四个叶子区域的分区重试。未正常结束或 JSON 无效则保留失败，返回 2；全部成功返回 0。输出包含逐页审计、原文和字段，不能直接作为公开摘要提交。

`--dry_run` 只看工作量；重复执行跳过已恢复页面。`--normalize_only` 只利用审计文件中完整结束的正文及有效 JSON，不加载 Qwen。规范化只调整类别别名和字段位置，不补写 JSON，不猜字段。

横置扫描件须人工核对方向后加 `--orientation_plan 本地计划.json`，格式为：

```json
[{"cache":"示例缓存文件名（不含.json）","page":2,"rotation_degrees_ccw":90}]
```

页码从 1 开始；角度为逆时针 0/90/180/270。本次 12 页使用人工方向计划，省略计划不会自动重现旋转。具体私有计划保留本机。

## 3. 增量重建

```powershell
python SystemCode/scripts/rebuild_rag_index.py --bid_input "$bid" `
  --cache_dir SystemCode/data/vision_cache/s604_qwen_nf4_repaired `
  --base_index SystemCode/data/rag_index/s604 `
  --output_index SystemCode/data/rag_index/s604_repaired --offline
```

输出须为新目录或空目录。缓存缺失或仍有 raw 页时停止，不调用 Qwen。模型相同时按完全相同的 `embedding_text` 复用旧向量，其余编码；指定不同 `--embedding_model` 禁用复用。同一路径换过权重也须视为模型改变，换新模型路径并重算。索引记录本地模型路径，换机器需调整或重建。

## 4. 历史分类与关键词/台账对照

当前证据检索请使用 [新评测入口](passage_rag_evaluation.md)。下列命令保留用于复现历史结果，需要索引中的 `ledger.json`；首次解析默认不再构建台账，复现旧实验时须显式添加 `--include_ledger`。增量重建脚本仍保留历史台账产物，但新评测不会读取它。

```powershell
python SystemCode/scripts/compare_classifiers.py `
  --classifier tfidf_svm=SystemCode/models/tfidf_svm `
  --classifier roberta_wwm_ext=SystemCode/models/roberta_wwm_ext `
  --holdout_json SystemCode/data/holdout_eval/2022__11.11annotations.json `
  --holdout_json SystemCode/data/holdout_eval/2024__2024.1.11-annotations.json `
  --index_dir SystemCode/data/rag_index/s604_repaired `
  --rag_holdout_json SystemCode/data/holdout_eval/2022__11.11annotations.json `
  --benchmark_json SystemCode/data/holdout_eval/s604_rag_benchmark.json `
  --reranker_model SystemCode/models/pretrained/bge-reranker-v2-m3 `
  --output_dir SystemCode/reports/rag_experiments/classifier_comparison --offline
```

每次用新的输出目录。省略索引、RAG 留出集和 benchmark 三项可只比较分类。模型顺序执行，检查标签、概率、共同基线及索引哈希；重排序失败立即停止，不将 RRF 回退当重排序。完整指标、逐查询结果和 CSV 留在输出目录。

`--max_eval_clauses 2` 只减少条款检索，分类仍覆盖全部样本，benchmark 仍全部运行；此入口检查不能冒充全量条款联合评测。八分类预测招标要求类型，视觉模型预测投标材料类别，两套标签不同。检索使用关键词/类别代理口径，台账关键词命中算第 1 位，不是精确证据召回或最终合规准确率。

## 回归检查

```powershell
python -m unittest discover -s SystemCode/tests -v
```

使用合成页面和模拟生成结果，不下载模型；覆盖分区重试、不完整输出拒收、规范化、金额类型保护、缓存保留及断点续跑。
