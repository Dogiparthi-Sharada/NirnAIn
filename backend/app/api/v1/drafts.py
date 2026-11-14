from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.api.deps import OrgId, Orgs
from app.api.v1.projects import ensure_org_visible
from app.core.errors import UnprocessableError
from app.domain.drafts import MAX_DRAFT_TEXT, ProjectDraft, ProjectDraftTextIn
from app.drafts.extract_text import (
    MAX_FILE_BYTES,
    TOO_LARGE_DETAIL,
    FileTooLargeError,
    UnsupportedFileError,
    extract_text,
    normalize,
    supported_suffix,
)
from app.drafts.reader import DraftReader, get_draft_reader

router = APIRouter(tags=["projects"])

DraftReaders = Annotated[DraftReader, Depends(get_draft_reader)]

PASTED_SOURCE = "Pasted notes"
NO_FILE_DETAIL = "Attach the file in a form field named `file`."
BODY_DETAIL = "Send a file (multipart/form-data) or JSON {text}."
# Multipart framing on top of the file itself.
FORM_OVERHEAD_BYTES = 64 * 1024

_REQUEST_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        },
        "application/json": {"schema": ProjectDraftTextIn.model_json_schema(by_alias=True)},
    },
}


async def _file_source(request: Request) -> tuple[str, str]:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_FILE_BYTES + FORM_OVERHEAD_BYTES:
        raise FileTooLargeError(TOO_LARGE_DETAIL)
    async with request.form(max_files=1, max_fields=1) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise UnprocessableError(NO_FILE_DETAIL)
        name = upload.filename or "Uploaded file"
        supported_suffix(name, upload.content_type)
        data = await upload.read(MAX_FILE_BYTES + 1)
        content_type = upload.content_type
    text = await asyncio.to_thread(extract_text, name, content_type, data)
    return text, name


async def _text_source(request: Request) -> tuple[str, str]:
    try:
        body = ProjectDraftTextIn.model_validate_json(await request.body())
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in errors]
        ) from exc
    return normalize(body.text), body.source_name or PASTED_SOURCE


@router.post(
    "/orgs/{orgId}/project-drafts/extract",
    response_model=ProjectDraft,
    summary="Read project details from a file or pasted notes (nothing is saved)",
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def extract_project_draft(
    org_id: OrgId, request: Request, orgs: Orgs, reader: DraftReaders
) -> ProjectDraft:
    await ensure_org_visible(orgs, org_id)
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        text, source_name = await _file_source(request)
    elif content_type.startswith("application/json"):
        text, source_name = await _text_source(request)
    else:
        raise UnsupportedFileError(BODY_DETAIL)
    # Same ceiling as intake notes, so the text can be analyzed as-is after "Create".
    return await reader.read(text[:MAX_DRAFT_TEXT], source_name)
