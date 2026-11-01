"""Task progress store.

seekdb keeps the module's own state table; PowerContext is the cross-run hand-off
store used by later runs. When ``POWERCONTEXT_URI`` is set, every saved state is
written through ``POST /v1/memory/remember`` and later runs read it back with
``POST /v1/memory/search``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

POWERCONTEXT_URI = os.environ.get("POWERCONTEXT_URI", "").strip().rstrip("/")
POWERCONTEXT_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "").strip()
POWERCONTEXT_SCOPE = os.environ.get("POWERCONTEXT_SCOPE", "").strip()
POWERCONTEXT_KIND = os.environ.get("POWERCONTEXT_KIND", "task_state")

_scope_cache: str | None = POWERCONTEXT_SCOPE or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_state(store, task_id: str, task_text: str, phase: str, done: list[str],
               todo: list[str], verification: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    store.execute(
        "REPLACE INTO task_state (task_id, task_text, phase, done, todo, verification,"
        " context_hash, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (task_id, task_text, phase, json.dumps(done, ensure_ascii=False),
         json.dumps(todo, ensure_ascii=False), json.dumps(verification, ensure_ascii=False),
         _context_hash(task_id, phase, verification), now))
    receipt = remember_in_powercontext(task_id, task_text, phase, done, todo, verification, now)
    return {"powercontext": receipt}


def load_state(store, task_id: str) -> dict[str, Any] | None:
    rows = store.fetch(
        "SELECT task_text, phase, done, todo, verification, context_hash, updated_at"
        " FROM task_state WHERE task_id = %s", (task_id,))
    local = None
    if rows:
        row = rows[0]
        local = dict(source="seekdb", task_id=task_id, task_text=row[0], phase=row[1],
                     done=json.loads(row[2]), todo=json.loads(row[3]),
                     verification=json.loads(row[4]), context_hash=row[5], updated_at=row[6])
    remote = recall_state(task_id)
    if remote and (local is None or str(remote.get("updated_at") or "") >= str(local["updated_at"])):
        remote["source"] = "powercontext"
        return remote
    return local


def _payload(task_id: str, task_text: str, phase: str, done: list[str], todo: list[str],
             verification: dict[str, Any], now: str) -> dict[str, Any]:
    return {"task_id": task_id, "task_text": task_text, "phase": phase, "done": done,
            "todo": todo, "verification": verification, "updated_at": now}


def remember_in_powercontext(task_id: str, task_text: str, phase: str, done: list[str],
                             todo: list[str], verification: dict[str, Any], now: str) -> dict[str, Any]:
    body = json.dumps(_payload(task_id, task_text, phase, done, todo, verification, now),
                      ensure_ascii=False)
    if not POWERCONTEXT_URI:
        return {"enabled": False, "reason": "POWERCONTEXT_URI not set"}
    scope = _default_scope()
    if not scope:
        return {"enabled": True, "ok": False, "reason": "no scope available"}
    payload = {"scope_id": scope, "kind": POWERCONTEXT_KIND, "text": body,
               "reason": f"SDD task state for {task_id}"}
    status, response = _post("/v1/memory/remember", payload)
    entry = (response or {}).get("entry") or (response or {}).get("memory") or {}
    return {"enabled": True, "ok": status == 200, "scope_id": scope,
            "citation": entry.get("citation"), "http_status": status,
            "error": None if status == 200 else str(response)[:200]}


def recall_state(task_id: str) -> dict[str, Any] | None:
    if not POWERCONTEXT_URI:
        return None
    scope = _default_scope()
    if not scope:
        return None
    status, response = _post("/v1/memory/search",
                             {"scope_id": scope, "query": f"{POWERCONTEXT_KIND} {task_id}", "limit": 10})
    if status != 200:
        return None
    for hit in _hits(response):
        text = hit.get("text") or ""
        marker = '"task_id": "' + task_id + '"'
        if marker not in text.replace("\\\"", "\""):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if payload.get("task_id") != task_id:
            continue
        payload["citation"] = hit.get("citation")
        return payload
    return None


def _hits(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        for key in ("hits", "entries", "results", "items", "data"):
            value = response.get(key)
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
    if isinstance(response, list):
        return [v for v in response if isinstance(v, dict)]
    return []


def _default_scope() -> str | None:
    global _scope_cache
    if _scope_cache:
        return _scope_cache
    status, response = _get("/v1/scopes/default")
    if status == 200 and isinstance(response, dict):
        scope = response.get("scope_id") or (response.get("scope") or {}).get("scope_id")
        if scope:
            _scope_cache = str(scope)
            return _scope_cache
    status, response = _get("/v1/scopes")
    items = response if isinstance(response, list) else (response or {}).get("items") or []
    for item in items:
        if isinstance(item, dict) and item.get("scope_id"):
            _scope_cache = str(item["scope_id"])
            return _scope_cache
    return None


def _request(method: str, path: str, payload: dict[str, Any] | None = None):
    url = POWERCONTEXT_URI + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if POWERCONTEXT_TOKEN:
        headers["Authorization"] = f"Bearer {POWERCONTEXT_TOKEN}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8") or "{}"
            return response.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            return exc.code, {"raw": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}


def _post(path: str, payload: dict[str, Any]):
    return _request("POST", path, payload)


def _get(path: str):
    return _request("GET", path)


def _context_hash(task_id: str, phase: str, verification: dict[str, Any]) -> str:
    blob = json.dumps([task_id, phase, verification], ensure_ascii=False, sort_keys=True)
    return str(abs(hash(blob)) % (10 ** 16))
