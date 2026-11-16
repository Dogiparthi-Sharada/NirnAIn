"""Project-draft endpoint: project details read from a transcript, document or pasted notes.

Purpose: Step 1b ("Create from a transcript or document") pre-fills the project form with each
field's verbatim quote and status; nothing is stored, and only organization members may call it.
How it fits: Reads files with app/api/v1/documents.py `uploaded_file` and app/drafts, then
`DraftReader` (Claude or the rules reader); a Claude read's tokens go to
app/repositories/ai_usage.py; layer: api.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.deps import AiUsageLog, OrgId, Orgs
from app.api.v1.documents import uploaded_file
from app.api.v1.projects import ensure_org_visible
from app.core.security import CurrentPrincipal
from app.domain.drafts import MAX_DRAFT_TEXT, ProjectDraft, ProjectDraftTextIn
from app.drafts.extract_text import (
    UnsupportedFileError,
    extract_text,
    normalize,
)
from app.drafts.reader import DraftReader, get_draft_reader
from app.repositories.ai_usage import AiUsageEvent, record_ai_usage

router = APIRouter(tags=["projects"])

DraftReaders = Annotated[DraftReader, Depends(get_draft_reader)]

PASTED_SOURCE = "Pasted notes"
BODY_DETAIL = "Send a file (multipart/form-data) or JSON {text}."

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
    """(text, file name) of the multipart upload; 413 / 415 / 422 for bad files.

    The file is read in place from Starlette's spooled upload (at most 100 MB,
    refused early while streaming), never copied into memory as a whole.
    """
    async with uploaded_file(request) as (upload, _):
        name = upload.filename or "Uploaded file"
        text = await asyncio.to_thread(extract_text, name, upload.content_type, upload.file)
    return text, name


async def _text_source(request: Request) -> tuple[str, str]:
    """(text, source name) of a JSON body, validated like any other request body (422)."""
    try:
        body = ProjectDraftTextIn.model_validate_json(await request.body())
    except ValidationError as invalid:
        errors = invalid.errors(include_url=False, include_input=False)
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in errors]
        ) from invalid
    return normalize(body.text), body.source_name or PASTED_SOURCE


@router.post(
    "/orgs/{orgId}/project-drafts/extract",
    response_model=ProjectDraft,
    summary="Read project details from a file or pasted notes (nothing is saved)",
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def extract_project_draft(
    org_id: OrgId,
    request: Request,
    orgs: Orgs,
    reader: DraftReaders,
    principal: CurrentPrincipal,
    usage_log: AiUsageLog,
) -> ProjectDraft:
    """Read a draft with Claude when configured, else with the labelled rules reader.

    A Claude read records its tokens for the organization (never failing the request).
    """
    await ensure_org_visible(orgs, org_id)
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        text, source_name = await _file_source(request)
    elif content_type.startswith("application/json"):
        text, source_name = await _text_source(request)
    else:
        raise UnsupportedFileError(BODY_DETAIL)
    # Same ceiling as intake notes, so the text can be analyzed as-is after "Create".
    draft, usage = await reader.read_with_usage(text[:MAX_DRAFT_TEXT], source_name)
    if usage is not None:
        event = AiUsageEvent(
            "project_draft", draft.model, usage, org_id=org_id, user_id=principal.user_id
        )
        await record_ai_usage(usage_log, event)
    return draft
