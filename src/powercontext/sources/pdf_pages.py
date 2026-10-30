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

"""Complete PDF page extraction and exact page-level evidence retrieval."""

from __future__ import annotations

import hashlib
import io
import re
from urllib.parse import quote

from pydantic import BaseModel, model_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
_QUERY_TOKEN = re.compile(r"[A-Za-z]+|\d+")
_QUERY_STOPWORDS = frozenset(
    "a an and are as at be by did do does for from had has have how in is it its of on or the to was were what "
    "when where which who why with year years company fiscal".split()
)


class PDFDocumentInput(BaseModel):
    """One complete PDF document to split into physical page Sources."""

    document_id: str
    content: bytes

    @model_validator(mode="after")
    def validate_input(self) -> PDFDocumentInput:
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id must be a non-blank string")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")
        return self


class PDFPageInput(BaseModel):
    """One physical PDF page with document and revision identity."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_page(self) -> PDFPageInput:
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id must be a non-blank string")
        if not isinstance(self.document_sha256, str) or len(self.document_sha256) != 64:
            raise ValueError("document_sha256 must be a SHA-256 hex digest")
        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int) or self.page_number < 1:
            raise ValueError("page_number must be a positive integer")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        return self


class PDFPageSource(Source):
    """A captured physical PDF page."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the stable identity for one page of one exact PDF revision."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-blank string")
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise ValueError("document_sha256 must be a SHA-256 hex digest")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"PDF page source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page from one complete PDF before returning any page."""

    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")
    if not content.lstrip().startswith(b"%PDF-"):
        raise ValueError("content is not a PDF document")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                raise ValueError("PDF requires a user password")
        pages = reader.pages
        if len(pages) < 1:
            raise ValueError("PDF must contain at least one physical page")
        digest = hashlib.sha256(content).hexdigest()
        extracted: list[PDFPageInput] = []
        for index, page in enumerate(pages, start=1):
            text = page.extract_text() or ""
            extracted.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=index,
                    text=text.strip(),
                )
            )
        return tuple(extracted)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF could not be parsed completely") from error


class PDFPageSourceAdapter:
    """Resolve and read physical PDF page Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project a PDF page as standard text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(
    memory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct exact page citations for one question."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-blank string")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    variants = _query_variants(question)
    per_query = min(max(limit * 4, limit), 100)
    scores: dict[tuple[str, int], float] = {}
    citations: dict[tuple[str, int], MemoryCitation] = {}

    for variant_index, variant in enumerate(variants):
        try:
            page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=per_query))
        except Exception:
            continue
        for rank, hit in enumerate(page.hits, start=1):
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            try:
                entry = await memory.get(GetMemoryEntryRequest(citation=citation))
            except Exception:
                continue
            for reference in entry.entry.sources:
                if reference.source_type != PDF_PAGE_SOURCE_NAME:
                    continue
                location = _page_location(reference.source_id)
                if location is None:
                    continue
                key = location
                scores[key] = scores.get(key, 0.0) + 1.0 / (60.0 + rank + variant_index * 0.01)
                citations.setdefault(key, citation)

    ordered = sorted(scores, key=lambda key: (-scores[key], key[0], key[1]))
    return tuple(citations[key] for key in ordered[:limit])


def _page_location(source_id: str) -> tuple[str, int] | None:
    try:
        document, digest, page = source_id.rsplit(":", 2)
        if not document or len(digest) != 64:
            return None
        return (document, int(page))
    except (ValueError, AttributeError):
        return None


def _query_variants(question: str) -> tuple[str, ...]:
    tokens = _QUERY_TOKEN.findall(question)
    lowered = [token.lower() for token in tokens]
    content = [token for token, low in zip(tokens, lowered, strict=True) if low not in _QUERY_STOPWORDS]
    numbers = [token for token in tokens if token.isdigit()]
    variants: list[str] = []
    for variant in (" ".join(tokens), " ".join(content), " ".join(numbers)):
        if variant and variant not in variants:
            variants.append(variant)
    return tuple(variants) or (question,)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
