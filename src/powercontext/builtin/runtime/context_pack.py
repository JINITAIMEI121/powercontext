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

"""Versioned context packs and prefix-reusing incremental deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContextPackError(ValueError):
    """A Context Pack or delta violates the wire contract."""


def _dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest_for(items: tuple[bytes, ...]) -> str:
    canonical = _dumps(
        {
            "schema": PACK_SCHEMA,
            "items": [base64.b64encode(item).decode("ascii") for item in items],
        }
    )
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("items must be a list")
    items: list[bytes] = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError("items must contain Base64 strings")
        try:
            items.append(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("items must contain valid Base64") from error
    return tuple(items)


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete, self-describing context payload."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        normalized = tuple(items)
        if any(not isinstance(item, bytes) for item in normalized):
            raise ContextPackError("items must be bytes")
        digest = _digest_for(normalized)
        byte_count = 0
        while True:
            payload = {
                "schema": PACK_SCHEMA,
                "items": _encode_items(normalized),
                "digest": digest,
                "byte_count": byte_count,
            }
            actual = len(_dumps(payload))
            if actual == byte_count:
                break
            byte_count = actual
        return cls(items=normalized, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = 512) -> ContextPack:
        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if isinstance(content, str):
            try:
                data = content.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ContextPackError("content must be valid UTF-8") from error
        elif isinstance(content, bytes):
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ContextPackError("content must be valid UTF-8") from error
            data = content
        else:
            raise ContextPackError("content must be str or bytes")
        return cls.build(_chunk_utf8(data, chunk_bytes))

    def to_bytes(self) -> bytes:
        return _dumps(
            {
                "schema": PACK_SCHEMA,
                "items": _encode_items(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        try:
            payload: Any = json.loads(data)
        except (TypeError, ValueError) as error:
            raise ContextPackError("pack must be valid JSON") from error
        if not isinstance(payload, dict) or set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack must contain exactly schema, items, digest, and byte_count")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unsupported pack schema")
        items = _decode_items(payload["items"])
        digest = payload["digest"]
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise ContextPackError("digest must be a sha256 digest")
        if digest != _digest_for(items):
            raise ContextPackError("pack digest does not match its items")
        byte_count = payload["byte_count"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int):
            raise ContextPackError("byte_count must be an integer")
        if byte_count != len(data):
            raise ContextPackError("pack byte_count does not match its wire size")
        return cls(items=items, digest=digest, byte_count=byte_count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A prefix-preserving delta from one pack to another."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.base_digest, str) or not _DIGEST_PATTERN.fullmatch(self.base_digest):
            raise ContextPackError("base_digest must be a sha256 digest")
        if not isinstance(self.target_digest, str) or not _DIGEST_PATTERN.fullmatch(self.target_digest):
            raise ContextPackError("target_digest must be a sha256 digest")
        if isinstance(self.prefix_items, bool) or not isinstance(self.prefix_items, int) or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        if not isinstance(self.appended, tuple) or any(not isinstance(item, bytes) for item in self.appended):
            raise ContextPackError("appended must be a tuple of bytes")

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("between requires two ContextPack values")
        prefix = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _dumps(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        try:
            payload: Any = json.loads(data)
        except (TypeError, ValueError) as error:
            raise ContextPackError("delta must be valid JSON") from error
        expected = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ContextPackError("delta must contain exactly schema, base_digest, target_digest, prefix_items, appended")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unsupported delta schema")
        prefix = payload["prefix_items"]
        if isinstance(prefix, bool) or not isinstance(prefix, int) or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        return cls(
            base_digest=payload["base_digest"],
            target_digest=payload["target_digest"],
            prefix_items=prefix,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from one base and its delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("apply requires a ContextPack and a ContextPackDelta")
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base_digest does not match the supplied base")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base item count")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack digest does not match target_digest")
    return target


def _chunk_utf8(data: bytes, limit: int) -> tuple[bytes, ...]:
    text = data.decode("utf-8")
    chunks: list[bytes] = []
    current = bytearray()
    for character in text:
        encoded = character.encode("utf-8")
        if len(encoded) > limit:
            raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")
        if len(current) + len(encoded) > limit:
            chunks.append(bytes(current))
            current.clear()
        current.extend(encoded)
    if current:
        chunks.append(bytes(current))
    return tuple(chunks)


__all__ = [
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
