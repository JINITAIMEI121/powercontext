"""Embedded seekdb storage for engineering artefacts and task state."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import pylibseekdb

_TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS spec_entry (
        id BIGINT PRIMARY KEY,
        spec_key VARCHAR(64) NOT NULL,
        title VARCHAR(255) NOT NULL,
        body TEXT NOT NULL,
        version VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        source_file VARCHAR(255) NOT NULL,
        source_line INT NOT NULL,
        updated_at VARCHAR(40) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS code_symbol (
        id BIGINT PRIMARY KEY,
        file_path VARCHAR(255) NOT NULL,
        symbol VARCHAR(128) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        signature TEXT NOT NULL,
        body TEXT NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        content_hash VARCHAR(64) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS test_case (
        id BIGINT PRIMARY KEY,
        file_path VARCHAR(255) NOT NULL,
        test_name VARCHAR(128) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        calls TEXT NOT NULL,
        body TEXT NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_state (
        task_id VARCHAR(64) PRIMARY KEY,
        task_text TEXT NOT NULL,
        phase VARCHAR(32) NOT NULL,
        done TEXT NOT NULL,
        todo TEXT NOT NULL,
        verification TEXT NOT NULL,
        context_hash VARCHAR(64) NOT NULL,
        updated_at VARCHAR(40) NOT NULL)""",
]

FTS_INDEXES = [
    "CREATE FULLTEXT INDEX ft_spec ON spec_entry(title, body) WITH PARSER space",
    "CREATE FULLTEXT INDEX ft_symbol ON code_symbol(signature, body) WITH PARSER beng",
    "CREATE FULLTEXT INDEX ft_test ON test_case(test_name, body, calls) WITH PARSER beng",
]


def query_terms(text: str, limit: int = 24) -> str:
    """Turn free text into a full-text query: ascii words plus CJK bigrams."""
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_]{2,}", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]+", lowered)
    bigrams: list[str] = []
    for run in cjk:
        if len(run) == 1:
            bigrams.append(run)
        bigrams.extend(run[i:i + 2] for i in range(len(run) - 1))
    seen: dict[str, None] = {}
    for term in words + bigrams:
        seen.setdefault(term, None)
    return " ".join(list(seen)[:limit])


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\x00", "\\0")
    return "'" + escaped + "'"


def _format(statement: str, params: Sequence[Any]) -> str:
    if not params:
        return statement
    parts = statement.split("%s")
    if len(parts) - 1 != len(params):
        raise ValueError("placeholder/param count mismatch")
    out = parts[0]
    for value, tail in zip(params, parts[1:]):
        out += _literal(value) + tail
    return out


class SeekdbStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._instance = asyncio.run(pylibseekdb.aopen(str(self.data_dir)))
        self._connection = pylibseekdb.connect("test")
        self._ensure_schema()

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            self._instance.close()

    def _cursor(self):
        return self._connection.cursor()

    def _ensure_schema(self) -> None:
        cursor = self._cursor()
        try:
            for statement in SCHEMA:
                cursor.execute(statement)
            for statement in FTS_INDEXES:
                try:
                    cursor.execute(statement)
                except Exception:
                    pass
            self._connection.commit()
        finally:
            cursor.close()

    def execute(self, statement: str, params: Sequence[Any] = ()) -> None:
        cursor = self._cursor()
        try:
            cursor.execute(_format(statement, list(params)))
            self._connection.commit()
        finally:
            cursor.close()

    def executemany(self, statement: str, rows: Iterable[Sequence[Any]]) -> None:
        cursor = self._cursor()
        try:
            for row in rows:
                cursor.execute(_format(statement, list(row)))
            self._connection.commit()
        finally:
            cursor.close()

    def fetch(self, statement: str, params: Sequence[Any] = ()) -> list[tuple]:
        cursor = self._cursor()
        try:
            cursor.execute(_format(statement, list(params)))
            return list(cursor.fetchall())
        finally:
            cursor.close()

    def clear(self, table: str) -> None:
        self.execute(f"DELETE FROM {table}")
