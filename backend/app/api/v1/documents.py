"""Document uploads for AI intake: upload, list and delete.

Every endpoint runs under the caller's RLS session (`Documents` is built from
`UserDB`), so the database itself enforces who may see, add or delete a
project's documents; the explicit checks only turn RLS outcomes into clear
404 / 403 responses.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status

from app.ai.documents import (
    MAX_UPLOAD_BYTES,
    MIME_TYPE_LIMIT,
    TOO_LARGE_DETAIL,
    UNSUPPORTED_DETAIL,
    detect_kind,
    extract_text,
    file_title,
)
from app.api.deps import DocumentId, Documents, ProjectId
from app.core.errors import (
    ContentTooLargeError,
    ForbiddenError,
    NotFoundError,
    UnsupportedMediaTypeError,
)
from app.domain.intake import SourceDocument

router = APIRouter(tags=["intake"])


async def _read_capped(file: UploadFile) -> bytes:
    """The upload's bytes; 413 when it is larger than the limit."""
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise ContentTooLargeError(TOO_LARGE_DETAIL)
    # Read one byte past the limit so a missing/wrong size header can't sneak a bigger file in.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ContentTooLargeError(TOO_LARGE_DETAIL)
    return data


@router.post(
    "/projects/{projectId}/documents",
    response_model=SourceDocument,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document for AI intake (only its extracted text is kept)",
)
async def upload_document(
    project_id: ProjectId, file: Annotated[UploadFile, File()], documents: Documents
) -> SourceDocument:
    """Read a PDF, Word or transcript file's text and keep it for the next intake run.

    A file whose text can't be read (audio, scanned PDF, corrupt file) is still
    stored, with `extractionStatus` 'failed' and a user-facing `extractionError`.
    """
    if not await documents.project_exists(project_id):
        raise NotFoundError("Project not found")
    if not await documents.can_write(project_id):
        raise ForbiddenError("You do not have permission to add documents to this project")
    kind = detect_kind(file.filename, file.content_type)
    if kind is None:
        raise UnsupportedMediaTypeError(UNSUPPORTED_DETAIL)
    data = await _read_capped(file)

    # Parsing a large PDF takes a while; keep it off the event loop.
    extraction = await asyncio.to_thread(extract_text, kind, data)
    mime_type = (file.content_type or "").strip()[:MIME_TYPE_LIMIT] or None
    document = await documents.create(
        project_id, kind, file_title(file.filename), mime_type, len(data), extraction
    )
    if document is None:
        raise ForbiddenError("The document was saved but is not visible to you")
    return document


@router.get(
    "/projects/{projectId}/documents",
    response_model=list[SourceDocument],
    summary="Uploaded documents of a project, newest first",
)
async def list_documents(project_id: ProjectId, documents: Documents) -> list[SourceDocument]:
    """The project's uploaded documents (pasted notes are not listed)."""
    if not await documents.project_exists(project_id):
        raise NotFoundError("Project not found")
    return await documents.list_for_project(project_id)


@router.delete(
    "/documents/{documentId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete an uploaded document",
)
async def delete_document(document_id: DocumentId, documents: Documents) -> Response:
    """Delete an upload: 404 when not visible, 403 when the caller can't write the project."""
    document = await documents.get(document_id)
    if document is None:
        raise NotFoundError("Document not found")
    if not await documents.can_write(document.project_id):
        raise ForbiddenError("You do not have permission to delete this document")
    if not await documents.delete(document_id):
        raise NotFoundError("Document not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
