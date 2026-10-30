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

"""HTTP scoring-service reranking for Memory search candidates."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score candidates with one HTTP service in batches, then select globally."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty string")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        candidate_count = len(candidates)
        if candidate_count < 1:
            raise ValueError("rerank requires at least one candidate")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > candidate_count:
            raise ValueError("rerank limit must be between one and the candidate count")

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, candidate_count, self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            payload = {
                "query": query,
                "candidates": [{"rank": rank, "text": hit.text} for rank, hit in zip(ranks, batch, strict=True)],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            scores.update(_parse_scores(response, ranks))

        selected = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _score in selected),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, expected_ranks: tuple[int, ...]) -> dict[int, float]:
    try:
        payload: Any = response.json()
    except ValueError as error:
        raise ValueError("reranker response must be valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly the scores field")
    raw_scores = payload["scores"]
    if not isinstance(raw_scores, list):
        raise ValueError("reranker scores must be a list")

    parsed: dict[int, float] = {}
    for item in raw_scores:
        if not isinstance(item, dict) or set(item) != {"rank", "score"}:
            raise ValueError("each reranker score must contain exactly rank and score")
        rank = item["rank"]
        score = item["score"]
        if isinstance(rank, bool) or not isinstance(rank, int):
            raise ValueError("reranker rank must be an integer")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("reranker score must be numeric")
        value = float(score)
        if not math.isfinite(value):
            raise ValueError("reranker score must be finite")
        if rank in parsed:
            raise ValueError("reranker scores must cover each rank exactly once")
        parsed[rank] = value

    if set(parsed) != set(expected_ranks):
        raise ValueError("reranker scores must cover every rank in the batch exactly once")
    return parsed


__all__ = ["DEFAULT_POLICY_ID", "HTTPMemoryReranker"]
