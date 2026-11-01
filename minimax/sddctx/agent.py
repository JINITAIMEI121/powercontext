"""Run one spec-driven development task end to end."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import model as model_module
from .retrieve import build_context, render_markdown
from .state import load_state, save_state

SYSTEM_PROMPT = (
    "你是严格执行规格驱动开发的代码助手。只依据给定上下文中的规格修改代码，"
    "保持未提及行为不变，不得新增未在规格中声明的要求。"
    '必须只输出一个 JSON 代码块：{"edits": [{"path": 相对路径, "content": 文件完整新内容}], '
    '"notes": 字符串}。path 必须是上下文中出现过的文件，content 是整份新文件。')


def run_task(store, project: Path, task: str, task_id: str, provider: str,
             apply: bool = True) -> dict[str, Any]:
    project = Path(project)
    context = build_context(store, task, task_id=task_id)
    prompt = _build_prompt(context, task, project)
    run_dir = _run_dir(project)

    before = _run_tests(project, context)
    _write(run_dir / "context.md", render_markdown(context))
    _write(run_dir / "prompt.txt", prompt)
    (run_dir / "before_tests.json").write_text(json.dumps(before, ensure_ascii=False, indent=2))

    response = model_module.complete(prompt, provider, SYSTEM_PROMPT)
    _write(run_dir / "model_response.txt", response["content"])
    edits = _parse_edits(response["content"])

    result: dict[str, Any] = {
        "run_dir": str(run_dir), "provider": response["provider"], "model": response["model"],
        "usage": response["usage"], "before": before, "edits": [e["path"] for e in edits],
        "notes": edits and response["content"][:200]}

    if apply and edits:
        backup = _apply_edits(project, edits, run_dir)
        after = _run_tests(project, context)
        (run_dir / "after_tests.json").write_text(json.dumps(after, ensure_ascii=False, indent=2))
        _write_diffs(run_dir, backup, project)
        result["after"] = after
        result["changed"] = sorted(backup)
        verification = {
            "target_before_pass": before["target"]["passed"],
            "target_after_pass": after["target"]["passed"],
            "regression_before_pass": before["regression"]["passed"],
            "regression_after_pass": after["regression"]["passed"],
            "provider": response["provider"], "model": response["model"],
            "run_dir": str(run_dir)}
        phase = "verified" if (after["target"]["passed"] and after["regression"]["passed"]) else "failed"
        result["state_receipt"] = save_state(store, task_id, task, phase,
                   done=[f"由 {response['provider']} 生成并应用 {len(edits)} 处修改",
                         f"目标测试: {after['target']['passed']}",
                         f"回归测试: {after['regression']['passed']}"],
                   todo=[] if phase == "verified" else ["修复失败用例后重跑"],
                   verification=verification)
    else:
        result["after"] = None
    return result


def refresh_state(store, project: Path, task: str, task_id: str, provider: str) -> dict[str, Any]:
    """Second run: reuse persisted state and only fill the remaining todo items."""
    state = load_state(store, task_id)
    context = build_context(store, task, task_id=task_id)
    if not state:
        return {"phase": "no-state", "context": context}
    follow_up = {
        "task_id": task_id,
        "phase_from_state": state["phase"],
        "todo_from_state": state["todo"],
        "verification_from_state": state["verification"],
        "specs_reused": [spec["spec_key"] for spec in context["specs"]],
    }
    if state["phase"] == "verified" and not state["todo"]:
        follow_up["decision"] = "无需再次修改，状态显示目标与回归测试均已通过"
        return follow_up
    prompt = _build_prompt(context, task) + "\n\n已有任务状态（来自 PowerContext/seekdb）：\n" + \
        json.dumps(state, ensure_ascii=False) + "\n请只处理 todo 项。"
    response = model_module.complete(prompt, provider, SYSTEM_PROMPT)
    follow_up["provider"] = response["provider"]
    follow_up["content_head"] = response["content"][:400]
    return follow_up


def _build_prompt(context: dict[str, Any], task: str, project: Path | None = None) -> str:
    project = Path(project) if project else Path.cwd()
    files: list[str] = []
    for item in context["code_to_modify"]:
        path = project / item["file"]
        if path.exists():
            files.append(f"--- {item['file']} ---\n{path.read_text(encoding='utf-8')}")
    specs = "\n".join(f"[{s['spec_key']}] {s['title']} (v{s['version']}) 来源 {s['source']}\n{s['excerpt']}"
                       for s in context["specs"])
    tests = "\n".join(f"{t['location']} :: {t['test']} ({t['kind']})"
                       for t in context["target_tests"] + context["regression_tests"])
    joined = "\n\n".join(files)
    return (f"任务：{task}\n\n# 适用规格\n{specs}\n\n# 需要修改的文件\n"
            + joined + f"\n\n# 必须通过的测试\n{tests}\n")


def _run_tests(project: Path, context: dict[str, Any]) -> dict[str, Any]:
    target = sorted({t["file"] for t in context["target_tests"]})
    regression = sorted({t["file"] for t in context["regression_tests"]})
    return {"target": _pytest(project, target), "regression": _pytest(project, regression)}


def _pytest(project: Path, files: list[str]) -> dict[str, Any]:
    if not files:
        return {"passed": False, "returncode": 1, "output": "no test file selected", "files": []}
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *files],
                               cwd=project, capture_output=True, text=True)
    output = (completed.stdout + completed.stderr)[-4000:]
    return {"passed": completed.returncode == 0, "returncode": completed.returncode,
            "files": files, "output": output}


def _parse_edits(content: str) -> list[dict[str, str]]:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    blob = match.group(1) if match else content
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    edits = payload.get("edits") or []
    cleaned = []
    for edit in edits:
        path = str(edit.get("path", "")).strip()
        body = edit.get("content")
        if not path or body is None or path.startswith("/") or ".." in path:
            continue
        cleaned.append({"path": path, "content": str(body)})
    return cleaned


def _apply_edits(project: Path, edits: list[dict[str, str]], run_dir: Path) -> dict[str, str]:
    backup: dict[str, str] = {}
    for edit in edits:
        path = project / edit["path"]
        if path.exists():
            backup[edit["path"]] = path.read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(edit["content"], encoding="utf-8")
    (run_dir / "applied_edits.json").write_text(
        json.dumps({k: v for k, v in backup.items()}, ensure_ascii=False, indent=2))
    return backup


def _write_diffs(run_dir: Path, backup: dict[str, str], project: Path) -> None:
    chunks: list[str] = []
    for path, old in backup.items():
        new = (project / path).read_text(encoding="utf-8")
        chunks.append("".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}")))
    _write(run_dir / "changes.patch", "\n".join(chunks))


def _run_dir(project: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = project / ".sdd-runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
