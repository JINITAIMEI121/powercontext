# LocalMind · 端侧 MiniLM + seekdb 混合检索

LocalMind 是一个可运行的迷你知识库：**文档和查询的 embedding 全部在浏览器本地完成**（MiniLM-L6-v2 q8 量化 ONNX + Transformers.js/WASM），向量与原文写入嵌入式 **seekdb**，检索时用 `MATCH … AGAINST` 取关键词候选、`l2_distance` 取语义候选，再做 RRF 融合。服务只监听 `127.0.0.1`，不调用任何在线 embedding API。

- 赛题：OceanBase 2026 大赛 StepFun 方向「端侧 AI 推理性能优化与 seekdb 混合检索」
- 技术说明：[TECH_NOTE.md](TECH_NOTE.md)
- 界面截图：[screenshot.png](screenshot.png)

## 快速运行

要求 Python 3.11+，macOS 或 Linux；模型与 WASM 已随仓库放在 `web/models/`。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 8788 可能被占用，可换端口
PORT=8899 python server.py
```

打开 <http://127.0.0.1:8899>：

1. 等待右上角/状态条显示「本地模型就绪 · MiniLM-L6-v2 q8」。
2. 点击 **载入示例文档**：6 篇示例在浏览器内切片、编码并写入 seekdb。
3. 输入查询，或点击 **运行验证样例**，应看到 `6/6 条验证样例通过`。
4. 也可以粘贴文本、选择 `.txt/.md` 文件，再点击 **建立索引**。

## 验证样例

| 查询 | 期望命中 |
| --- | --- |
| hybrid retrieval fulltext vector fusion | Vector retrieval with seekdb |
| asyncio cancellation event loop | Async Python patterns |
| coffee water temperature ratio | Coffee brewing notes |
| EBITDA cash flow capital expenditures | PepsiCo 2022 10-K notes |
| RAG citation context budget | RAG context engineering |
| Qinghai Lake altitude supply stops | Qinghai Lake cycling |

本机实测：模型加载 0.4s（浏览器缓存）、6 篇 / 7 个片段浏览器端编码 0.79s、6/6 验证通过；查询 embedding 与 seekdb 检索耗时显示在页面上。

## 架构

```text
浏览器
  ├─ Transformers.js + all-MiniLM-L6-v2 (q8 ONNX, WASM)
  │    ├─ 文档切片 → 384 维向量
  │    └─ 查询 → 384 维向量
  └─ fetch /api/docs, /api/search
          │
Python 标准库 HTTP 服务
  └─ 嵌入式 seekdb
       ├─ docs(id, title, created_at)
       ├─ chunks(id, doc_id, ordinal, text, embedding VECTOR(384))
       ├─ FULLTEXT INDEX ft_chunks ON chunks(text)
       └─ MATCH … AGAINST + l2_distance + RRF
```

- **端侧推理**：`web/models/Xenova/all-MiniLM-L6-v2/onnx/model_quantized.onnx`（q8，约 22MB）；`web/models/wasm/` 为 ONNX Runtime Web 的 SIMD 与回退 WASM。
- **量化**：使用 q8 量化模型，单线程 WASM，避免跨源隔离要求；池化为 mean pooling，输出 L2 归一化。
- **存储**：`pylibseekdb` 启动嵌入式实例，所有 SQL 都经过本地连接；没有 SQLite/JSON/mock 替代。
- **检索**：关键词 + 语义双路候选，RRF（`1/(60+rank)`）融合；页面展示 embedding 与 seekdb 检索耗时。

## 目录

```text
stepfun/
├── server.py             # HTTP 服务 + seekdb schema/检索
├── requirements.txt
├── web/
│   ├── index.html
│   ├── app.js            # Transformers.js 端侧推理 + UI
│   ├── style.css
│   ├── transformers.min.js
│   ├── models/           # MiniLM q8 + ONNX Runtime WASM
│   └── samples/          # 示例文档与查询
├── screenshot.png
├── README.md
└── TECH_NOTE.md
```

## 已知边界

- 端侧模型是通用 MiniLM，不是领域微调模型；检索质量主要验证链路和工程可行性。
- 单进程、单写入者；并发写入需要额外连接/锁设计。
- 模型与 WASM 随仓库分发，首次加载受浏览器缓存影响；页面会显示实际加载耗时。
