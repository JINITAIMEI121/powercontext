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

"""Cross-call Embedding request coalescing with a bounded success cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(frozen=True, slots=True)
class _TextResult:
    vector: EmbeddingVector
    batch_id: int | None
    usage: InferenceUsage | None


class CoalescingEmbeddingModel:
    """Deduplicate identical texts across concurrent callers and cache successes."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1_000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._in_flight: dict[str, asyncio.Future[_TextResult]] = {}
        self._pending: dict[str, asyncio.Future[_TextResult]] = {}
        self._flush_task: asyncio.Task[None] | None = None
        self._batch_counter = 0
        self.profile = model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        values = tuple(texts)
        if not values:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique = tuple(dict.fromkeys(values))
        results: dict[str, _TextResult] = {}
        waiting: list[tuple[str, asyncio.Future[_TextResult]]] = []

        for text in unique:
            cached = self._cache_get(text)
            if cached is not None:
                results[text] = _TextResult(vector=cached, batch_id=None, usage=None)
                continue
            future = self._in_flight.get(text)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                future.add_done_callback(_consume_future_outcome)
                self._in_flight[text] = future
                self._pending[text] = future
                self._schedule_flush()
            waiting.append((text, future))

        for text, future in waiting:
            results[text] = await asyncio.shield(future)

        usages: dict[int, InferenceUsage] = {}
        for text in unique:
            result = results[text]
            if result.batch_id is not None and result.usage is not None:
                usages[result.batch_id] = result.usage
        usage = _sum_usage(tuple(usages.values()))
        return EmbeddingResult(vectors=tuple(results[text].vector for text in values), usage=usage)

    def _cache_get(self, text: str) -> EmbeddingVector | None:
        if self._cache_size <= 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _schedule_flush(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        try:
            if self._coalesce_seconds > 0:
                await asyncio.sleep(self._coalesce_seconds)
            else:
                await asyncio.sleep(0)
            pending = self._pending
            self._pending = {}
            self._flush_task = None
            if not pending:
                return
            items = tuple(pending.items())
            for start in range(0, len(items), self._max_batch_size):
                batch = items[start : start + self._max_batch_size]
                asyncio.create_task(self._run_batch(batch))
        except asyncio.CancelledError:
            self._flush_task = None
            raise

    async def _run_batch(self, batch: tuple[tuple[str, asyncio.Future[_TextResult]], ...]) -> None:
        texts = tuple(text for text, _future in batch)
        futures = tuple(future for _text, future in batch)
        try:
            result = await self._model.embed(texts)
            if len(result.vectors) != len(texts):
                raise ValueError("embedding vector count does not match the text count")
            batch_id = self._batch_counter
            self._batch_counter += 1
            for text, future, vector in zip(texts, futures, result.vectors, strict=True):
                normalized = tuple(float(value) for value in vector)
                self._cache_put(text, normalized)
                self._in_flight.pop(text, None)
                if not future.done():
                    future.set_result(_TextResult(vector=normalized, batch_id=batch_id, usage=result.usage))
        except Exception as error:
            for text, future in batch:
                self._in_flight.pop(text, None)
                if not future.done():
                    future.set_exception(error)


def _sum_usage(usages: tuple[InferenceUsage, ...]) -> InferenceUsage:
    requests = sum(usage.requests for usage in usages)
    return InferenceUsage(
        requests=requests,
        input_tokens=_sum_optional(usage.input_tokens for usage in usages),
        output_tokens=_sum_optional(usage.output_tokens for usage in usages),
    )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _consume_future_outcome(future: asyncio.Future[_TextResult]) -> None:
    if not future.cancelled():
        future.exception()


__all__ = ["CoalescingEmbeddingModel"]
