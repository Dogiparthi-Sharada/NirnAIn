"""Uploaded intake documents, queried under the caller's RLS session.

Pasted notes are also `source_documents` rows (kind 'text') but they belong to
intake runs (see `app.repositories.intake`); this repository only lists and
manages uploaded files (kind <> 'text'). RLS lets project readers see a
project's documents and only project writers insert or delete them.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.documents import Extraction
from app.domain.common import DocumentKind
from app.domain.intake import SourceDocument
from app.repositories import access
from app.repositories.tables import source_documents


def _document_select() -> sa.Select[Any]:
    return sa.select(
        source_documents.c.id,
        source_documents.c.project_id,
        source_documents.c.kind,
        source_documents.c.title,
        source_documents.c.mime_type,
        source_documents.c.size_bytes,
        source_documents.c.extraction_status,
        source_documents.c.extraction_error,
        sa.func.coalesce(sa.func.char_length(source_documents.c.content_text), 0).label("chars"),
        source_documents.c.created_at,
    )


def _is_upload() -> sa.ColumnElement[bool]:
    return source_documents.c.kind != DocumentKind.TEXT


def _to_document(row: sa.Row[Any]) -> SourceDocument:
    return SourceDocument.model_validate(dict(row._mapping))


class DocumentRepository:
    """Uploaded documents of the projects the caller can see."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        """Whether the caller can see the project (RLS hides the others)."""
        return await access.visible_project(self._conn, project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        """Whether the caller may add or delete the project's documents."""
        return await access.can_write_project(self._conn, project_id)

    async def create(
        self,
        project_id: uuid.UUID,
        kind: DocumentKind,
        title: str,
        mime_type: str | None,
        size_bytes: int,
        extraction: Extraction,
    ) -> SourceDocument | None:
        """Store an upload as its extracted text (the file itself is not kept)."""
        document_id = uuid.uuid4()
        await self._conn.execute(
            sa.insert(source_documents).values(
                id=document_id,
                project_id=project_id,
                kind=kind,
                title=title,
                mime_type=mime_type,
                size_bytes=size_bytes,
                content_text=extraction.text,
                extraction_status=extraction.status,
                extraction_error=extraction.error,
            )
        )
        return await self.get(document_id)

    async def get(self, document_id: uuid.UUID) -> SourceDocument | None:
        """One uploaded document, or None when missing or not visible."""
        statement = _document_select().where(source_documents.c.id == document_id, _is_upload())
        row = (await self._conn.execute(statement)).first()
        return _to_document(row) if row else None

    async def list_for_project(self, project_id: uuid.UUID) -> list[SourceDocument]:
        """The project's uploaded documents, newest first."""
        statement = (
            _document_select()
            .where(source_documents.c.project_id == project_id, _is_upload())
            .order_by(source_documents.c.created_at.desc(), source_documents.c.id.desc())
        )
        return [_to_document(row) for row in (await self._conn.execute(statement)).all()]

    async def delete(self, document_id: uuid.UUID) -> bool:
        """Delete an upload; False when RLS (or a concurrent delete) left nothing to delete.

        Scope items quoting it keep their quote; their `source_document_id` is set
        to null by the foreign key.
        """
        statement = (
            sa.delete(source_documents)
            .where(source_documents.c.id == document_id, _is_upload())
            .returning(source_documents.c.id)
        )
        return (await self._conn.execute(statement)).first() is not None
