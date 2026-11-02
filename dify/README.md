# 记忆罐头（Memory Can）

用 Dify 做的一个好玩的小应用：把每天的一句话丢进「罐头」，下次打开时会翻出旧回忆，并用一种随机的语气回你一句。
数据真实存储在 **seekdb** 中，Dify 工作流负责编排「召回 → 生成答复 → 存档」的完整链路。

## 玩法

1. 在 Dify 对话里说一句话，例如「今天在西湖边喝了一杯很好喝的龙井」。
2. 工作流调用 seekdb 记忆服务：按关键词召回你以前写过的相似片段。
3. 用一个随机的「罐头人格」（夸夸怪 / 冷静观察员 / 文艺青年 / 吐槽役）把旧回忆和新输入揉成一句回复。
4. 把这句话写回 seekdb，回复末尾显示罐头里已经攒了多少条回忆。

真实运行示例（见 `screenshots/chat.png`）：

```
你：今天在西湖边喝了一杯很好喝的龙井
罐头（冷静观察员）：咚——罐头是空的，你是第一条。
  已记录。今天的你比昨天多了一点具体。
  「今天在西湖边喝了一杯很好喝的龙井」已经收好，下次再来就能翻到它了。
  （罐头里现在有 1 条回忆）

你：又想去西湖了，顺便再喝一次龙井
罐头：咚——罐头开了。
  翻到一条旧回忆：「今天在西湖边喝了一杯很好喝的龙井」。
  今天这句「又想去西湖了，顺便再喝一次龙井」，也替你收好了。
  罐头里现在有 2 条回忆。
```

## 结构

```
Dify 1.18 Chatflow「记忆罐头」
  ├─ 开始
  ├─ HTTP Request：POST http://host.docker.internal:18700/chat
  │     请求体 {"user_id":"{{#sys.user_id#}}","query":"{{#sys.query#}}"}
  ├─ Code（python3）：解析返回的 reply / total
  └─ Answer：输出 reply + 罐头条数
        │
        ▼
dify/memory_api.py  （Python 标准库 HTTP 服务，端口 18700）
        │  pymysql
        ▼
seekdb（比赛提供的 seekdb 二进制，MySQL 协议端口 2882）
```

- **Dify**：应用与工作流编排、变量传递、对话界面、发布站点。
- **seekdb**：真实存储每条记忆，并为召回提供查询结果。
- **memory_api.py**：只做薄适配（建表、写入、关键词召回、模板拼装），不含预置答案。

不依赖任何在线大模型；整个作品可在本机离线复现（Dify 镜像首次拉取需要网络）。

## 目录

| 文件 | 说明 |
| --- | --- |
| `memory_api.py` | seekdb 记忆服务（`/remember`、`/recall`、`/chat`、`/stats`） |
| `init.sql` | 数据库与表结构 |
| `app.dsl.yml` | Dify 应用 DSL（导出包内的 `app.yaml`），导入即可得到完整工作流 |
| `docker-compose.override.yaml` | Dify 本地部署的端口补充、host 网关与 SSRF 放行配置 |
| `screenshots/chat.png` | 真实对话截图（含第二次召回旧记忆） |
| `validate.sh` | 一键验证记忆服务与 seekdb 写入/召回 |

## 本地复现

### 1. 启动 seekdb

使用比赛提供的 `seekdb` 二进制（或任意兼容 MySQL 协议的 seekdb / OceanBase 实例），监听 `2882`：

```bash
python3 <seekdb-repo>/.github/script/seekdb/sdb.py start \
  --binary <seekdb-repo>/build_release/src/observer/seekdb \
  --base-dir /root/t1 --port 2882
```

### 2. 建表并启动记忆服务

```bash
pip install pymysql
obclient -h127.0.0.1 -P2882 -uroot < dify/init.sql
python3 dify/memory_api.py          # 监听 0.0.0.0:18700
```

验证：

```bash
bash dify/validate.sh
```

### 3. 启动 Dify

