"""LocalMind: browser-embedded MiniLM + seekdb hybrid retrieval demo."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pylibseekdb

DIMENSIONS = 384
ROOT = Path(__file__).parent
WEB = ROOT / "web"
_token = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")
_lock = threading.RLock()
_instance = None
_connection = None
_ready = False


def _sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "") + "'"


def _vector_literal(vector: list[float]) -> str:
    return "'[" + ",".join(f"{value:.7f}" for value in vector) + "]'"


def _validate_vector(vector: object) -> list[float]:
    if not isinstance(vector, list) or len(vector) != DIMENSIONS:
        raise ValueError(f"vector must contain {DIMENSIONS} numbers")
    normalized = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("vector values must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("vector values must be finite")
        normalized.append(number)
    return normalized


def _fts_terms(query: str) -> str:
    return " ".join(_token.findall(query.lower())[:20])


def init_db() -> None:
    global _instance, _connection, _ready
    with _lock:
        if _ready:
            return
        path = ROOT / "data" / "seekdb"
        path.mkdir(parents=True, exist_ok=True)
        _instance = asyncio.run(pylibseekdb.aopen(str(path)))
        _connection = pylibseekdb.connect("test")
        cursor = _connection.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS docs ("
            "id INT PRIMARY KEY, title VARCHAR(255) NOT NULL, created_at VARCHAR(40) NOT NULL)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "id INT PRIMARY KEY, doc_id INT NOT NULL, ordinal INT NOT NULL, "
            "text TEXT NOT NULL, embedding VECTOR(384) NOT NULL)"
        )
        try:
            cursor.execute("CREATE FULLTEXT INDEX ft_chunks ON chunks(text) WITH PARSER SPACE")
        except Exception:
            pass
        _connection.commit()
        cursor.close()
        _ready = True


def shutdown() -> None:
    global _connection, _instance, _ready
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
        init_db()
    return _connection.cursor()


def stats() -> dict:
    cursor = _cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM docs")
        docs = int(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM chunks")
        chunks = int(cursor.fetchone()[0])
    finally:
        cursor.close()
    return {"docs": docs, "chunks": chunks, "dimensions": DIMENSIONS, "engine": "seekdb"}


def add_document(title: str, chunks: list[dict]) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("title must not be blank")
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 64:
        raise ValueError("chunks must contain between 1 and 64 items")
    prepared = []
    for chunk in chunks:
        text = str(chunk.get("text", "")).strip()
        if not text:
            raise ValueError("chunk text must not be blank")
        prepared.append((text, _validate_vector(chunk.get("vector"))))
    with _lock:
        cursor = _cursor()
        try:
            cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM docs")
            doc_id = int(cursor.fetchone()[0])
            created_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor.execute(
                "INSERT INTO docs (id, title, created_at) VALUES "
                f"({doc_id}, {_sql_quote(title)}, {_sql_quote(created_at)})"
            )
            cursor.execute("SELECT COALESCE(MAX(id), 0) FROM chunks")
            next_chunk = int(cursor.fetchone()[0]) + 1
            for ordinal, (text, vector) in enumerate(prepared):
                cursor.execute(
                    "INSERT INTO chunks (id, doc_id, ordinal, text, embedding) VALUES "
                    f"({next_chunk + ordinal}, {doc_id}, {ordinal}, {_sql_quote(text)}, {_vector_literal(vector)})"
                )
            _connection.commit()
            return {"id": doc_id, "title": title, "chunks": len(prepared), "created_at": created_at}
        except Exception:
            try:
                _connection.rollback()
            except Exception:
                pass
            raise
        finally:
            cursor.close()


def list_documents() -> list[dict]:
    cursor = _cursor()
    try:
        cursor.execute("SELECT id, title, created_at FROM docs ORDER BY id DESC")
        return [{"id": int(row[0]), "title": row[1], "created_at": row[2]} for row in cursor.fetchall()]
    finally:
        cursor.close()


def reset() -> None:
    with _lock:
        cursor = _cursor()
        try:
            cursor.execute("DELETE FROM chunks")
            cursor.execute("DELETE FROM docs")
            _connection.commit()
        finally:
            cursor.close()


def search(query: str, vector: list[float], limit: int = 6) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be blank")
    vector = _validate_vector(vector)
    limit = max(1, min(limit, 20))
    terms = _fts_terms(query)
    starts = time.perf_counter()
    fts_rows: list[tuple[int, float]] = []
    vector_rows: list[tuple[int, float]] = []
    with _lock:
        cursor = _cursor()
        try:
            if terms:
                escaped = _sql_quote(terms)
                cursor.execute(
                    "SELECT c.id, MATCH(c.text) AGAINST "
                    f"({escaped} IN NATURAL LANGUAGE MODE) AS score FROM chunks c "
                    f"WHERE MATCH(c.text) AGAINST ({escaped} IN NATURAL LANGUAGE MODE) "
                    "ORDER BY score DESC LIMIT 30"
                )
                fts_rows = [(int(row[0]), float(row[1])) for row in cursor.fetchall()]
            literal = _vector_literal(vector)
            cursor.execute(
                "SELECT c.id, l2_distance(c.embedding, "
                f"{literal}) AS distance FROM chunks c ORDER BY l2_distance(c.embedding, {literal}) LIMIT 30"
            )
            vector_rows = [(int(row[0]), float(row[1])) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT c.id, c.doc_id, d.title, c.ordinal, c.text FROM chunks c JOIN docs d ON d.id = c.doc_id"
            )
            payload = {
                int(row[0]): {
                    "chunk_id": int(row[0]),
                    "doc_id": int(row[1]),
                    "title": row[2],
                    "ordinal": int(row[3]),
                    "text": row[4],
                }
                for row in cursor.fetchall()
            }
        finally:
            cursor.close()
    fused: dict[int, float] = {}
    sources: dict[int, set[str]] = {}
    for rank, (chunk_id, _score) in enumerate(fts_rows, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (60.0 + rank)
        sources.setdefault(chunk_id, set()).add("keyword")
    for rank, (chunk_id, _distance) in enumerate(vector_rows, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (60.0 + rank)
        sources.setdefault(chunk_id, set()).add("semantic")
    results = []
    for chunk_id, score in sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:limit]:
        item = payload.get(chunk_id)
        if item is None:
            continue
        item["score"] = round(score, 7)
        item["sources"] = sorted(sources.get(chunk_id, ()))
        results.append(item)
    return {"results": results, "elapsed_ms": round((time.perf_counter() - starts) * 1000, 2)}


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalMind/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[localmind] {self.address_string()} {fmt % args}")

    def _json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        suffix = path.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".onnx": "application/octet-stream",
            ".wasm": "application/wasm",
        }.get(suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if suffix in {".onnx", ".wasm"}:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json(200, {"status": "ok", **stats()})
            return
        if parsed.path == "/api/docs":
            self._json(200, {"documents": list_documents()})
            return
        if parsed.path in ("/", "/index.html"):
            self._file(WEB / "index.html")
            return
        relative = parsed.path.lstrip("/")
        candidate = (WEB / relative).resolve()
        if WEB.resolve() in candidate.parents or candidate.parent == WEB.resolve():
            self._file(candidate)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/docs":
                document = add_document(str(payload.get("title", "")), payload.get("chunks", []))
                self._json(201, {"document": document, **stats()})
                return
            if path == "/api/search":
                result = search(
                    str(payload.get("query", "")),
                    payload.get("vector", []),
                    int(payload.get("limit", 6)),
                )
                self._json(200, result)
                return
            if path == "/api/reset":
                reset()
                self._json(200, {"ok": True, **stats()})
                return
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
            return
        except Exception as error:
            self._json(500, {"error": str(error)})
            return
        self.send_error(404)


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8788"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LocalMind running at http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutdown()


if __name__ == "__main__":
    main()
