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

"""Row-level CSV Sources with stable identities and exact text evidence."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")


def _validate_primary_key_value(primary_key_value: str) -> None:
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-blank string")


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to split into row Sources."""

    document_id: str
    primary_key: str
    content: bytes

    @model_validator(mode="after")
    def validate_input(self) -> CSVDocumentInput:
        _validate_document_id(self.document_id)
        if not self.primary_key:
            raise ValueError("primary_key must be a non-empty string")
        if not isinstance(self.content, bytes):
            raise ValueError("content must be bytes")
        return self


class CSVRowInput(BaseModel):
    """One row resolved from a CSV document, preserving header order and values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowInput:
        _validate_document_id(self.document_id)
        if not self.primary_key:
            raise ValueError("primary_key must be a non-empty string")
        _validate_primary_key_value(self.primary_key_value)
        if not self.columns:
            raise ValueError("columns must not be empty")
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same length")
        seen: set[str] = set()
        for column in self.columns:
            if not column or not column.strip():
                raise ValueError("column names must be non-blank strings")
            if column in seen:
                raise ValueError("column names must be unique")
            seen.add(column)
        if self.primary_key not in seen:
            raise ValueError("primary_key must match one column exactly")
        key_index = self.columns.index(self.primary_key)
        if self.values[key_index] != self.primary_key_value:
            raise ValueError("primary_key_value must match the column value")
        return self


class CSVRowSource(Source):
    """A captured CSV row whose identity derives from document and primary key."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the stable, delimiter-safe identity for one CSV row."""

    _validate_document_id(document_id)
    _validate_primary_key_value(primary_key_value)
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and fully validate a CSV document before returning any row."""

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error

    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed CSV content: {error}") from error

    if not records:
        raise ValueError("CSV document must contain a header record")

    header = records[0]
    if not header:
        raise ValueError("CSV header must not be empty")
    seen: set[str] = set()
    for column in header:
        if not column or not column.strip():
            raise ValueError("CSV header fields must be non-blank")
        if column in seen:
            raise ValueError("CSV header fields must be unique")
        seen.add(column)
    if value.primary_key not in seen:
        raise ValueError("primary_key must match one header field exactly")

    key_index = header.index(value.primary_key)
    columns = tuple(header)
    rows: list[CSVRowInput] = []
    primary_keys: set[str] = set()

    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError("CSV record width must match the header")
        primary_key_value = record[key_index]
        _validate_primary_key_value(primary_key_value)
        if primary_key_value in primary_keys:
            raise ValueError("primary key values must be unique within a document")
        primary_keys.add(primary_key_value)
        csv_row_source_id(value.document_id, primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )

    return tuple(rows)


class CSVRowSourceAdapter:
    """Resolve and read row-level CSV Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project a CSV row as the standard text-evidence payload."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
