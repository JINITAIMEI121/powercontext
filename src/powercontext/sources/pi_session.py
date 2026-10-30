# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pi session branches as immutable, incrementally captured evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_CONNECTOR_NAME = "pi-session-history"
PI_CONNECTOR_VERSION = "1"
_CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """One complete Pi session JSONL document and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None

    @model_validator(mode="after")
    def validate_input(self) -> PiSessionInput:
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")
        if self.leaf_id is not None and (not isinstance(self.leaf_id, str) or not self.leaf_id.strip()):
            raise ValueError("leaf_id must be a non-blank string")
        return self


class PiEventInput(BaseModel):
    """One visible Pi event on the selected branch."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]

    @model_validator(mode="after")
    def validate_event(self) -> PiEventInput:
        for field in ("session_id", "event_id", "event_type", "role", "content"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-blank string")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id.strip()):
            raise ValueError("parent_id must be null or a non-blank string")
        if not isinstance(self.record, dict):
            raise ValueError("record must be the complete input object")
        return self


class PiEventSource(Source):
    """A captured Pi event with a stable session/event identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the stable, delimiter-safe identity for one Pi event."""

    for field, value in (("session_id", session_id), ("event_id", event_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-blank string")
    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"Pi event source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return the visible events from root to the selected leaf."""

    session_id, records, by_id = _parse_session(value.content)
    if value.leaf_id is None:
        if not records:
            return ()
        leaf_id = records[-1]["id"]
    else:
        leaf_id = value.leaf_id
        if leaf_id not in by_id:
            raise ValueError("leaf_id does not identify a session record")

    chain: list[dict[str, Any]] = []
    current: str | None = leaf_id
    while current is not None:
        record = by_id[current]
        chain.append(record)
        current = record.get("parentId")
    chain.reverse()

    events: list[PiEventInput] = []
    for record in chain:
        event = _visible_event(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


def _parse_session(content: bytes) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session must be valid UTF-8") from error
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session must contain a header record")

    header = _load_object(lines[0])
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session header must declare type=session and version=3")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session header must contain a non-blank id")

    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for line in lines[1:]:
        record = _load_object(line)
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every Pi record must contain a non-blank id")
        if event_id in by_id:
            raise ValueError("Pi record ids must be unique")
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id.strip() or parent_id not in by_id:
                raise ValueError("Pi record parentId must reference an earlier record")
        by_id[event_id] = record
        records.append(record)
    return session_id, records, by_id


def _load_object(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except ValueError as error:
        raise ValueError("Pi session lines must be valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Pi session records must be JSON objects")
    return value


def _visible_event(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    event_type = record.get("type")
    if event_type in ("compaction", "branch_summary"):
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return _event(session_id, record, event_type, event_type, f"{event_type}: {summary}")

    if event_type == "custom_message":
        body = _content_text(record.get("content"))
        if not body.strip():
            return None
        return _event(session_id, record, event_type, "custom", f"custom: {body}")

    if event_type != "message":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or not role.strip():
        return None

    if role == "toolResult":
        name = message.get("toolName", "")
        call_id = message.get("toolCallId", "")
        body = _content_text(message.get("content"))
        return _event(session_id, record, event_type, role, f"{role}: [tool_result {name} {call_id}] {body}")

    if role == "bashExecution":
        command = message.get("command", "")
        output = message.get("output", "")
        return _event(session_id, record, event_type, role, f"{role}: $ {command}\n{output}")

    parts: list[str] = []
    raw_content = message.get("content")
    if isinstance(raw_content, str):
        parts.append(raw_content)
    elif isinstance(raw_content, list):
        for block in raw_content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif block_type == "toolCall":
                name = block.get("name", "")
                call_id = block.get("id", "")
                arguments = block.get("arguments", {})
                serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                parts.append(f"[tool_call {name} {call_id}] {serialized}")
    body = "\n".join(parts)
    if not body.strip():
        return None
    return _event(session_id, record, event_type, role, f"{role}: {body}")


def _event(
    session_id: str,
    record: dict[str, Any],
    event_type: str,
    role: str,
    content: str,
) -> PiEventInput:
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record.get("parentId"),
        event_type=event_type,
        role=role,
        content=content,
        record=record,
    )


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


class PiEventSourceAdapter:
    """Resolve and read Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Project one Pi event as standard text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Incrementally import visible events from one selected Pi branch."""

    name = PI_CONNECTOR_NAME
    version = PI_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        if leaf_id is not None and (not isinstance(leaf_id, str) or not leaf_id.strip()):
            raise ValueError("leaf_id must be a non-blank string")
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        content = self._path.read_bytes()
        session_id, _records, by_id = _parse_session(content)
        events = parse_pi_session(PiSessionInput(content=content, leaf_id=self._leaf_id))
        checkpoint = _load_checkpoint(session.checkpoint)
        if checkpoint["session_id"] is None:
            checkpoint["session_id"] = session_id
        elif checkpoint["session_id"] != session_id:
            raise ValueError("Pi session id changed since the previous checkpoint")

        stored = checkpoint["events"]
        for event_id in tuple(stored):
            record = by_id.get(event_id)
            if record is None:
                raise ValueError("a previously imported Pi event is missing from the session")
            event = _visible_event(session_id, record)
            if event is None or _event_digest(event) != stored[event_id]["digest"]:
                raise ValueError("a previously imported Pi event was rewritten")
            if stored[event_id]["source_id"] != pi_event_source_id(session_id, event_id):
                raise ValueError("a previously imported Pi event changed identity")

        for event in events:
            existing = stored.get(event.event_id)
            if existing is not None:
                if existing["digest"] != _event_digest(event):
                    raise ValueError("a previously imported Pi event was rewritten")
                continue
            outcome = await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            if outcome.status.value == "accepted":
                stored[event.event_id] = {
                    "digest": _event_digest(event),
                    "source_id": pi_event_source_id(event.session_id, event.event_id),
                }

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": _CHECKPOINT_VERSION, "session_id": session_id, "events": stored},
        )


def _load_checkpoint(value: object) -> dict[str, Any]:
    if value is None:
        return {"version": _CHECKPOINT_VERSION, "session_id": None, "events": {}}
    if not isinstance(value, dict):
        raise ValueError("Pi connector checkpoint must be an object")
    if value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("unsupported Pi connector checkpoint version")
    session_id = value.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("Pi connector checkpoint session_id must be a string or null")
    events = value.get("events")
    if not isinstance(events, dict):
        raise ValueError("Pi connector checkpoint events must be an object")
    normalized: dict[str, dict[str, str]] = {}
    for event_id, item in events.items():
        if not isinstance(event_id, str) or not isinstance(item, dict):
            raise ValueError("Pi connector checkpoint events are malformed")
        digest = item.get("digest")
        source_id = item.get("source_id")
        if not isinstance(digest, str) or not isinstance(source_id, str):
            raise ValueError("Pi connector checkpoint events are malformed")
        normalized[event_id] = {"digest": digest, "source_id": source_id}
    return {"version": _CHECKPOINT_VERSION, "session_id": session_id, "events": normalized}


def _event_digest(event: PiEventInput) -> str:
    payload = json.dumps(
        {
            "session_id": event.session_id,
            "event_id": event.event_id,
            "parent_id": event.parent_id,
            "event_type": event.event_type,
            "role": event.role,
            "content": event.content,
            "record": event.record,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "PI_CONNECTOR_NAME",
    "PI_CONNECTOR_VERSION",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
