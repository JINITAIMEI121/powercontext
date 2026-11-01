"""Task progress store: seekdb table plus an optional PowerContext backend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

POWERCONTEXT_URI = os.environ.get("POWERCONTEXT_URI", "").strip()


def save_state(store, task_id: str, task_text: str, phase: str, done: list[str],
               todo: list[str], verification: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.execute(
        "REPLACE INTO task_state (task_id, task_text, phase, done, todo, verification,"
        " context_hash, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (task_id, task_text, phase, json.dumps(done, ensure_ascii=False),
         json.dumps(todo, ensure_ascii=False), json.dumps(verification, ensure_ascii=False),
         _context_hash(task_id, phase, verification), now))
    if POWERCONTEXT_URI:
        _remember_in_powercontext(task_id, phase, done, todo, verification, now)


def load_state(store, task_id: str) -> dict[str, Any] | None:
    rows = store.fetch(
        "SELECT task_text, phase, done, todo, verification, context_hash, updated_at"
        " FROM task_state WHERE task_id = %s", (task_id,))
    if not rows:
        return None
    row = rows[0]
    return dict(task_id=task_id, task_text=row[0], phase=row[1],
                done=json.loads(row[2]), todo=json.loads(row[3]),
                verification=json.loads(row[4]), context_hash=row[5], updated_at=row[6])


def recall_state(task_id: str) -> dict[str, Any] | None:
    """Read the stored hand-off back from PowerContext (best effort)."""
    if not POWERCONTEXT_URI:
        return None
    payload = _powercontext_call("recall", {"task_id": task_id})
    return payload if isinstance(payload, dict) else None


def _context_hash(task_id: str, phase: str, verification: dict[str, Any]) -> str:
    blob = json.dumps([task_id, phase, verification], ensure_ascii=False, sort_keys=True)
    return str(abs(hash(blob)) % (10 ** 16))


def _remember_in_powercontext(task_id, phase, done, todo, verification, now) -> None:
    _powercontext_call("remember", {
        "task_id": task_id, "phase": phase, "done": done, "todo": todo,
        "verification": verification, "recorded_at": now})


def _powercontext_call(action: str, payload: dict[str, Any]) -> Any:
    binary = shutil.which("powercontext")
    if not binary:
        return None
    command = [binary, "code", "remember" if action == "remember" else "recall",
               "--service", POWERCONTEXT_URI, "--json", json.dumps(payload, ensure_ascii=False)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"raw": completed.stdout.strip()}
