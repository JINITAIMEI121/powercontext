"""Select the artefacts that apply to a development task."""

from __future__ import annotations

import re
from typing import Any

from .db import SeekdbStore, query_terms
from .state import load_state

SPEC_SQL = (
    "SELECT spec_key, title, version, status, source_file, source_line,"
    " MATCH(title, body) AGAINST(%s) AS score, body"
    " FROM spec_entry WHERE MATCH(title, body) AGAINST(%s) AND status <> 'deprecated'"
    " ORDER BY score DESC LIMIT %s")

CODE_SQL = (
    "SELECT file_path, symbol, kind, signature, start_line, end_line, content_hash,"
    " MATCH(signature, body) AGAINST(%s) AS score"
    " FROM code_symbol WHERE MATCH(signature, body) AGAINST(%s)"
    " ORDER BY score DESC LIMIT %s")

TEST_SQL = (
    "SELECT file_path, test_name, kind, calls, start_line,"
    " MATCH(test_name, body, calls) AGAINST(%s) AS score"
    " FROM test_case WHERE MATCH(test_name, body, calls) AGAINST(%s)"
    " ORDER BY score DESC LIMIT %s")


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def spec_identifiers(specs: list[dict[str, Any]]) -> list[str]:
    """Pull code identifiers that the applicable specs explicitly name."""
    names: dict[str, None] = {}
    for spec in specs:
        for token in IDENT_RE.findall(spec.get("excerpt", "")):
            for piece in token.split("."):
                if len(piece) >= 3:
                    names.setdefault(piece.lower(), None)
    return list(names)


def build_context(store: SeekdbStore, task: str, task_id: str | None = None,
                  limit: int = 6) -> dict[str, Any]:
    base_terms = query_terms(task)
    specs = []
    for row in store.fetch(SPEC_SQL, (base_terms, base_terms, limit)):
        specs.append(dict(spec_key=row[0], title=row[1], version=row[2], status=row[3],
                          source=f"{row[4]}:{row[5]}", relevance=round(float(row[6]), 4),
                          excerpt=row[7][:400]))

    code_terms = query_terms(" ".join([base_terms] + spec_identifiers(specs)
                                       + [item["symbol"] for item in []]), limit=32)
    code = []
    for row in store.fetch(CODE_SQL, (code_terms, code_terms, limit)):
        code.append(dict(file=row[0], symbol=row[1], kind=row[2], signature=row[3],
                         location=f"{row[0]}:{row[4]}-{row[5]}", content_hash=row[6],
                         relevance=round(float(row[7]), 4)))

    if not code:
        files = {spec["source"].split(":")[0] for spec in specs}
        rows = store.fetch(
            "SELECT file_path, symbol, kind, signature, start_line, end_line, content_hash"
            " FROM code_symbol ORDER BY file_path, start_line LIMIT %s", (limit,))
        code = [dict(file=r[0], symbol=r[1], kind=r[2], signature=r[3],
                     location=f"{r[0]}:{r[4]}-{r[5]}", content_hash=r[6], relevance=0.0)
                for r in rows]

    touched = {item["symbol"] for item in code}
    tests = []
    for row in store.fetch(TEST_SQL, (code_terms, code_terms, limit * 3)):
        tests.append(dict(file=row[0], test=row[1], kind=row[2], calls=row[3].split(),
                          location=f"{row[0]}:{row[4]}", relevance=round(float(row[5]), 4)))
    if not tests:
        for row in store.fetch(
                "SELECT file_path, test_name, kind, calls, start_line FROM test_case"
                " ORDER BY file_path, start_line LIMIT %s", (limit * 3,)):
            tests.append(dict(file=row[0], test=row[1], kind=row[2], calls=row[3].split(),
                              location=f"{row[0]}:{row[4]}", relevance=0.0))

    functional = [t for t in tests if t["kind"] == "functional"
                  and (not touched or touched & set(t["calls"]))]
    regression = [t for t in tests if t["kind"] == "regression"]

    state = load_state(store, task_id) if task_id else None
    return {
        "task": task,
        "query_terms": code_terms,
        "specs": specs,
        "code_to_modify": code,
        "target_tests": functional or [t for t in tests if t["kind"] == "functional"][:limit],
        "regression_tests": regression,
        "task_state": state,
    }


def render_markdown(context: dict[str, Any]) -> str:
    lines = [f"# 开发上下文：{context['task']}", ""]
    lines.append("## 适用规格")
    if context["specs"]:
        for spec in context["specs"]:
            lines.append(f"- [{spec['spec_key']}] {spec['title']} (version {spec['version']}, "
                         f"{spec['status']}) 来源 {spec['source']} 相关度 {spec['relevance']}")
            lines.append(f"  > {spec['excerpt'].splitlines()[0] if spec['excerpt'] else ''}")
    else:
        lines.append("- （无匹配规格）")
    lines.append("")
    lines.append("## 需要修改的代码")
    for item in context["code_to_modify"]:
        lines.append(f"- {item['kind']} `{item['symbol']}` @ {item['location']}"
                     f" :: {item['signature']} (hash {item['content_hash']})")
    lines.append("")
    lines.append("## 验证测试")
    lines.append("目标功能测试：")
    for item in context["target_tests"]:
        lines.append(f"- {item['location']} :: {item['test']}")
    lines.append("回归测试：")
    for item in context["regression_tests"]:
        lines.append(f"- {item['location']} :: {item['test']}")
    lines.append("")
    lines.append("## 任务状态")
    state = context.get("task_state")
    if state:
        lines.append(f"- phase: {state['phase']}")
        lines.append(f"- done: {'; '.join(state['done']) or '（无）'}")
        lines.append(f"- todo: {'; '.join(state['todo']) or '（无）'}")
        lines.append(f"- verification: {state['verification']}")
    else:
        lines.append("- （无历史状态）")
    return "\n".join(lines)
