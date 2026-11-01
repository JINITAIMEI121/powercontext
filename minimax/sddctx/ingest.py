"""Import specs, code symbols and tests from a project into seekdb."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .db import SeekdbStore


def _row_id(*parts: str) -> int:
    digest = hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=7).digest()
    return int.from_bytes(digest, "big") % (2 ** 62)


@dataclass
class SpecEntry:
    spec_key: str
    title: str
    body: str
    version: str
    status: str
    source_file: str
    source_line: int


def parse_specs(spec_dir: Path) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    if not spec_dir.is_dir():
        return entries
    for path in sorted(spec_dir.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        current: SpecEntry | None = None
        buffer: list[str] = []
        for index, line in enumerate(lines, start=1):
            if line.startswith("## "):
                if current is not None:
                    entries.append(_finish(current, buffer))
                header = line[3:].strip()
                spec_key, _, title = header.partition(" ")
                current = SpecEntry(spec_key=spec_key.strip(), title=(title or spec_key).strip(),
                                    body="", version="", status="", source_file=str(path),
                                    source_line=index)
                buffer = []
            elif current is not None:
                buffer.append(line)
        if current is not None:
            entries.append(_finish(current, buffer))
    return entries


def _finish(entry: SpecEntry, buffer: list[str]) -> SpecEntry:
    body = "\n".join(buffer).strip()
    version = ""
    status = "active"
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("version:"):
            version = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("status:"):
            status = stripped.split(":", 1)[1].strip() or "active"
        else:
            kept.append(line)
    entry.body = "\n".join(kept).strip()
    entry.version = version or "unversioned"
    entry.status = "deprecated" if status.lower().startswith("deprecat") else status
    return entry


def parse_code(root: Path, subdir: str = "src") -> list[dict]:
    symbols: list[dict] = []
    base = root / subdir
    if not base.is_dir():
        return symbols
    for path in sorted(base.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                signature = _signature(node)
                start = node.lineno
                end = getattr(node, "end_lineno", start) or start
                body = "\n".join(source.splitlines()[start - 1:end])
                symbols.append(dict(
                    id=_row_id(relative, node.name, str(start)),
                    file_path=relative, symbol=node.name, kind=kind, signature=signature,
                    body=body, start_line=start, end_line=end,
                    content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]))
    return symbols


def _signature(node: ast.AST) -> str:
    args = getattr(node, "args", None)
    if args is None:
        return f"class {node.name}"
    parts: list[str] = []
    for arg in list(getattr(args, "posonlyargs", [])) + list(args.args):
        parts.append(arg.arg)
    if getattr(args, "vararg", None):
        parts.append("*" + args.vararg.arg)
    for arg in args.kwonlyargs:
        parts.append(arg.arg)
    if getattr(args, "kwarg", None):
        parts.append("**" + args.kwarg.arg)
    returns = getattr(node, "returns", None)
    tail = f" -> {ast.unparse(returns)}" if returns is not None else ""
    return f"{node.name}({', '.join(parts)}){tail}"


def parse_tests(root: Path, subdir: str = "tests") -> list[dict]:
    cases: list[dict] = []
    base = root / subdir
    if not base.is_dir():
        return cases
    for path in sorted(base.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(root))
        kind = "regression" if "regression" in path.name else "functional"
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                calls = sorted({
                    call.func.id for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                } | {
                    call.func.attr for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                })
                start = node.lineno
                end = getattr(node, "end_lineno", start) or start
                body = "\n".join(source.splitlines()[start - 1:end])
                cases.append(dict(
                    id=_row_id(relative, node.name),
                    file_path=relative, test_name=node.name, kind=kind,
                    calls=" ".join(calls), body=body, start_line=start, end_line=end))
    return cases


def ingest(store: SeekdbStore, project: Path) -> dict[str, int]:
    project = Path(project)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    specs = parse_specs(project / "specs")
    symbols = parse_code(project)
    tests = parse_tests(project)

    store.clear("spec_entry")
    store.executemany(
        "REPLACE INTO spec_entry (id, spec_key, title, body, version, status, source_file, source_line, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(_row_id(e.source_file, e.spec_key, e.title), e.spec_key, e.title, e.body,
          e.version, e.status, e.source_file, e.source_line, now) for e in specs])

    store.clear("code_symbol")
    store.executemany(
        "REPLACE INTO code_symbol (id, file_path, symbol, kind, signature, body, start_line, end_line, content_hash)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(s["id"], s["file_path"], s["symbol"], s["kind"], s["signature"], s["body"],
          s["start_line"], s["end_line"], s["content_hash"]) for s in symbols])

    store.clear("test_case")
    store.executemany(
        "REPLACE INTO test_case (id, file_path, test_name, kind, calls, body, start_line, end_line)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [(t["id"], t["file_path"], t["test_name"], t["kind"], t["calls"], t["body"],
          t["start_line"], t["end_line"]) for t in tests])

    return {"specs": len(specs), "symbols": len(symbols), "tests": len(tests)}
