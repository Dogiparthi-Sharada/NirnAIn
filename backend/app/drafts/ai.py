"""Project drafts read by Claude: prompt, output schema and the checks on its answer.

The model must quote the notes word for word. Every quote is looked up in the
source text: a "stated" value whose quote isn't there is downgraded to
"inferred" (the PM has to confirm it), and values that don't parse are dropped,
so a model mistake can never put an unchecked value into a project.

Called by `app.drafts.reader.DraftReader` (on a worker thread) only when
ANTHROPIC_API_KEY is set; the call goes through `app.ai.llm.LLM`.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.llm import LLM
from app.ai.prompting import DATA_RULE, as_data, data_block, one_line
from app.domain.drafts import (
    DateField,
    DraftStatus,
    MoneyField,
    ProjectDraftFields,
    TextField,
)
from app.domain.projects import MAX_SAFE_CENTS

PROMPT_TEXT_LIMIT = 60_000
QUOTE_LIMIT = 500
NAME_LIMIT = 200
DESCRIPTION_LIMIT = 1000
DEPARTMENT_LIMIT = 120
SPONSOR_LIMIT = 200
MAX_TOKENS = 2000

SYSTEM_PROMPT = (
    "You help a project manager create a project in a planning tool from meeting notes, "
    "a transcript or a project document. "
    + DATA_RULE
    + " Fill in each project detail only when the text supports it, and never guess: "
    "if the text doesn't say it, use status 'missing' with value and quote null. "
    "Use status 'stated' when the text says the value outright, and 'inferred' when you "
    "worked it out from what the text says (e.g. 'by Q3 2025' means a target end date of "
    "2025-09-30). For every stated or inferred value, quote the sentence it came from word "
    "for word, exactly as it appears in the text. "
    "name: the project's name, short, without words like 'kickoff' or 'meeting notes'. "
    "description: one or two sentences on what the project will achieve and for whom. "
    "startDate / targetEndDate: ISO dates (YYYY-MM-DD) for when the work starts and must be "
    "finished (go-live, deadline). A meeting date is not a start date. "
    "budget: the total approved budget as a plain number in currency units (e.g. 375000 for "
    "'$375K'), not a single cost item. department: the owning department or business unit. "
    "sponsor: the executive sponsor, with their title when given."
)


class _AiText(BaseModel):
    value: str | None = Field(description="The value, or null when missing")
    quote: str | None = Field(description="Verbatim sentence from the text, or null")
    status: Literal["stated", "inferred", "missing"]


class _AiAmount(BaseModel):
    value: float | None = Field(description="Total budget in currency units, or null")
    quote: str | None = Field(description="Verbatim sentence from the text, or null")
    status: Literal["stated", "inferred", "missing"]


class AiDraft(BaseModel):
    """The structured answer Claude must return (validated by the SDK)."""

    name: _AiText
    description: _AiText
    start_date: _AiText = Field(description="value: YYYY-MM-DD")
    target_end_date: _AiText = Field(description="value: YYYY-MM-DD")
    budget: _AiAmount
    department: _AiText
    sponsor: _AiText


def build_prompt(text: str) -> str:
    """The source as escaped data (prompt-injection guard), cut to the prompt budget."""
    return (
        data_block(as_data(text[:PROMPT_TEXT_LIMIT]))
        + "\n\nFill in the project details from the text above."
    )


def find_quote(quote: str | None, text: str) -> str | None:
    """The span of `text` matching `quote` (ignoring case and spacing), or None."""
    if not quote:
        return None
    words = quote.replace("&lt;", "<").replace("&gt;", ">").split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(word) for word in words)
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0)[:QUOTE_LIMIT] if match else None


def _checked(status: DraftStatus, quote: str | None, text: str) -> tuple[DraftStatus, str | None]:
    """A "stated" value needs a quote found in the source; otherwise the PM must confirm it."""
    found = find_quote(quote, text)
    if status == "stated" and found is None:
        return "inferred", None
    return status, found


def _text(raw: _AiText, text: str, limit: int) -> TextField:
    value = one_line(raw.value or "", limit)
    if raw.status == "missing" or not value:
        return TextField()
    status, quote = _checked(raw.status, raw.quote, text)
    return TextField(value=value, quote=quote, status=status)


def _date(raw: _AiText, text: str) -> DateField:
    if raw.status == "missing" or not raw.value:
        return DateField()
    try:
        value = date.fromisoformat(raw.value.strip())
    except ValueError:
        return DateField()
    status, quote = _checked(raw.status, raw.quote, text)
    return DateField(value=value, quote=quote, status=status)


def _money(raw: _AiAmount, text: str) -> MoneyField:
    if raw.status == "missing" or raw.value is None:
        return MoneyField()
    try:
        cents = int((Decimal(str(raw.value)) * 100).to_integral_value())
    except (InvalidOperation, ValueError, OverflowError):  # NaN / infinity
        return MoneyField()
    if not 0 < cents <= MAX_SAFE_CENTS:
        return MoneyField()
    status, quote = _checked(raw.status, raw.quote, text)
    return MoneyField(value=cents, quote=quote, status=status)


def to_fields(answer: AiDraft, text: str) -> ProjectDraftFields:
    """Claude's answer as draft fields, after the quote, date and money checks."""
    return ProjectDraftFields(
        name=_text(answer.name, text, NAME_LIMIT),
        description=_text(answer.description, text, DESCRIPTION_LIMIT),
        start_date=_date(answer.start_date, text),
        target_end_date=_date(answer.target_end_date, text),
        budget_cents=_money(answer.budget, text),
        department=_text(answer.department, text, DEPARTMENT_LIMIT),
        sponsor=_text(answer.sponsor, text, SPONSOR_LIMIT),
    )


def ai_draft(llm: LLM, text: str, timeout_s: float) -> ProjectDraftFields:
    """One structured Claude call (blocking: run it with `asyncio.to_thread`)."""
    answer, _usage = llm.parse(
        system=SYSTEM_PROMPT,
        prompt=build_prompt(text),
        output=AiDraft,
        max_tokens=MAX_TOKENS,
        timeout_s=timeout_s,
    )
    return to_fields(answer, text)
