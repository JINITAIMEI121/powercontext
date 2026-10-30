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

"""Propagate Source availability through an explicit evidence DAG."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import text

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources.journal import validate_scope_id
from powercontext.sources import SourceRef

_TABLE = "pc_source_lifecycle_nodes"
_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    scope_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    family TEXT NOT NULL,
    reference TEXT NOT NULL,
    parents TEXT NOT NULL,
    policy TEXT NOT NULL,
    revision INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    available INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (scope_id, node_id)
)
"""


class SourceLifecycleError(ValueError):
    """A lifecycle registration, event, or resolution violates its contract."""


class SourceLifecycleEvent(BaseModel):
    """One immutable availability event for one registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(gt=0, strict=True)]
    sequence: Annotated[int, Field(gt=0, strict=True)]
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("lifecycle text must not be blank")
        return value


@dataclass
class _Node:
    node_id: str
    family: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...]
    policy: Literal["all", "any"]
    revision: int
    sequence: int
    available: bool
    reason: str


class SourceLifecycleApplication:
    """Select the lifecycle service for one Scope."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._services: dict[str, ScopedSourceLifecycleService] = {}

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        validated = validate_scope_id(scope_id)
        service = self._services.get(validated)
        if service is None:
            service = ScopedSourceLifecycleService(self._runtime, validated)
            self._services[validated] = service
        return service


async def bootstrap_topic_visibility(runtime: Any) -> None:
    """Reapply persisted inactive Topic visibility before any search can run."""

    database = getattr(getattr(runtime, "_provider", None), "database", None)
    inactive = getattr(runtime, "_topic_memory_inactive", None)
    if database is None or inactive is None:
        return
    async with database.transaction() as connection:
        await connection.execute(text(_CREATE_TABLE))
        rows = (
            await connection.execute(
                text(f"SELECT scope_id, reference FROM {_TABLE} WHERE family = 'topic' AND available = 0")
            )
        ).mappings().all()
    for row in rows:
        try:
            reference = ArtifactRef.model_validate_json(row["reference"])
        except Exception:
            continue
        inactive.add((row["scope_id"], reference.artifact_id, reference.revision))


