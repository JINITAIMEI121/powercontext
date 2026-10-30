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
import math
import re
from collections import Counter
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
_QUERY_SYNONYMS = {
    "geograph": ("geographic", "region", "regions", "country", "countries", "market", "markets", "operations"),
    "legal": ("litigation", "proceeding", "proceedings", "claim", "claims", "regulatory", "lawsuit", "investigation"),
    "battle": ("litigation", "proceeding", "proceedings", "claim", "claims"),
    "restructur": ("restructuring", "impairment", "productivity", "charge", "charges", "severance"),
    "ebitda": ("operating", "income", "depreciation", "amortization"),
    "capex": ("capital", "expenditure", "expenditures", "property", "plant", "equipment"),
    "revenue": ("revenue", "sales"),
    "margin": ("margin", "operating profit", "gross profit"),
    "employee": ("employee", "employees", "headcount"),
}
_SECTION_BOOSTS = (
    (
        (
            "income statement",
            "p&l",
            "profit and loss",
            "operating margin",
            "gross margin",
            "operating income",
            "operating profit",
            "net income",
            "net earnings",
            "profit margin",
            "effective tax",
            "income tax",
            "ebitda",
            "cost of sales",
            "cost of revenue",
            "revenue change",
            "sales",
        ),
        (
            "consolidated statement of income",
            "consolidated statements of income",
            "consolidated statement of operations",
            "consolidated statements of operations",
            "statements of operations",
            "statement of operations",
            "summary of financial performance",
        ),
        15.0,
    ),
    (
        (
            "balance sheet",
            "financial position",
            "working capital",
            "quick ratio",
            "current ratio",
            "current assets",
            "current liabilities",
            "total assets",
            "total liabilities",
            "liability",
            "equity",
            "receivable",
            "inventory",
            "debt",
            "borrowings",
        ),
        (
            "consolidated balance sheet",
            "consolidated balance sheets",
            "statement of financial position",
            "statements of financial position",
            "summary of financial performance",
        ),
        15.0,
    ),
    (
        (
            "cash flow",
            "operating cash",
            "free cash",
            "capex",
            "capital expenditure",
            "capital expenditures",
            "dividend",
            "depreciation",
            "amortization",
            "dpo",
            "days payable",
            "ccc",
            "cash conversion",
        ),
        (
            "consolidated statement of cash flows",
            "consolidated statements of cash flows",
            "statement of cash flows",
            "statements of cash flows",
        ),
        15.0,
    ),
    (
        ("legal", "litigation", "lawsuit", "proceeding", "regulatory", "contingenc"),
        ("legal proceedings", "litigation", "contingencies", "commitments and contingencies"),
        15.0,
    ),
    (
        ("effective tax", "tax rate", "income tax", "provision for income"),
        ("effective tax rate", "income taxes", "provision for income taxes", "summary of financial performance"),
        12.0,
    ),
    (
        ("capital-intensive", "capital intensive"),
        (
            "consolidated statement of income",
            "consolidated balance sheet",
            "consolidated statement of cash flows",
            "property, plant and equipment",
            "capital expenditures",
        ),
        15.0,
    ),
    (
        ("revenue change", "operating margin change", "results of operations", "management's discussion", "md&a"),
        ("management's discussion and analysis", "results of operations"),
        15.0,
    ),
    (
        ("segment", "geograph", "customer", "product categor", "service categor", "primary customer"),
        (
            "summary of business segment data",
            "business segment",
            "reporting segment",
            "reportable segment",
            "segment data",
            "results of operations",
            "customers",
            "customer",
            "geographic",
            "our operations",
        ),
        12.0,
    ),
    (
        ("product categor", "service categor", "segment", "segment data"),
        (
            "summary of business segment data",
            "segment data",
            "by business segment",
            "sales and operating income",
            "performance by business segment",
        ),
        50.0,
    ),
    (
        ("customer",),
        ("customers", "customer"),
        20.0,
    ),
    (
        ("customer concentration",),
        (
            "customer concentration",
            "customers",
            "customer",
            "one customer",
            "customer accounted for",
            "of our consolidated net revenue",
        ),
        50.0,
    ),
    (
        ("industry", "products and services", "major products", "major services"),
        ("industry", "products", "services", "our business"),
        10.0,
    ),
    (
        ("cyclical", "risk factor", "subject to cyclical", "forward-looking"),
        ("cyclical", "forward-looking statements", "risk factors"),
        8.0,
    ),
    (
        ("restructur", "acquisition", "business combination", "impairment"),
        ("restructuring", "business combinations", "acquisitions", "impairment"),
        8.0,
    ),
    (
        ("inventory", "receivable", "accounts payable", "turnover"),
        ("inventories", "accounts receivable", "accounts payable", "inventory turnover"),
        8.0,
    ),
    (
        ("dividend", "share repurchase", "stock repurchase"),
        ("dividends", "stock repurchases", "shareholders' equity"),
        8.0,
    ),
    (
        ("card member", "retain", "business environment"),
        ("card members", "business environment"),
        8.0,
    ),
    (
        ("geograph", "operate", "operations"),
        ("our operations", "north america", "europe", "asia pacific", "africa"),
        6.0,
    ),
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

    documents = await _page_documents(memory)
    if documents:
        ranked = _rank_page_documents(documents, question)
        return tuple(documents[index][0] for index in ranked[:limit])

    citations: dict[tuple[str, int], MemoryCitation] = {}
    for variant in _query_variants(question):
        try:
            page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=min(limit * 4, 100)))
        except Exception:
            continue
        for hit in page.hits:
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
                if reference.source_type == PDF_PAGE_SOURCE_NAME:
                    location = _page_location(reference.source_id)
                    if location is not None:
                        citations.setdefault(location, citation)
                    break
    return tuple(citations.values())[:limit]


