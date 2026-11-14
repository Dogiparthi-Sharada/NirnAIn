"""Claude reading of new meeting notes (when ANTHROPIC_API_KEY is set).

The model only proposes changes; every one is checked against the notes and the project
before it is kept: the quote must appear in the notes verbatim, the work package or
person must exist, and the value must parse. Questions are always derived by rules
(`app.changes.derive`), so answering them maps to overrides deterministically.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.llm import LLM
from app.ai.prompting import DATA_RULE, as_data, data_block, one_line
from app.changes import detect
from app.changes import text as tx
from app.changes.context import ChangeContext
from app.domain.changes import DetectedChange

logger = logging.getLogger("app.changes")

RULES_MODEL = "rules"
TIMEOUT_S = 60.0
MAX_CHANGES = 20

SYSTEM_PROMPT = (
    "You compare new meeting notes with a project's applied plan and list what changed: "
    "the total budget, the deadline, the duration of a listed work package (including work "
    "that is now done), the weekly hours of a listed team member (0 when they leave), or new "
    "or removed scope. Report only changes the notes state. Never invent numbers, dates, "
    "people or work packages; skip anything that is merely on track or unchanged. "
    "For every change copy the sentence it comes from verbatim into `quote`. " + DATA_RULE
)


class ProposedChange(BaseModel):
    kind: Literal["budget", "deadline", "duration", "capacity", "scope"]
    target: str | None = Field(
        default=None,
        description="duration: the work package code exactly as listed; capacity: the "
        "person's full name exactly as listed; otherwise null",
    )
    value: str | None = Field(
        default=None,
        description="budget: new total in US dollars (digits only); deadline: YYYY-MM-DD; "
        "duration: new total working days (0 = done); capacity: hours per week (0 = leaves); "
        "scope: the requirement in one sentence",
    )
    quote: str
    confidence: float = Field(ge=0, le=1)


class ProposedChanges(BaseModel):
    changes: list[ProposedChange]


def build_prompt(notes: str, ctx: ChangeContext) -> str:
    lines = [
        f"Today: {ctx.today.isoformat()}",
        f"Applied plan: {ctx.plan_label}",
        f"Total budget: {tx.fmt_money(ctx.budget_cents)}",
        f"Deadline: {ctx.deadline.isoformat() if ctx.deadline else 'none'}",
        "Work packages (code | name | working days):",
        *(
            f"- {t.code} | {as_data(one_line(t.name, 200))} | {t.duration_days or 0}"
            for t in ctx.tasks
        ),
        "Team (full name | hours per week):",
        *(f"- {as_data(one_line(p.name, 120))} | {p.hours_per_week:g}" for p in ctx.people),
        "",
        "New meeting notes:",
        as_data(notes),
    ]
    return data_block("\n".join(lines)) + "\n\nList what changed."


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def to_changes(out: ProposedChanges, notes: str, ctx: ChangeContext) -> list[DetectedChange]:
    """Keep only what the notes support and the project contains."""
    haystack = _norm(notes)
    kept: list[DetectedChange] = []
    for c in out.changes[:MAX_CHANGES]:
        quote = " ".join(c.quote.split()).strip()
        if not quote or _norm(quote) not in haystack:
            continue
        change = _one(c, quote, ctx, f"c{len(kept) + 1}")
        if change is not None:
            kept.append(change)
    return kept


def _one(c: ProposedChange, quote: str, ctx: ChangeContext, cid: str) -> DetectedChange | None:  # noqa: PLR0911
    value = (c.value or "").strip()
    base = {"id": cid, "kind": c.kind, "quote": quote, "confidence": c.confidence}
    if c.kind == "budget":
        digits = re.sub(r"[^\d.]", "", value)
        if not digits:
            return None
        cents = round(float(digits) * 100)
        if cents <= 0 or cents == ctx.budget_cents:
            return None
        return DetectedChange.model_validate({**base, "from_": ctx.budget_cents, "to": cents})
    if c.kind == "deadline":
        try:
            new = date.fromisoformat(value)
        except ValueError:
            return None
        if new == ctx.deadline:
            return None
        old = ctx.deadline.isoformat() if ctx.deadline else None
        return DetectedChange.model_validate({**base, "from_": old, "to": new.isoformat()})
    if c.kind == "scope":
        return DetectedChange.model_validate(
            {
                **base,
                "to": value or quote,
                "note": "Re-planning doesn't add work. Add it as a work package in WBS Builder, "
                "then record the change again.",
            }
        )
    try:
        number = float(value)
    except ValueError:
        return None
    target = (c.target or "").strip()
    if c.kind == "duration":
        task = next(
            (t for t in ctx.tasks if t.code == target or t.name.lower() == target.lower()), None
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
    person = next((p for p in ctx.people if p.name.lower() == target.lower()), None)
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


class ChangeDetector:
    """Claude when configured, else (or when the call fails) the deterministic rules."""

    def __init__(self, llm: LLM | None) -> None:
        self._llm = llm

    async def detect(self, notes: str, ctx: ChangeContext) -> tuple[list[DetectedChange], str]:
        llm = self._llm
        if llm is not None and llm.configured:
            try:
                parsed, _usage = await asyncio.wait_for(
                    asyncio.to_thread(
                        llm.parse,
                        system=SYSTEM_PROMPT,
                        prompt=build_prompt(notes, ctx),
                        output=ProposedChanges,
                        max_tokens=4000,
                        timeout_s=TIMEOUT_S,
                    ),
                    timeout=TIMEOUT_S + 5,
                )
                return to_changes(parsed, notes, ctx), llm.model
            except Exception:
                logger.exception("change detection with Claude failed; using the rules")
        return detect.detect(notes, ctx), RULES_MODEL
