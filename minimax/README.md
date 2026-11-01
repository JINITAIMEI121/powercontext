# SDD Context · 规格驱动开发的工程上下文模块

OceanBase 2026 大赛 MiniMax 赛题「规有所依，面向软件持续演进的 SDD 上下文管理系统」的参赛作品。

模块提供一个 CLI 入口 `sddctx`：把一个业务工程的**规格、代码、测试**导入嵌入式 **seekdb**，
按开发任务检索出当前适用的上下文，交给使用 **MiniMax** 的开发助手完成真实代码修改，
用 pytest 验证「改前失败、改后通过」，并把任务进展与验证结果写入 **PowerContext**（无服务时回落 seekdb），
重启模块后读取并复用该状态。

## 快速运行

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
./run_demo.sh                 # 完整跑一遍验证样例（默认 provider=minimax）
./run_demo.sh --provider mock # 无模型凭据时，仅验证链路（结果不计入作品证据）
```

PowerContext 通道（可选但为验收必需）：

```bash
pip install "powercontext[cli,server]==1.2.0"
powercontext config init --template --output .env.pctx     # 存储选 sqlite
set -a; . .env.pctx; set +a
powercontext server run &                                   # 默认 http://127.0.0.1:8000
export POWERCONTEXT_URI=http://127.0.0.1:8000
```

模型凭据从环境变量 `MINIMAX_API_KEY` 读取，或读取 `~/.config/oceanbase-2026/minimax_key`；
接口与模型可用 `MINIMAX_API_URL` / `MINIMAX_MODEL` 覆盖，默认
`https://api.minimax.cn/v1/chat/completions` + `MiniMax-M3.1-Flash`（OpenAI 兼容）。

## 调用入口

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `sddctx init` | — | 重建嵌入式 seekdb 数据目录 `.seekdb/` |
| `sddctx ingest [--stats]` | `--project` 工程根 | 规格/代码符号/测试用例入库，返回条目数 |
| `sddctx context <任务> [--task-id] [--json]` | 任务描述 | 适用规格、待改代码、验证测试、任务状态（Markdown 或 JSON），每条带来源 |
| `sddctx run <任务> [--task-id] [--provider minimax\|mock] [--dry-run]` | 任务描述 | 先跑目标/回归测试→构造上下文→模型产出改动→落盘→复跑测试→写任务状态；输出前后结果与运行目录 |
| `sddctx status [--task-id]` | — | 读取持久化任务状态（新进程） |
| `sddctx follow-up <任务>` | — | 读取状态后只处理未完成项，证明状态被复用 |

## 技术分工

| 技术 | 实际承担的作用 | 位置 |
| --- | --- | --- |
| MiniMax | 读取上下文后生成/修改代码（`sddctx/model.py`，OpenAI 兼容接口），开发记录落在 `demo_project/.sdd-runs/<时间戳>/`：`prompt.txt`、`model_response.txt`、`changes.patch`、`before/after_tests.json` | `sddctx/model.py`、`sddctx/agent.py` |
| seekdb | 存储规格条目、代码符号（含 AST 签名与内容哈希）、测试用例与任务状态，三张表都建 FULLTEXT 索引；上下文全部经 `MATCH ... AGAINST` 检索得到 | `sddctx/db.py`、`sddctx/ingest.py`、`sddctx/retrieve.py` |
| PowerContext | 任务进展与验证结果的跨进程存储：`POWERCONTEXT_URI` 指向 Server 时，`sddctx run` 通过 `POST /v1/memory/remember`（`kind=task_state`）写入并返回 citation；后续运行用 `POST /v1/memory/search` 读回，`status`/`follow-up` 据此决定是否需要继续改动 | `sddctx/state.py` |

## 检索设计

1. 任务文本 → 关键词（ASCII 词 + 中文二元组）→ 先检索**规格**；
2. 从命中规格正文里抽取被点名的代码标识符（如 `service.filter_orders`、`repository.list_orders`），
   与任务词合并成第二跳查询 → 检索**待改代码符号**；
3. 用同一批标识符检索**测试用例**，再按「测试调用了被改符号」筛出目标功能测试，回归测试单列；
4. 已废弃规格（`status: deprecated`）不参与检索，避免使用过时约束。

规格更新后重新 `ingest`，后续上下文立即使用新内容（见下方验证样例第 6 步）。

## 验证样例

- **要改什么**：给订单查询增加 `since` / `until` 闭区间筛选，`since > until` 时抛 `ValueError`，
  且不就地修改入参列表（规格 `SPEC-003.1/2/3`）。
- **哪些原有行为必须保持**：状态/客户筛选、返回顺序、`get_order`、`total_amount`
  （回归用例 `tests/test_repository_regression.py`，规格 `SPEC-001.*`）。
- **初始数据**：`demo_project/`（`git checkout .` 可重置），`specs/` 3 份规格、`src/orders/` 2 个模块、`tests/` 2 个测试文件。

操作步骤与预期结果：

| 步骤 | 命令 | 预期 |
| --- | --- | --- |
| 1 | `sddctx init && sddctx ingest --stats` | `specs=7, symbols=4, tests=12` |
| 2 | `sddctx context "<任务>"` | 命中 `SPEC-003.*`+`SPEC-002.1`，列出 `filter_orders`/`list_orders` 等待改符号、5 条目标测试、7 条回归测试，全部带 `文件:行号` 来源 |
| 3 | `sddctx run "<任务>" --provider minimax` | `before.target=false`（目标行为未实现）、`before.regression=true` |
| 4 | 同上 | 模型产出 `edits`，写盘后 `after.target=true`、`after.regression=true`，`phase=verified` |
| 5 | 新进程执行 `sddctx status` | 读回 done/todo/verification；`run` 输出里的 `state_receipt.powercontext.citation` 即 PowerContext 条目引用 |
| 5b | 先 `DELETE FROM task_state` 清空本地状态表，再执行 `sddctx status` 与 `sddctx follow-up` | `status` 返回 `source=powercontext` 且带 citation；`follow-up` 依据读回的状态判定「无需再次修改」 |
| 6 | 改 `specs/SPEC-003-date-range.md` 的 `version` 与错误文案后 `sddctx ingest` | `sddctx context` 输出显示新版本与新内容（不使用固定答案） |
| 7 | `sddctx follow-up "<任务>"` | 状态为 `verified` 且无待办时直接判定无需再改；有待办时把状态拼进新一轮上下文继续处理 |

重置方法：`cd demo_project && git checkout . && rm -rf .sdd-runs && sddctx init && sddctx ingest`。

## 已知限制

- `--provider mock` 只用于离线验证链路，输出中会标注 `provider=mock`，不作为 MiniMax 参与证据。
- 上下文检索基于 seekdb 全文索引（`space`/`beng` 分词），未使用向量召回；跨语言同义改写场景召回偏弱。
- PowerContext 通道依赖可访问的 `POWERCONTEXT_URI` 与 `powercontext` CLI；未配置时状态落在 seekdb，
  重启进程后同样可读取复用（第 5 步已覆盖）。
- 嵌入式 seekdb 单进程单写者；`sddctx` 每次调用独占实例。