async def _page_documents(memory) -> tuple[tuple[MemoryCitation, tuple[str, int], str], ...]:
    try:
        entries = (await memory.list()).entries
    except Exception:
        return ()
    documents: list[tuple[MemoryCitation, tuple[str, int], str]] = []
    seen: set[tuple[str, int]] = set()
    for row in entries:
        for reference in row.entry.sources:
            if reference.source_type != PDF_PAGE_SOURCE_NAME:
                continue
            location = _page_location(reference.source_id)
            if location is None or location in seen:
                break
            seen.add(location)
            text = row.entry.text
            page_text = text.split("\n", 1)[1] if "\n" in text else text
            documents.append((row.citation, location, page_text))
            break
    return tuple(documents)


def _rank_page_documents(
    documents: tuple[tuple[MemoryCitation, tuple[str, int], str], ...],
    question: str,
) -> list[int]:
    query_terms = _weighted_query_terms(question)
    if not query_terms:
        return list(range(len(documents)))
    token_counts = [Counter(_query_tokens(document[2])) for document in documents]
    document_count = len(documents)
    document_frequency = {
        term: sum(1 for counts in token_counts if term in counts) for term, _weight in query_terms
    }
    average_length = sum(sum(counts.values()) for counts in token_counts) / document_count
    question_lower = question.lower()
    section_bonus = [0.0] * document_count
    for cues, phrases, bonus in _SECTION_BOOSTS:
        if not any(cue in question_lower for cue in cues):
            continue
        for index, document in enumerate(documents):
            text = re.sub(r"\s+", " ", document[2].lower())
            if any(phrase in text for phrase in phrases):
                section_bonus[index] += bonus
    if "customer" in question_lower:
        # Customer concentration is usually described in the early Business / Risk Factors pages.
        for index, document in enumerate(documents):
            if document[1][1] <= 20:
                text = re.sub(r"\s+", " ", document[2].lower())
                if "customer" in text:
                    section_bonus[index] += 60.0
    numbers = {term for term, _weight in query_terms if term.isdigit()}
    scores: list[tuple[float, int]] = []
    for index, counts in enumerate(token_counts):
        length = sum(counts.values())
        score = section_bonus[index]
        for term, weight in query_terms:
            frequency = counts.get(term, 0)
            if frequency == 0:
                continue
            inverse = math.log(1.0 + (document_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += weight * inverse * frequency * 2.4 / (
                frequency + 1.4 * (1.0 - 0.75 + 0.75 * length / max(1.0, average_length))
            )
        for number in numbers:
            if number in counts:
                score += 3.0
        scores.append((score, index))
    scores.sort(reverse=True)

    positions = {document[1]: index for index, document in enumerate(documents)}
    adjacency = [0.0] * document_count
    for _score, index in scores[:5]:
        document_id, page_number = documents[index][1]
        for distance, bonus in ((1, 8.0), (2, 4.0)):
            for neighbor in (page_number - distance, page_number + distance):
                neighbor_index = positions.get((document_id, neighbor))
                if neighbor_index is not None:
                    adjacency[neighbor_index] = max(adjacency[neighbor_index], bonus)
    scores = [(score + adjacency[index], index) for score, index in scores]
    scores.sort(key=lambda item: (-item[0], documents[item[1]][1]))
    return [index for _score, index in scores]


def _weighted_query_terms(question: str) -> tuple[tuple[str, float], ...]:
    terms: list[tuple[str, float]] = []
    for token in _query_tokens(question):
        if token in _QUERY_STOPWORDS:
            continue
        terms.append((token, 1.0))
        for prefix, synonyms in _QUERY_SYNONYMS.items():
            if token.startswith(prefix):
                terms.extend((synonym, 0.55) for synonym in synonyms if " " not in synonym)
    best: dict[str, float] = {}
    for term, weight in terms:
        best[term] = max(best.get(term, 0.0), weight)
    return tuple(best.items())


def _query_tokens(text: str) -> list[str]:
    return [token.lower() for token in _QUERY_TOKEN.findall(text)]


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