```bash
cd <dify-source>/docker
cp .env.example .env
# 关键配置：
#   SECRET_KEY=<随机值>
#   EXPOSE_NGINX_PORT=18080
#   EXPOSE_NGINX_SSL_PORT=18443
#   COMPOSE_PROFILES=weaviate,postgresql,collaboration
docker compose up -d
```

`docker-compose.override.yaml`（本目录）需要放到 Dify 的 `docker/` 目录下（同名即可自动合并），它做两件缺一不可的事：

1. **host mapping**：给 `ssrf_proxy`、`agent_ssrf_proxy`、`api`、`worker`、`worker_beat`、`plugin_daemon` 加
   `extra_hosts: host.docker.internal:host-gateway`。注意必须包含 `ssrf_proxy` 本身 —— HTTP 节点是经它转发的，
   代理容器解析不到这个名字就会 502。
2. **SSRF 放行**：给 `ssrf_proxy` 设 `SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.1` 与
   `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=host.docker.internal`。Dify 的 `squid.conf.template` 里
   `include /etc/squid/dify_allow_private.conf` 位于 `http_access deny to_private_networks` **之前**，
   所以这两个官方变量是唯一有效的放行入口；宿主机网关 `172.17.0.1` 属于 RFC1918，不放行时请求会被判
   `TCP_DENIED/403`，只配 `extra_hosts` 是不够的。

改完需要重建代理容器让 entrypoint 重新生成白名单：

```bash
docker compose up -d --force-recreate ssrf_proxy agent_ssrf_proxy
docker exec docker-ssrf_proxy-1 cat /etc/squid/dify_allow_private.conf   # 应看到两条 acl + http_access allow
```

首次打开 `http://localhost:18080/install` 设置管理员账号，然后在「工作室 → 导入应用」中选择 `app.dsl.yml`。

### 4. 运行

应用发布后打开 `http://localhost:18080/chat/<站点访问码>`，输入任意一句话即可。
数据写入 seekdb 的 `dify_memory.memories` 表。

## 端到端验证

`verify_e2e.sh` 逐段证明链路真的通了，任何一段失败都会以非零码退出并打印判据：

| 步骤 | 判据 |
| --- | --- |
| 1. 记忆服务 + seekdb | `GET :18700/stats` 返回 200，并回显 seekdb 真实行数 |
| 2. 代理解析 | `docker exec docker-ssrf_proxy-1 getent hosts host.docker.internal` 得到宿主网关 IP |
| 3. 穿过 squid 打到宿主 | 在 `api` 容器内 `curl -x http://ssrf_proxy:3128 http://host.docker.internal:18700/stats` 返回 200；`ssrf_proxy` 访问日志里对应一条 `TCP_MISS/200`（不是 `TCP_DENIED/403`） |
| 4. Dify 工作流 | 用应用的 API Key 连续调用两次 `/v1/chat-messages`，第二次回复包含「翻到一条旧回忆」 |
| 5. 落库 | seekdb `select count(*)` 在两次调用之间递增，且新增行的内容与本次输入一致 |

手工复现第 3 步（最容易踩坑的一段）：

```bash
docker exec docker-api-1 sh -lc \
  'curl -sS -o /dev/null -w "%{http_code}\n" -x http://ssrf_proxy:3128 http://host.docker.internal:18700/stats'
docker logs --tail 20 docker-ssrf_proxy-1 2>&1 | grep host.docker.internal
```

## 验证

`validate.sh` 会写入两条样例、按关键词召回，并打印 seekdb 中的真实行数。
Dify 侧验证：连续发两条包含相同关键词的消息，第二条回复应出现「翻到一条旧回忆」且条数递增（见 `screenshots/chat.png`）。

## 设计说明

- 召回使用「关键词 LIKE + 最近记录兜底」，结果全部来自 seekdb 查询，不伪造记忆。
- 回复由本地模板 + 随机人格生成，不调用外部模型；离线可复现、零模型成本。
- 以 `sys.user_id` 隔离不同访问者的数据。

## 已知限制

- 关键词召回是朴素子串匹配，中文分词未接入；数据量很大时可替换为 seekdb 全文索引或向量检索。
- 回复模板固定四种人格；把 Code 节点替换为任意 LLM 节点即可升级为模型生成。
