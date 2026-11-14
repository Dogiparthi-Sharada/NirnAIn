"""Document uploads for AI intake: upload, list and delete.

Every endpoint runs under the caller's RLS session (`Documents` is built from
`UserDB`), so the database itself enforces who may see, add or delete a
project's documents; the explicit checks only turn RLS outcomes into clear
404 / 403 responses.

`uploaded_file` is the shared multipart reader for uploads (also used by
`POST /orgs/{orgId}/project-drafts/extract`): it enforces the 100 MB limit
while the body streams in, so an oversized file is refused early instead of
being spooled in full.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Request, Response, status
from starlette.datastructures import UploadFile
from starlette.types import Message, Receive

from app.ai.documents import (
    MAX_UPLOAD_BYTES,
    MIME_TYPE_LIMIT,
    TOO_LARGE_DETAIL,
    UNSUPPORTED_DETAIL,
    VIDEO_DETAIL,
    detect_kind,
    extract_text,
    file_title,
    is_video,
    stream_size,
)
from app.api.deps import DocumentId, Documents, ProjectId
from app.core.errors import (
    ContentTooLargeError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    UnsupportedMediaTypeError,
)
from app.domain.intake import SourceDocument

router = APIRouter(tags=["intake"])
logger = logging.getLogger("app.api.documents")

NO_FILE_DETAIL = "Attach the file in a form field named `file`."
# Multipart framing (boundary lines, part headers) on top of the file itself.
FORM_OVERHEAD_BYTES = 64 * 1024

UPLOAD_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        }
    },
}


def _capped_receive(receive: Receive, limit_bytes: int) -> Receive:
    """`receive` that raises 413 as soon as more than `limit_bytes` of body have arrived."""
    received_bytes = 0

    async def receive_within_limit() -> Message:
        nonlocal received_bytes
        message = await receive()
        if message["type"] == "http.request":
            received_bytes += len(message.get("body", b""))
            if received_bytes > limit_bytes:
                raise ContentTooLargeError(TOO_LARGE_DETAIL)
        return message

    return receive_within_limit


@asynccontextmanager
async def uploaded_file(request: Request) -> AsyncIterator[tuple[UploadFile, int]]:
    """The multipart `file` part and its size in bytes; 413 over `MAX_UPLOAD_BYTES`.

    A declared Content-Length over the limit is refused before anything is
    read; otherwise the body is counted while it streams in. Starlette spools
    the file in chunks (memory, then disk), and the file stays open until the
    `async with` block ends, so callers read it from `upload.file` in place.
    """
    body_limit = MAX_UPLOAD_BYTES + FORM_OVERHEAD_BYTES
    declared_length = request.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > body_limit:
        raise ContentTooLargeError(TOO_LARGE_DETAIL)
    capped_request = Request(request.scope, _capped_receive(request.receive, body_limit))
    async with capped_request.form(max_files=1, max_fields=1) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise UnprocessableError(NO_FILE_DETAIL)
        size_bytes = await asyncio.to_thread(stream_size, upload.file)
        if size_bytes > MAX_UPLOAD_BYTES:
            raise ContentTooLargeError(TOO_LARGE_DETAIL)
        yield upload, size_bytes


@router.post(
    "/projects/{projectId}/documents",
    response_model=SourceDocument,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document for AI intake (only its extracted text is kept)",
    openapi_extra={"requestBody": UPLOAD_REQUEST_BODY},
)
async def upload_document(
    project_id: ProjectId, request: Request, documents: Documents
) -> SourceDocument:
    """Read a PDF, Word or transcript file's text and keep it for the next intake run.

    A file whose text can't be read (audio, scanned PDF, corrupt file) is still
    stored, with `extractionStatus` 'failed' and a user-facing `extractionError`.
    Video files are refused (415).
    """
    if not await documents.project_exists(project_id):
        raise NotFoundError("Project not found")
    if not await documents.can_write(project_id):
        raise ForbiddenError("You do not have permission to add documents to this project")
    async with uploaded_file(request) as (upload, size_bytes):
        # Video is refused outright: there is no transcription, and the meeting's
        # transcript (.vtt/.srt/.txt) is what intake can actually read.
        if is_video(upload.filename, upload.content_type):
            raise UnsupportedMediaTypeError(VIDEO_DETAIL)
        kind = detect_kind(upload.filename, upload.content_type)
        if kind is None:
            raise UnsupportedMediaTypeError(UNSUPPORTED_DETAIL)
        # Parsing a large PDF takes a while; keep it off the event loop.
        extraction = await asyncio.to_thread(extract_text, kind, upload.file)
        title, content_type = file_title(upload.filename), upload.content_type
    mime_type = (content_type or "").strip()[:MIME_TYPE_LIMIT] or None
    document = await documents.create(project_id, kind, title, mime_type, size_bytes, extraction)
    if document is None:
        raise ForbiddenError("The document was saved but is not visible to you")
    if extraction.failure is not None:
        # Security rule (SEC-006): no file name or content in the log, only what
        # identifies the upload and the parser's exception class.
        logger.warning(
            "document text extraction failed",
            extra={
                "document_id": str(document.id),
                "size_bytes": size_bytes,
                "kind": str(kind),
                "error": extraction.failure,
            },
        )
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
