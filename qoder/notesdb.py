"""NoteSeek storage layer: embedded seekdb + keyword/vector hybrid search."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

import pylibseekdb

DIMENSIONS = 64
_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")
_QUERY_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")

_lock = threading.Lock()
_instance = None
_connection = None
_ready = False


def _sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "") + "'"


def _embedding(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    tokens = _TOKEN.findall(text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + min(len(token), 8) / 8.0)
    for left, right in zip(tokens, tokens[1:], strict=False):
        digest = hashlib.sha256((left + right).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        vector[index] += 0.6 if digest[4] % 2 == 0 else -0.6
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def _vector_literal(vector: list[float]) -> str:
    return "'[" + ",".join(f"{value:.6f}" for value in vector) + "]'"


def _fts_terms(query: str) -> str:
    return " ".join(_QUERY_TOKEN.findall(query.lower())[:16])


def init(data_dir: Path | None = None) -> None:
    global _instance, _connection, _ready
    with _lock:
        if _ready:
            return
        path = Path(data_dir or Path(__file__).parent / "data" / "seekdb")
        path.mkdir(parents=True, exist_ok=True)
        _instance = asyncio.run(pylibseekdb.aopen(str(path)))
        _connection = pylibseekdb.connect("test")
        cursor = _connection.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS notes ("
            "id INT PRIMARY KEY, title VARCHAR(255) NOT NULL, body TEXT NOT NULL, "
            "embedding VECTOR(64) NOT NULL, created_at VARCHAR(40) NOT NULL)"
        )
        try:
            cursor.execute("CREATE FULLTEXT INDEX ft_notes ON notes(title, body) WITH PARSER SPACE")
        except Exception:
            pass
        _connection.commit()
        cursor.close()
        _ready = True


def shutdown() -> None:
    global _instance, _connection, _ready
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None
        if _instance is not None:
            _instance.close()
            _instance = None
        _ready = False


def _cursor():
    if not _ready or _connection is None:
        init()
    return _connection.cursor()


def count_notes() -> int:
    cursor = _cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM notes")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        cursor.close()


def list_notes(limit: int = 50) -> list[dict]:
    cursor = _cursor()
    try:
        cursor.execute(
            f"SELECT id, title, body, created_at FROM notes ORDER BY id DESC LIMIT {max(1, min(limit, 200))}"
        )
        return [
            {"id": int(row[0]), "title": row[1], "body": row[2], "created_at": row[3]}
            for row in cursor.fetchall()
        ]
    finally:
        cursor.close()


def add_note(title: str, body: str) -> dict:
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise ValueError("title and body must not be blank")
    if len(title) > 255:
        raise ValueError("title must be at most 255 characters")
    with _lock:
        cursor = _cursor()
        try:
            cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM notes")
            note_id = int(cursor.fetchone()[0])
            vector = _vector_literal(_embedding(f"{title}\n{body}"))
            created_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor.execute(
                "INSERT INTO notes (id, title, body, embedding, created_at) VALUES "
                f"({note_id}, {_sql_quote(title)}, {_sql_quote(body)}, {vector}, {_sql_quote(created_at)})"
            )
            _connection.commit()
            return {"id": note_id, "title": title, "body": body, "created_at": created_at}
        finally:
            cursor.close()


def seed_defaults() -> int:
    if count_notes() > 0:
        return 0
    samples = [
        (
            "向量数据库入门",
            "向量数据库把文本、图片等对象编码为高维向量，再用近似最近邻索引（HNSW、IVF）做语义检索。"
            "常见指标包括余弦相似度、内积和 L2 距离；工程上还要关注过滤、更新与召回率。",
        ),
        (
            "OceanBase seekdb 笔记",
            "seekdb 是面向 AI Agent 的嵌入式数据库，支持全文索引与向量索引。"
            "可以用一条 SQL 同时取得 MATCH ... AGAINST 的关键词候选和 l2_distance 的语义候选，再做融合排序。",
        ),
        (
            "RAG 上下文工程",
            "检索增强生成的关键不是把整篇文档塞进提示词，而是按问题选择原文证据、保留精确引用并控制上下文预算。"
            "上下文压缩、增量传输和来源生命周期都会影响最终回答质量。",
        ),
        (
            "Python 异步编程",
            "asyncio 通过事件循环调度协程；await 让出控制权，gather 并发等待多个任务。"
            "注意取消隔离、超时和阻塞调用，避免在事件循环里执行同步重活。",
        ),
        (
            "手冲咖啡参数",
            "浅烘豆常用水温 90-93 摄氏度，粉水比 1:15 左右；研磨度接近白砂糖。"
            "先闷蒸 30 秒，再分两到三段注水，总时间约 2 分 30 秒。",
        ),
        (
            "青海湖骑行路线",
            "环青海湖约 360 公里，常见四天行程。海拔 3200 米左右，需要预留适应时间；"
            "西海镇、湖东种羊场、黑马河和刚察是常见补给点，注意逆风和紫外线。",
        ),
        (
            "财报分析：PepsiCo 2022",
            "阅读 10-K 时先定位利润表、现金流量表和资产负债表。"
            "EBITDA 可由经营利润加折旧摊销估算；自由现金流关注经营现金流减去资本开支。",
        ),
        (
            "Git 常用命令",
            "rebase 用于整理线性历史，cherry-pick 把单个提交搬到当前分支，worktree 可以并行检出多个分支。"
            "改写已推送历史前要确认协作者影响并做好备份。",
        ),
    ]
    for title, body in samples:
        add_note(title, body)
    return len(samples)


def search_notes(query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(limit, 20))
    terms = _fts_terms(query)
    fts_rows: list[tuple[int, float]] = []
    vector_rows: list[tuple[int, float]] = []
    with _lock:
        cursor = _cursor()
        try:
            if terms:
                escaped = _sql_quote(terms)
                cursor.execute(
                    "SELECT id, title, body, MATCH(title, body) AGAINST "
                    f"({escaped} IN NATURAL LANGUAGE MODE) AS score FROM notes "
                    f"WHERE MATCH(title, body) AGAINST ({escaped} IN NATURAL LANGUAGE MODE) "
                    "ORDER BY score DESC LIMIT 20"
                )
                fts_rows = [(int(row[0]), float(row[3])) for row in cursor.fetchall()]
            vector = _vector_literal(_embedding(query))
            cursor.execute(
                "SELECT id, title, body, l2_distance(embedding, "
                f"{vector}) AS distance FROM notes ORDER BY l2_distance(embedding, {vector}) LIMIT 20"
            )
            vector_rows = [(int(row[0]), float(row[3])) for row in cursor.fetchall()]
            cursor.execute("SELECT id, title, body, created_at FROM notes")
            payload = {
                int(row[0]): {"id": int(row[0]), "title": row[1], "body": row[2], "created_at": row[3]}
                for row in cursor.fetchall()
            }
        finally:
            cursor.close()

    fused: dict[int, float] = {}
    sources: dict[int, set[str]] = {}
    for rank, (note_id, _score) in enumerate(fts_rows, start=1):
        fused[note_id] = fused.get(note_id, 0.0) + 1.0 / (60.0 + rank)
        sources.setdefault(note_id, set()).add("keyword")
    for rank, (note_id, _distance) in enumerate(vector_rows, start=1):
        fused[note_id] = fused.get(note_id, 0.0) + 1.0 / (60.0 + rank)
        sources.setdefault(note_id, set()).add("semantic")

    results = []
    for note_id, score in sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:limit]:
        note = payload.get(note_id)
        if note is None:
            continue
        snippet = re.sub(r"\s+", " ", note["body"]).strip()
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        results.append(
            {
                "id": note["id"],
                "title": note["title"],
                "snippet": snippet,
                "score": round(score, 6),
                "sources": sorted(sources.get(note_id, ())),
            }
        )
    return results


__all__ = ["add_note", "count_notes", "init", "list_notes", "search_notes", "seed_defaults", "shutdown"]
