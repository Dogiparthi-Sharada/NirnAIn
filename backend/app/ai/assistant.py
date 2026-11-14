"""Planning assistant: the system prompt and the project context it answers from.

Pure functions; the endpoint reads the context under the caller's RLS session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.ai.llm import ChatMessage
from app.ai.prompting import DATA_RULE, as_data, data_block, one_line

MAX_SCOPE_PER_CATEGORY = 15
MAX_NODES = 150
MAX_RECOMMENDATIONS = 5

NOT_CONFIGURED = "AI assistant is not configured"
TIMED_OUT = "The AI assistant took too long to answer. Please try again."
UNAVAILABLE = "The AI assistant is unavailable right now. Please try again."


@dataclass(frozen=True, slots=True)
class WbsLine:
    code: str
    name: str
    level: int
    duration_days: int | None
    owner: str | None


@dataclass(frozen=True, slots=True)
class ProjectContext:
    name: str
    description: str | None = None
    status: str | None = None
    current_step: str | None = None
    start_date: date | None = None
    target_end_date: date | None = None
    budget_cents: int | None = None
    scope: dict[str, list[str]] = field(default_factory=dict)  # accepted, by category
    wbs: tuple[WbsLine, ...] = ()
    constraints: tuple[str, ...] = ()  # "Budget: $375,000 (Must hold)" ...
    run_status: str | None = None
    run_kpis: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()


def money(cents: int) -> str:
    return f"${cents / 100:,.0f}"


def context_text(ctx: ProjectContext) -> str:  # noqa: PLR0912
    lines = [f"Project: {as_data(one_line(ctx.name, 200))}"]
    if ctx.description:
        lines.append(f"Description: {as_data(one_line(ctx.description, 1000))}")
    if ctx.status or ctx.current_step:
        lines.append(f"Status: {ctx.status or 'unknown'}; workflow step: {ctx.current_step or '-'}")
    if ctx.start_date or ctx.target_end_date:
        start, end = ctx.start_date or "not set", ctx.target_end_date or "not set"
        lines.append(f"Dates: start {start}, target end {end}")
    if ctx.budget_cents is not None:
        lines.append(f"Budget: {money(ctx.budget_cents)}")
    if ctx.scope:
        lines.append("Accepted scope:")
        for category, texts in ctx.scope.items():
            for text in texts[:MAX_SCOPE_PER_CATEGORY]:
                lines.append(f"- [{category}] {as_data(one_line(text, 300))}")
    if ctx.wbs:
        lines.append("WBS:")
        for node in ctx.wbs[:MAX_NODES]:
            parts = [f"{node.code} {as_data(one_line(node.name, 150))}"]
            if node.duration_days is not None:
                parts.append(f"{node.duration_days} working days")
            if node.owner:
                parts.append(f"owner {as_data(one_line(node.owner, 100))}")
            lines.append("  " * (node.level - 1) + "; ".join(parts))
    if ctx.constraints:
        lines.append("Constraints:")
        lines.extend(f"- {c}" for c in ctx.constraints)
    if ctx.run_status:
        lines.append(f"Latest optimization run: {ctx.run_status}")
        lines.extend(f"- {as_data(k)}" for k in ctx.run_kpis)
        if ctx.recommendations:
            lines.append("Top recommendations:")
            lines.extend(
                f"- {as_data(one_line(r, 300))}" for r in ctx.recommendations[:MAX_RECOMMENDATIONS]
            )
    return "\n".join(lines)


def system_prompt(page: str, ctx: ProjectContext | None) -> str:
    parts = [
        "You are NirnAIn's planning assistant, helping a project manager use an AI-assisted "
        "project-planning tool (meeting notes -> scope -> WBS -> constraints -> optimal plan "
        "-> versions). Answer in at most 150 words, plainly, without markdown tables. "
        "Use only the project data below and general project-management knowledge; when the "
        "data doesn't say something, say so instead of guessing, and never invent numbers, "
        "names or dates. " + DATA_RULE,
        f"The user is on the '{as_data(one_line(page, 40))}' screen.",
    ]
    if ctx is None:
        parts.append("No project is open.")
    else:
        parts.append(data_block(context_text(ctx)))
    return "\n\n".join(parts)


def kpi_lines(kpis: Sequence[object]) -> tuple[str, ...]:
    """`result.kpis` of an optimization run -> 'Budget: $1 (sub)' lines; ignores junk."""
    out: list[str] = []
    for kpi in kpis[:6]:
        if isinstance(kpi, dict) and kpi.get("label") and kpi.get("value"):
            sub = f" ({one_line(str(kpi['sub']), 120)})" if kpi.get("sub") else ""
            out.append(f"{one_line(str(kpi['label']), 60)}: {one_line(str(kpi['value']), 60)}{sub}")
    return tuple(out)


def chat_messages(messages: Sequence[tuple[str, str]]) -> list[ChatMessage]:
    """(role, content) pairs -> what the API accepts: starts with the user, roles alternate.

    Leading assistant turns (the UI's greeting) and empty turns are dropped;
    consecutive turns of the same role are joined.
    """
    out: list[ChatMessage] = []
    for role, content in messages:
        text = content.strip()
        if not text or (not out and role != "user") or role not in ("user", "assistant"):
            continue
        if out and out[-1].role == role:
            out[-1] = ChatMessage(out[-1].role, f"{out[-1].content}\n\n{text}")
        else:
            out.append(ChatMessage("user" if role == "user" else "assistant", text))
    return out
