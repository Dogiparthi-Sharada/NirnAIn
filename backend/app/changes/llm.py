"""Claude reading of new meeting notes (when ANTHROPIC_API_KEY is set).

The model only proposes changes; every one is checked against the notes and the project
before it is kept: the quote must appear in the notes verbatim, the work package or
person must exist (a newcomer must not), and the value must parse. Questions are always
derived by rules (`app.changes.derive`), so answering them maps to overrides
deterministically.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.ai.llm import LLM
from app.ai.prompting import DATA_RULE, as_data, data_block, one_line
from app.changes import detect, parsing
from app.changes.context import ChangeContext
from app.changes.team import DEFAULT_NEW_MEMBER_HOURS, addition
from app.domain.changes import DetectedChange, NewMember

logger = logging.getLogger("app.changes")

RULES_MODEL = "rules"
TIMEOUT_S = 60.0
MAX_CHANGES = 20

SYSTEM_PROMPT = (
    "You compare new meeting notes with a project's applied plan and list what changed: "
    "the total budget, the deadline, the duration of a listed work package (including work "
    "that is now done), the weekly hours of a listed team member (0 when they leave), a "
    "person brought onto the project (kind team: their role, their name only if the notes "
    "give it, their hours only if stated, and whether they come from outside as a "
    "contractor), or new or removed scope. Report only changes the notes "
    "state. Never invent numbers, dates, people or work packages; skip anything that is "
    "merely on track or unchanged. "
    "For every change copy the sentence it comes from verbatim into `quote`. " + DATA_RULE
)


class ProposedChange(BaseModel):
    """One change as Claude proposes it; checked by `to_changes` before it is kept."""

    kind: Literal["budget", "deadline", "duration", "capacity", "scope", "team"]
    target: str | None = Field(
        default=None,
        description="duration: the work package code exactly as listed; capacity: the "
        "person's full name exactly as listed; team: the newcomer's full name if the notes "
        "give it; otherwise null",
    )
    value: str | None = Field(
        default=None,
        description="budget: new total in US dollars (digits only); deadline: YYYY-MM-DD; "
        "duration: new total working days (0 = done); capacity: hours per week (0 = leaves); "
        "team: hours per week if stated, else null; scope: the requirement in one sentence",
    )
    role: str | None = Field(
        default=None, description="team: the newcomer's role, e.g. Security Specialist"
    )
    contractor: bool = Field(
        default=False, description="team: true when they come from outside the organization"
    )
    quote: str
    confidence: float = Field(ge=0, le=1)


class ProposedChanges(BaseModel):
    """Structured output of the Claude call."""

    changes: list[ProposedChange]


def build_prompt(notes: str, context: ChangeContext) -> str:
    """The user message: project context and the notes, inside the data block."""
    lines = [
        f"Today: {context.today.isoformat()}",
        f"Applied plan: {context.plan_label}",
        f"Total budget: {parsing.format_money(context.budget_cents)}",
        f"Deadline: {context.deadline.isoformat() if context.deadline else 'none'}",
        "Work packages (code | name | working days):",
        *(
            f"- {task.code} | {as_data(one_line(task.name, 200))} | {task.duration_days or 0}"
            for task in context.tasks
        ),
        "Team (full name | hours per week):",
        *(
            f"- {as_data(one_line(person.name, 120))} | {person.hours_per_week:g}"
            for person in context.people
        ),
        "",
        "New meeting notes:",
        as_data(notes),
    ]
    return data_block("\n".join(lines)) + "\n\nList what changed."


def _normalized(value: str) -> str:
    return " ".join(value.split()).lower()


def to_changes(out: ProposedChanges, notes: str, context: ChangeContext) -> list[DetectedChange]:
    """Keep only what the notes support and the project contains."""
    haystack = _normalized(notes)
    kept: list[DetectedChange] = []
    for proposal in out.changes[:MAX_CHANGES]:
        quote = " ".join(proposal.quote.split()).strip()
        if not quote or _normalized(quote) not in haystack:
            continue
        change = _proposal_to_change(proposal, quote, context, f"c{len(kept) + 1}")
        if change is not None:
            kept.append(change)
    return kept


def _proposal_to_change(  # noqa: PLR0911
    proposal: ProposedChange, quote: str, context: ChangeContext, change_id: str
) -> DetectedChange | None:
    """Turn one proposal into a change, or None when its value or target doesn't check out."""
    value = (proposal.value or "").strip()
    base = {
        "id": change_id,
        "kind": proposal.kind,
        "quote": quote,
        "confidence": proposal.confidence,
    }
    if proposal.kind == "budget":
        digits = re.sub(r"[^\d.]", "", value)
        if not digits:
            return None
        cents = round(float(digits) * 100)
        if cents <= 0 or cents == context.budget_cents:
            return None
        return DetectedChange.model_validate({**base, "from_": context.budget_cents, "to": cents})
    if proposal.kind == "deadline":
        try:
            new = date.fromisoformat(value)
        except ValueError:
            return None
        if new == context.deadline:
            return None
        old = context.deadline.isoformat() if context.deadline else None
        return DetectedChange.model_validate({**base, "from_": old, "to": new.isoformat()})
    if proposal.kind == "scope":
        action, note = detect.scope_action(f"{value} {quote}")
        return DetectedChange.model_validate(
            {**base, "to": value or quote, "action": action, "note": note}
        )
    if proposal.kind == "team":
        return _newcomer(proposal, value, base, context)
    try:
        number = float(value)
    except ValueError:
        return None
    target = (proposal.target or "").strip()
    if proposal.kind == "duration":
        task = next(
            (
                task
                for task in context.tasks
                if task.code == target or task.name.lower() == target.lower()
            ),
            None,
        )
        if task is None or number < 0 or int(number) == task.duration_days:
            return None
        return DetectedChange.model_validate(
            {
                **base,
                "target": task.id,
                "target_label": f"{task.code} {task.name}",
                "from_": task.duration_days,
                "to": int(number),
                "note": "Done. Nothing left to plan." if number == 0 else None,
            }
        )
    person = next(
        (person for person in context.people if person.name.lower() == target.lower()), None
    )
    if person is None or not 0 <= number <= 80 or number == person.hours_per_week:
        return None
    return DetectedChange.model_validate(
        {
            **base,
            "target": person.id,
            "target_label": person.name,
            "from_": person.hours_per_week,
            "to": number,
        }
    )