class ScopedSourceLifecycleService:
    """Persist one Scope's evidence DAG and mirror it into native Memory state."""

    def __init__(self, runtime: Any, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id
        self._nodes: dict[str, _Node] | None = None
        self._ready_lock = asyncio.Lock()
        self._mutation_lock = asyncio.Lock()

    @property
    def _database(self) -> Any:
        database = getattr(getattr(self._runtime, "_provider", None), "database", None)
        if database is None:
            raise SourceLifecycleError("lifecycle persistence is unavailable")
        return database

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        _require_node_id(node_id)
        _require_positive_int(revision, "revision")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source registration requires a SourceRef")
        await self._ensure_ready()
        await self._validate_source(source_ref)
        async with self._mutation_lock:
            nodes = self._require_nodes()
            existing = nodes.get(node_id)
            if existing is not None:
                if existing.family == "source" and existing.reference == source_ref and existing.revision == revision:
                    return
                raise SourceLifecycleError(f"node {node_id!r} is already registered with a different binding")
            node = _Node(
                node_id=node_id,
                family="source",
                reference=source_ref,
                parents=(),
                policy="all",
                revision=revision,
                sequence=0,
                available=True,
                reason="",
            )
            nodes[node_id] = node
            try:
                await self._persist_nodes((node,))
            except Exception:
                nodes.pop(node_id, None)
                raise

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        await self._register_derived(node_id, "memory", citation, parents, policy)

    async def register_topic(
        self,
        node_id: str,
        artifact: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        await self._register_derived(node_id, "topic", artifact, parents, policy)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("apply requires a SourceLifecycleEvent")
        await self._ensure_ready()
        async with self._mutation_lock:
            nodes = self._require_nodes()
            node = nodes.get(event.node_id)
            if node is None or node.family != "source":
                raise SourceLifecycleError(f"{event.node_id!r} is not a registered Source")
            if event.revision != node.revision:
                raise SourceLifecycleError("event revision does not match the registered Source revision")
            if event.sequence < node.sequence:
                raise SourceLifecycleError("event sequence must increase monotonically")
            if event.sequence == node.sequence:
                if event.available != node.available or event.reason != node.reason:
                    raise SourceLifecycleError("the same sequence cannot describe a different event")
                target = _availability(nodes)
                _record_availability(nodes, target)
                await self._apply_native_states(nodes, target)
                await self._persist_nodes(tuple(nodes.values()))
                self._sync_topic_filter(nodes, target)
                return sorted(node_id for node_id, available in target.items() if available)

            previous = (node.available, node.sequence, node.reason)
            previous_states = {node_id: item.available for node_id, item in nodes.items()}
            node.available = event.available
            node.sequence = event.sequence
            node.reason = event.reason
            target = _availability(nodes)
            _record_availability(nodes, target)
            try:
                await self._apply_native_states(nodes, target)
                await self._persist_nodes(tuple(nodes.values()))
            except Exception:
                node.available, node.sequence, node.reason = previous
                for node_id, available in previous_states.items():
                    nodes[node_id].available = available
                raise
            self._sync_topic_filter(nodes, target)
            return sorted(node_id for node_id, available in target.items() if available)

    async def resolve(
        self,
        node_id: str,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("purpose must be generation or audit")
        await self._ensure_ready()
        nodes = self._require_nodes()
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError(f"unknown lifecycle node: {node_id!r}")
        if purpose == "generation" and not _availability(nodes)[node_id]:
            raise SourceLifecycleError(f"{node_id!r} is not available for generation")
        return node.reference

    async def _register_derived(
        self,
        node_id: str,
        family: Literal["memory", "topic"],
        reference: MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        _require_node_id(node_id)
        _require_policy(policy)
        normalized_parents = _require_parents(parents)
        if family == "memory" and not isinstance(reference, MemoryCitation):
            raise SourceLifecycleError("memory registration requires a MemoryCitation")
        if family == "topic" and not isinstance(reference, ArtifactRef):
            raise SourceLifecycleError("topic registration requires an ArtifactRef")
        await self._ensure_ready()
        if family == "memory":
            await self._validate_memory(reference)
        else:
            await self._validate_topic(reference)
        async with self._mutation_lock:
            nodes = self._require_nodes()
            existing = nodes.get(node_id)
            if existing is not None:
                if (
                    existing.family == family
                    and existing.reference == reference
                    and existing.parents == normalized_parents
                    and existing.policy == policy
                ):
                    return
                raise SourceLifecycleError(f"node {node_id!r} is already registered with a different binding")
            if node_id in normalized_parents:
                raise SourceLifecycleError("a node cannot depend on itself")
            missing = [parent for parent in normalized_parents if parent not in nodes]
            if missing:
                raise SourceLifecycleError(f"parents must be registered first: {missing!r}")

            available = _policy_state(nodes, normalized_parents, policy)
            node = _Node(
                node_id=node_id,
                family=family,
                reference=reference,
                parents=normalized_parents,
                policy=policy,
                revision=0,
                sequence=0,
                available=available,
                reason="",
            )
            await self._apply_native_node(node)
            nodes[node_id] = node
            try:
                await self._persist_nodes((node,))
            except Exception:
                nodes.pop(node_id, None)
                raise
            target = _availability(nodes)
            self._sync_topic_filter(nodes, target)

    async def _ensure_ready(self) -> None:
        if self._nodes is not None:
            return
        async with self._ready_lock:
            if self._nodes is not None:
                return
            await self._ensure_schema()
            self._nodes = await self._load_nodes()
            self._sync_topic_filter(self._nodes, _availability(self._nodes))

    async def _ensure_schema(self) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(text(_CREATE_TABLE))

    async def _load_nodes(self) -> dict[str, _Node]:
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    text(
                        f"SELECT node_id, family, reference, parents, policy, revision, sequence, available, reason "
                        f"FROM {_TABLE} WHERE scope_id = :scope_id"
                    ),
                    {"scope_id": self.scope_id},
                )
            ).mappings().all()
        nodes: dict[str, _Node] = {}
        for row in rows:
            family = row["family"]
            reference = _decode_reference(family, row["reference"])
            node = _Node(
                node_id=row["node_id"],
                family=family,
                reference=reference,
                parents=tuple(json.loads(row["parents"])),
                policy=row["policy"],
                revision=int(row["revision"]),
                sequence=int(row["sequence"]),
                available=bool(row["available"]),
                reason=row["reason"] or "",
            )
            nodes[node.node_id] = node
        return nodes

    async def _persist_nodes(self, nodes: tuple[_Node, ...]) -> None:
        async with self._database.transaction() as connection:
            for node in nodes:
                parameters = {
                    "scope_id": self.scope_id,
                    "node_id": node.node_id,
                    "family": node.family,
                    "reference": json.dumps(node.reference.model_dump(mode="json"), ensure_ascii=False),
                    "parents": json.dumps(list(node.parents), ensure_ascii=False),
                    "policy": node.policy,
                    "revision": node.revision,
                    "sequence": node.sequence,
                    "available": int(node.available),
                    "reason": node.reason,
                }
                result = await connection.execute(
                    text(
                        f"UPDATE {_TABLE} SET family = :family, reference = :reference, parents = :parents, "
                        f"policy = :policy, revision = :revision, sequence = :sequence, available = :available, "
                        f"reason = :reason WHERE scope_id = :scope_id AND node_id = :node_id"
                    ),
                    parameters,
                )
                if result.rowcount == 0:
                    await connection.execute(
                        text(
                            f"INSERT INTO {_TABLE} "
                            f"(scope_id, node_id, family, reference, parents, policy, revision, sequence, available, reason) "
                            f"VALUES (:scope_id, :node_id, :family, :reference, :parents, :policy, :revision, "
                            f":sequence, :available, :reason)"
                        ),
                        parameters,
                    )

    async def _validate_source(self, source_ref: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type, source_ref.source_id
            )
        except Exception as error:
            raise SourceLifecycleError("registered Source is not readable") from error

    async def _validate_memory(self, citation: MemoryCitation) -> None:
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except Exception as error:
            raise SourceLifecycleError("registered Memory citation is not readable") from error

    async def _validate_topic(self, artifact: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                "topic-memory", artifact.artifact_id, artifact.revision
            )
        except Exception as error:
            raise SourceLifecycleError("registered Topic Memory revision is not readable") from error

    async def _apply_native_node(self, node: _Node) -> None:
        if node.family == "memory":
            await self._apply_memory_state(node, node.available)
        elif node.family == "topic":
            self._set_topic_active(node.reference, node.available)

    async def _apply_native_states(self, nodes: dict[str, _Node], target: dict[str, bool]) -> None:
        for node in nodes.values():
            if node.family == "memory":
                await self._apply_memory_state(node, target[node.node_id])
            elif node.family == "topic":
                self._set_topic_active(node.reference, target[node.node_id])

    async def _apply_memory_state(self, node: _Node, available: bool) -> None:
        citation = node.reference
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory lifecycle node requires a MemoryCitation")
        async with self._runtime._context(self.scope_id) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                manifest_item = next(
                    (item for item in current.content.manifest.entries if item.entry_id == citation.entry_id),
                    None,
                )
                if manifest_item is None or manifest_item.entry_version_id != citation.entry_version_id:
                    raise SourceLifecycleError("registered Memory entry version is not current")
                entry = next(
                    (
                        item
                        for item in await service.entries(current)
                        if item.entry_id == citation.entry_id
                        and item.entry_version_id == citation.entry_version_id
                    ),
                    None,
                )
                if entry is None:
                    raise SourceLifecycleError("registered Memory entry version is not readable")
                if available and manifest_item.state == "inactive":
                    await service.reactivate(current, entries=(entry,), reason="source-lifecycle")
                elif not available and manifest_item.state == "active":
                    await service.forget(current, entries=(entry,), reason="source-lifecycle")

    def _set_topic_active(self, reference: SourceRef | MemoryCitation | ArtifactRef, available: bool) -> None:
        if not isinstance(reference, ArtifactRef):
            raise SourceLifecycleError("topic lifecycle node requires an ArtifactRef")
        inactive = getattr(self._runtime, "_topic_memory_inactive", None)
        if inactive is None:
            raise SourceLifecycleError("topic visibility is unavailable")
        key = (self.scope_id, reference.artifact_id, reference.revision)
        if available:
            inactive.discard(key)
        else:
            inactive.add(key)

    def _sync_topic_filter(self, nodes: dict[str, _Node], target: dict[str, bool]) -> None:
        for node in nodes.values():
            if node.family == "topic":
                self._set_topic_active(node.reference, target[node.node_id])

    def _require_nodes(self) -> dict[str, _Node]:
        if self._nodes is None:
            raise SourceLifecycleError("lifecycle state is not loaded")
        return self._nodes


def _decode_reference(family: str, value: str) -> SourceRef | MemoryCitation | ArtifactRef:
    if family == "source":
        return SourceRef.model_validate_json(value)
    if family == "memory":
        return MemoryCitation.model_validate_json(value)
    if family == "topic":
        return ArtifactRef.model_validate_json(value)
    raise SourceLifecycleError(f"unknown lifecycle family: {family!r}")


def _require_node_id(node_id: str) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node_id must be a non-blank string")


def _require_positive_int(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceLifecycleError(f"{field} must be a positive integer")


def _require_policy(policy: str) -> None:
    if policy not in ("all", "any"):
        raise SourceLifecycleError("policy must be 'all' or 'any'")


def _require_parents(parents: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(parents, (tuple, list)) or not parents:
        raise SourceLifecycleError("derived nodes require at least one parent")
    normalized = tuple(parents)
    if any(not isinstance(parent, str) or not parent.strip() for parent in normalized):
        raise SourceLifecycleError("parent ids must be non-blank strings")
    if len(set(normalized)) != len(normalized):
        raise SourceLifecycleError("parent ids must be unique")
    return normalized


def _policy_state(nodes: dict[str, _Node], parents: tuple[str, ...], policy: str) -> bool:
    values = [_node_available(nodes[parent], nodes) for parent in parents]
    return all(values) if policy == "all" else any(values)


def _record_availability(nodes: dict[str, _Node], target: dict[str, bool]) -> None:
    for node_id, available in target.items():
        nodes[node_id].available = available


def _availability(nodes: dict[str, _Node]) -> dict[str, bool]:
    cache: dict[str, bool] = {}

    def evaluate(node_id: str) -> bool:
        if node_id in cache:
            return cache[node_id]
        node = nodes[node_id]
        if node.family == "source":
            value = node.available
        else:
            parents = tuple(evaluate(parent) for parent in node.parents)
            value = all(parents) if node.policy == "all" else any(parents)
        cache[node_id] = value
        return value

    for node_id in nodes:
        evaluate(node_id)
    return cache


def _node_available(node: _Node, nodes: dict[str, _Node]) -> bool:
    if node.family == "source":
        return node.available
    values = [_node_available(nodes[parent], nodes) for parent in node.parents]
    return all(values) if node.policy == "all" else any(values)


__all__ = [
    "ScopedSourceLifecycleService",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "bootstrap_topic_visibility",
]
