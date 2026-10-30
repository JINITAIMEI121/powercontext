# 一页技术说明 · LocalMind

## 目标

在浏览器本地完成文档与查询的向量化，用嵌入式 seekdb 完成全文 + 向量混合检索，不依赖在线 embedding API，也不把文档内容发送到第三方。

## 端侧推理

| 项 | 选择 |
| --- | --- |
| 模型 | `Xenova/all-MiniLM-L6-v2`，384 维句子向量 |
| 量化 | q8 ONNX（`model_quantized.onnx`，约 22MB） |
| 运行时 | Transformers.js 2.17.2 + ONNX Runtime Web（WASM） |
| 线程 | 单线程 SIMD（`numThreads=1`），避免 SharedArrayBuffer/COOP-COEP 依赖 |
| 池化 | mean pooling，输出 L2 归一化 |
| 文档 | 按句子切分，约 380 字符/片段，浏览器内批量编码 |

本机实测（Apple M5 / Chrome）：模型加载 0.4s（已缓存），6 篇文档共 7 个片段编码 0.79s，单条查询编码约 10–20ms；这些数字会随设备与浏览器变化，页面会显示实际耗时。

## seekdb 侧

```sql
docs(id, title, created_at)
chunks(id, doc_id, ordinal, text, embedding VECTOR(384))
CREATE FULLTEXT INDEX ft_chunks ON chunks(text) WITH PARSER SPACE
```

检索在一个连接上执行两次候选查询：

1. 关键词：`MATCH(text) AGAINST ('terms' IN NATURAL LANGUAGE MODE)`，按分数取前 30。
2. 语义：`ORDER BY l2_distance(embedding, :vector) LIMIT 30`。

应用层用 RRF（`1/(60+rank)`）融合两路候选，按 chunk 去重后返回 top-k；结果保留 `doc_id` / `chunk_id`，可回查原文。服务只监听 `127.0.0.1`，没有在线模型调用。

## 为什么这样分工

- 端侧负责“算”：embedding 在用户设备完成，节省网络与 API 成本，文档内容不出本机。
- 数据库负责“存与找”：seekdb 同时维护全文与向量索引，避免应用层再引入独立向量库；RRF 让关键词命中和语义命中互补。
- 浏览器与数据库之间只传文本、向量和引用，不传模型权重；服务端不依赖 GPU。

## 验证

点击“载入示例文档”后，“运行验证样例”会执行 6 条查询并检查期望标题：

```text
6/6 条验证样例通过
```

示例覆盖：seekdb 混合检索、asyncio 取消、咖啡参数、10-K 财务指标、RAG 引用预算、青海湖骑行。示例文档与查询位于 `web/samples/`。

## 边界与后续

- 使用通用 MiniLM，未做领域微调；目标是验证端侧推理 + seekdb 检索链路。
- 中文全文索引依赖空格分词；语义通道可补足同义表达，生产环境可替换为中文分析器。
- 单进程单写入者；大规模并发需要连接池、事务和任务队列。
- 模型权重与 WASM 随仓库分发；若需进一步减小体积，可按设备能力裁剪 WASM 或改用更小的量化模型。
