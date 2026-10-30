# NoteSeek · 本地研究笔记知识库

NoteSeek 是一个用 **OceanBase seekdb** 做关键词 + 向量混合检索的本地笔记应用。它把每一条笔记同时写入全文索引和高维向量列，搜索时用 `MATCH … AGAINST` 取关键词候选、用 `l2_distance` 取语义候选，再用 RRF 融合并返回可回查的笔记 ID。

- 项目网站（Qoder Sites）：https://noteseek-ab537s284ft.qoder.website
- 代码仓库：`https://github.com/JINITAIMEI121/powercontext/tree/2026-competition-ecosystem/qoder`
- 开发记录：[QODER.md](QODER.md)
- 界面截图：[screenshots/app.png](screenshots/app.png)

## 为什么做这个

学习、科研和工作中的笔记往往散落在多个文档里：关键词搜索找不到换了说法的内容，纯向量检索又容易忽略精确术语。NoteSeek 用同一个嵌入式数据库同时维护两种索引，在一台笔记本上完成“写入 → 双路检索 → 融合 → 引用回查”的闭环，不需要额外部署数据库服务，也不需要任何在线 API Key。

## 技术栈

| 层 | 实现 |
| --- | --- |
| 存储与索引 | 嵌入式 seekdb（`pylibseekdb`），`FULLTEXT INDEX` + `VECTOR(64)` 列 |
| 检索 | `MATCH … AGAINST` 关键词候选；`l2_distance` 语义候选；RRF 融合 |
| 后端 | Python 标准库 `http.server`（零 Web 框架依赖） |
| 前端 | 单页 HTML/CSS/JS，支持实时搜索与新增笔记 |
| 开发工具 | Qoder Desktop（多文件编辑、运行、截图与验证） |

## 运行

要求 Python 3.11+，macOS 或 Linux。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python seed.py          # 写入 8 条示例笔记
python app.py           # http://127.0.0.1:8765
```

打开 <http://127.0.0.1:8765>，输入“向量检索 数据库”“混合检索 全文索引”“EBITDA 现金流”等查询。新增笔记会立即进入全文与向量索引，下一次搜索即可命中。

## 验证

```bash
python validate.py
```

预期输出（节选）：

```text
PASS 向量检索 数据库 -> ['向量数据库入门', 'OceanBase seekdb 笔记', ...]
PASS 混合检索 全文索引 -> ['OceanBase seekdb 笔记', ...]
PASS asyncio 协程 事件循环 -> ['Python 异步编程', ...]
PASS 手冲咖啡 水温 粉水比 -> ['手冲咖啡参数', ...]
PASS EBITDA 现金流 资本开支 -> ['财报分析：PepsiCo 2022', ...]
PASS 环湖 海拔 补给 -> ['青海湖骑行路线', ...]
all 6 queries passed
```

也可以直接调用 HTTP API：

```bash
curl http://127.0.0.1:8765/api/health
curl 'http://127.0.0.1:8765/api/search?q=seekdb&limit=5'
curl -X POST http://127.0.0.1:8765/api/notes \
  -H 'Content-Type: application/json' \
  -d '{"title":"RAG 评估清单","body":"召回率、引用准确率、上下文预算与端到端延迟。"}'
```

## seekdb 集成点

`notesdb.py` 是唯一的存储层：

1. `pylibseekdb.aopen(data/seekdb)` 启动嵌入式实例，`pylibseekdb.connect("test")` 连接。
2. 建表：`notes(id, title, body, embedding VECTOR(64), created_at)`，并创建 `FULLTEXT INDEX ft_notes ON notes(title, body) WITH PARSER SPACE`。
3. 写入：正文经本地哈希向量化（64 维、单位归一化）后与原文一起写入。
4. 搜索：同一条连接上分别执行关键词与向量候选 SQL，应用层用 RRF 融合；结果保留 `note_id`，可回查原文。

没有使用 SQLite、JSON 或内存 mock 替代 seekdb。

## 目录

```text
qoder/
├── app.py              # HTTP 服务与静态页面
├── notesdb.py          # seekdb schema、向量化、混合检索
├── seed.py             # 示例数据
├── validate.py         # 离线验收
├── requirements.txt
├── samples/queries.json
├── web/                # 前端（同时作为 Qoder Sites 静态站点）
├── screenshots/app.png
├── README.md
└── QODER.md
```

## 已知边界

- 示例向量化使用本地哈希，不依赖在线模型；它适合演示检索链路，不等价于语义 embedding 模型。
- 中文全文索引按空格分词，示例查询以短语/英文术语为主；语义通道会补足同义表达。
- 单进程、单写入者，适合个人知识库；并发写入需要额外的连接与锁设计。
