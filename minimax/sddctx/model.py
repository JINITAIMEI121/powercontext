"""Model providers: MiniMax (OpenAI compatible) and an offline mock."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

MINIMAX_URL = os.environ.get("MINIMAX_API_URL", "https://api.minimax.cn/v1/chat/completions")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3.1-Flash")
MAX_TOKENS = int(os.environ.get("MINIMAX_MAX_TOKENS", "24576"))
TIMEOUT = int(os.environ.get("MINIMAX_TIMEOUT", "600"))
KEY_FILES = [Path.home() / ".config/oceanbase-2026/minimax_plan_key",
             Path.home() / ".config/oceanbase-2026/minimax_key"]


class ModelError(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if key:
        return key
    for path in KEY_FILES:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    return ""


def complete(prompt: str, provider: str, system: str) -> dict[str, Any]:
    if provider == "mock":
        return _mock(prompt)
    if provider == "minimax":
        return _minimax(prompt, system)
    raise ModelError(f"unknown provider {provider}")


def _minimax(prompt: str, system: str) -> dict[str, Any]:
    key = api_key()
    if not key:
        raise ModelError("missing MiniMax API key (set MINIMAX_API_KEY)")
    body = json.dumps({
        "model": MINIMAX_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0, "max_tokens": MAX_TOKENS}).encode("utf-8")
    request = urllib.request.Request(MINIMAX_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ModelError(f"MiniMax request failed: {exc}") from exc
    if payload.get("base_resp", {}).get("status_code"):
        raise ModelError(f"MiniMax error: {payload['base_resp']}")
    choices = payload.get("choices") or []
    if not choices:
        raise ModelError(f"MiniMax returned no choices: {str(payload)[:200]}")
    message = choices[0]["message"]
    content = message.get("content") or ""
    if not content.strip():
        usage = payload.get("usage", {})
        raise ModelError(
            "MiniMax returned no answer text (reasoning budget exhausted: "
            f"usage={usage}, reasoning_tokens="
            f"{usage.get('completion_tokens_details', {}).get('reasoning_tokens')})")
    return {"provider": "minimax", "model": payload.get("model", MINIMAX_MODEL),
            "usage": payload.get("usage", {}), "content": content}


def _mock(prompt: str) -> dict[str, Any]:
    """Deterministic offline stand-in used only for plumbing checks."""
    here = Path(__file__).resolve().parent
    edits = [
        {"path": "src/orders/service.py", "content": (here / "_mock_service.py").read_text(encoding="utf-8")},
        {"path": "src/orders/repository.py", "content": (here / "_mock_repository.py").read_text(encoding="utf-8")},
    ]
    return {"provider": "mock", "model": "mock-1", "usage": {},
            "content": "```json\n" + json.dumps({"edits": edits, "notes": "mock"},
                                                 ensure_ascii=False) + "\n```"}
