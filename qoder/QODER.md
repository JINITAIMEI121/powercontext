# Qoder 开发记录 · NoteSeek

## 使用方式

本项目在 **Qoder Desktop** 中完成。Qoder 负责：

- 阅读 OceanBase 2026 大赛 Qoder 赛题要求，拆出“可运行、真实接入 seekdb、可发布网站、有验证样例”的交付清单。
- 生成并迭代 `notesdb.py` 的 seekdb schema、全文/向量候选 SQL 与 RRF 融合逻辑。
- 生成 `app.py` 的 HTTP API、静态页面服务和错误处理。
- 生成单页前端 `web/`，包括搜索、结果卡片、关键词/语义徽标、新增笔记和静态演示降级。
- 在本地启动 `pylibseekdb` 嵌入式实例，运行 `seed.py` / `validate.py` / HTTP 接口做真实读写验证。
- 使用 Qoder 的浏览器工具打开本地页面、执行搜索并截图，检查实际界面。
- 整理 README、验证样例与 Qoder Sites 静态站点。

## 关键步骤

1. **确认 seekdb 可用**：先用 `pylibseekdb.aopen` 启动实例，验证 `VECTOR(64)`、`l2_distance`、`FULLTEXT INDEX` 和 `MATCH … AGAINST` 在嵌入式 seekdb 中可用。
2. **设计数据模型**：`notes(id, title, body, embedding VECTOR(64), created_at)`；正文与标题进入全文索引，向量列保存本地哈希向量。
3. **实现混合检索**：关键词候选与向量候选分别查询，应用层按 RRF 融合；结果保留 note ID 与 snippet。
4. **实现 Web 层**：标准库 HTTP 服务提供 `/api/health`、`/api/notes`、`/api/search`，并托管静态页面。
5. **前端与静态演示**：实时模式调用 API；静态托管时自动降级为预置样例并展示提示条，便于 Qoder Sites 访客理解项目。
6. **验证**：`python seed.py` 写入 8 条笔记；`python validate.py` 的 6 条查询全部通过；HTTP 搜索与新增笔记均实测成功。
7. **发布**：将 `web/` 作为 Qoder Sites 站点发布，仓库中的 `qoder/` 目录保留完整源码与运行说明。

## 验证记录

```text
$ python seed.py
seeded 8 notes; total=8

$ python validate.py
PASS 向量检索 数据库 -> ['向量数据库入门', 'OceanBase seekdb 笔记', ...]
PASS 混合检索 全文索引 -> ['OceanBase seekdb 笔记', ...]
PASS asyncio 协程 事件循环 -> ['Python 异步编程', ...]
PASS 手冲咖啡 水温 粉水比 -> ['手冲咖啡参数', ...]
PASS EBITDA 现金流 资本开支 -> ['财报分析：PepsiCo 2022', ...]
PASS 环湖 海拔 补给 -> ['青海湖骑行路线', ...]
all 6 queries passed

$ curl http://127.0.0.1:8765/api/health
{"status": "ok", "notes": 8, "engine": "seekdb"}
```

## 说明

- 全程没有使用 SQLite、JSON 文件或内存结构替代 seekdb；`notesdb.py` 的读写都经过嵌入式 seekdb 连接。
- 向量化使用本地确定性哈希，不依赖在线模型；这是为了让评审在没有 API Key 的环境中也能复现完整链路。
- 站点在 Qoder Sites 上以静态演示模式运行；本地 `python app.py` 运行完整读写版本，二者共用同一套前端文件。
