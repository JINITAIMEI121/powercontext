window.NOTESEEK_DEMO = {
  notes: [
    { id: 1, title: "向量数据库入门", body: "向量数据库把文本、图片等对象编码为高维向量，再用 HNSW、IVF 等近似最近邻索引做语义检索。" },
    { id: 2, title: "OceanBase seekdb 笔记", body: "seekdb 支持全文索引与向量索引，可以用 MATCH ... AGAINST 和 l2_distance 同时取得两路候选。" },
    { id: 3, title: "RAG 上下文工程", body: "按问题选择原文证据、保留精确引用并控制上下文预算，比把整篇文档塞进提示词更可靠。" },
    { id: 4, title: "Python 异步编程", body: "asyncio 通过事件循环调度协程；await 让出控制权，gather 并发等待多个任务。" },
    { id: 5, title: "手冲咖啡参数", body: "浅烘豆常用水温 90-93 摄氏度，粉水比 1:15 左右；先闷蒸 30 秒再分段注水。" },
    { id: 6, title: "青海湖骑行路线", body: "环青海湖约 360 公里，海拔 3200 米左右，西海镇、黑马河和刚察是常见补给点。" },
    { id: 7, title: "财报分析：PepsiCo 2022", body: "阅读 10-K 时先定位利润表、现金流量表和资产负债表；EBITDA 可由经营利润加折旧摊销估算。" },
    { id: 8, title: "Git 常用命令", body: "rebase 整理线性历史，cherry-pick 搬运单个提交，worktree 并行检出多个分支。" },
  ],
  results: {
    "向量检索 数据库": [
      { id: 1, title: "向量数据库入门", snippet: "向量数据库把文本、图片等对象编码为高维向量，再用 HNSW、IVF 等近似最近邻索引做语义检索。", score: 0.032, sources: ["keyword", "semantic"] },
      { id: 2, title: "OceanBase seekdb 笔记", snippet: "seekdb 支持全文索引与向量索引，可以用 MATCH ... AGAINST 和 l2_distance 同时取得两路候选。", score: 0.028, sources: ["keyword", "semantic"] },
    ],
    "混合检索 全文索引": [
      { id: 2, title: "OceanBase seekdb 笔记", snippet: "seekdb 支持全文索引与向量索引，可以用 MATCH ... AGAINST 和 l2_distance 同时取得两路候选。", score: 0.032, sources: ["keyword", "semantic"] },
      { id: 3, title: "RAG 上下文工程", snippet: "按问题选择原文证据、保留精确引用并控制上下文预算，比把整篇文档塞进提示词更可靠。", score: 0.021, sources: ["semantic"] },
    ],
    "asyncio 协程": [
      { id: 4, title: "Python 异步编程", snippet: "asyncio 通过事件循环调度协程；await 让出控制权，gather 并发等待多个任务。", score: 0.032, sources: ["keyword", "semantic"] },
    ],
    "EBITDA 现金流": [
      { id: 7, title: "财报分析：PepsiCo 2022", snippet: "阅读 10-K 时先定位利润表、现金流量表和资产负债表；EBITDA 可由经营利润加折旧摊销估算。", score: 0.032, sources: ["keyword", "semantic"] },
    ],
  },
};