def _newcomer(
    proposal: ProposedChange, value: str, base: dict[str, object], context: ChangeContext
) -> DetectedChange | None:
    """A person brought in: needs a role; the name defaults to the role, hours count only
    when stated (else 40 h a week and a question asks), and a contractor is costed at the
    project's contractor rate (else a question asks)."""
    role = " ".join((proposal.role or "").split())[:120]
    if not role:
        return None
    name = " ".join((proposal.target or "").split())[:200] or role
    if any(person.name.casefold() == name.casefold() for person in context.people):
        return None
    try:
        stated = round(float(value)) if value else None
    except ValueError:
        stated = None
    hours = stated if stated is not None and 1 <= stated <= 80 else None
    try:
        member = NewMember(
            full_name=name,
            role_title=role,
            hours_per_week=hours or DEFAULT_NEW_MEMBER_HOURS,
            worker_type="contractor" if proposal.contractor else "employee",
            hourly_rate_cents=context.contractor_rate_cents if proposal.contractor else None,
        )
    except ValidationError:
        return None
    return DetectedChange.model_validate({**base, **addition(member, hours)})


class ChangeDetector:
    """Claude when configured, else (or when the call fails) the deterministic rules."""

    def __init__(self, llm: LLM | None) -> None:
        self._llm = llm

    async def detect(self, notes: str, context: ChangeContext) -> tuple[list[DetectedChange], str]:
        """Changes in the notes and who read them (`rules` or the Claude model id).

        Never raises for AI problems: a failed or slow Claude call falls back to the rules.
        """
        llm = self._llm
        if llm is not None and llm.configured:
            try:
                parsed, _usage = await asyncio.wait_for(
                    asyncio.to_thread(
                        llm.parse,
                        system=SYSTEM_PROMPT,
                        prompt=build_prompt(notes, context),
                        output=ProposedChanges,
                        max_tokens=4000,
                        timeout_s=TIMEOUT_S,
                    ),
                    timeout=TIMEOUT_S + 5,
                )
                return to_changes(parsed, notes, context), llm.model
            except Exception:
                logger.exception("change detection with Claude failed; using the rules")
        return detect.detect(notes, context), RULES_MODEL
