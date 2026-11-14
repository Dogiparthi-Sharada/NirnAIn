"""The questions a change raises (step 7) and checking the answers.

Business rule ("never invent"): a value from meeting notes that conflicts with one you
confirmed (budget, deadline, an effort estimate, someone's hours) is asked, never
overwritten. A change whose work package or person the notes don't name exactly asks
which one it means. Someone the notes bring in without saying their hours is asked about
(16, 24 or 40 h a week, or not at all). Your own edits and progress updates need no questions.

Questions are always built here by rules, also when Claude read the notes, so that each
answer maps to an override deterministically (see `app.changes.derive`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Literal

from app.changes import parsing
from app.changes.context import ChangeContext
from app.domain.changes import ChangeQuestion, ChangeSource, DetectedChange, QuestionOption

# Option values with a fixed meaning; other values are amounts, ISO dates or target ids.
KEEP = "keep"
NEW = "new"
NONE = "none"
# Hours a week offered for a newcomer from the notes who comes without hours.
HOURS_CHOICES = (16, 24, 40)


def build_questions(
    changes: list[DetectedChange], source: ChangeSource, context: ChangeContext
) -> list[ChangeQuestion]:
    """Values from notes that conflict with ones you confirmed are asked, never overwritten.

    Your own edits and progress need no questions. A change whose work package or person
    is unclear always asks which one it means.
    """
    questions: list[ChangeQuestion] = []
    for change in changes:
        if change.target is None and change.candidates:
            questions.append(_target_question(change))
    if source != "notes":
        return questions

    budgets = [
        change
        for change in changes
        if change.kind == "budget" and isinstance(change.to, int | float)
    ]
    if budgets:
        current = None if context.budget_cents is None else str(context.budget_cents)
        questions.append(
            _group_question(
                "budget", budgets, context, lambda value: parsing.format_money(int(value)), current
            )
        )
    deadlines = [
        change for change in changes if change.kind == "deadline" and isinstance(change.to, str)
    ]
    if deadlines:
        questions.append(
            _group_question(
                "deadline",
                deadlines,
                context,
                lambda value: parsing.format_date(date.fromisoformat(value)),
                context.deadline.isoformat() if context.deadline else None,
            )
        )
    for change in changes:
        if change.kind == "team" and change.action == "add" and change.to is None:
            questions.append(_team_question(change, context))
        if change.target is None:
            continue
        if change.kind == "duration" and change.to != 0:
            questions.append(
                ChangeQuestion(
                    id=f"duration:{change.id}",
                    kind="duration",
                    change_id=change.id,
                    tier="blocking",
                    text=f"Effort for {change.target_label}",
                    why=f"{context.plan_label} uses {_weeks(change.from_)}. The notes change "
                    "it; confirm before it is used.",
                    options=[
                        QuestionOption(value=NEW, label=f"Confirm {_weeks(change.to)}"),
                        QuestionOption(
                            value=KEEP,
                            label=f"Keep {_weeks(change.from_)}",
                        ),
                    ],
                )
            )
        elif change.kind == "capacity":
            questions.append(_capacity_question(change, context))
    return questions


def _weeks(value: object) -> str:
    return parsing.format_days(_as_number(value))


def _hours(value: object) -> str:
    return parsing.format_hours(_as_number(value))


def _as_number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _target_question(change: DetectedChange) -> ChangeQuestion:
    def format_value(value: int | float | None) -> str:
        return (
            parsing.format_days(value) if change.kind == "duration" else parsing.format_hours(value)
        )

    return ChangeQuestion(
        id=f"target:{change.id}",
        kind="target",
        change_id=change.id,
        tier="blocking",
        text="Which work package does this refer to?"
        if change.kind == "duration"
        else "Who does this refer to?",
        why="The notes don't name it exactly, so we won't guess.",
        options=[
            QuestionOption(
                value=str(option.id),
                label=f"{option.label} ({format_value(option.from_)} → {format_value(option.to)})",
            )
            for option in change.candidates
        ]
        + [QuestionOption(value=NONE, label="None of these")],
    )


def _group_question(
    kind: Literal["budget", "deadline"],
    changes: list[DetectedChange],
    context: ChangeContext,
    format_value: Callable[[str], str],
    current: str | None,
) -> ChangeQuestion:
    """One question for all budget (or deadline) values the notes give, plus "keep"."""
    values: list[str] = parsing.unique(str(change.to) for change in changes)
    noun = "budget" if kind == "budget" else "deadline"
    if current is None:
        text = f"The notes give a {noun}. Which should the new plan use?"
        keep_label = f"Don't set a {noun}"
    else:
        text = (
            "This conflicts with a value you confirmed. "
            f"{context.plan_label} uses {format_value(current)}. Which should the new plan use?"
        )
        keep_label = f"Keep {format_value(current)}"
    return ChangeQuestion(
        id=kind,
        kind=kind,
        change_id=changes[0].id,
        tier="blocking",
        text=text,
        options=[
            QuestionOption(value=value, label=f"Use {format_value(value)}") for value in values
        ]
        + [QuestionOption(value=KEEP, label=keep_label)],
    )


def _capacity_question(change: DetectedChange, context: ChangeContext) -> ChangeQuestion:
    name = change.target_label or "this person"
    if change.to == 0:
        return ChangeQuestion(
            id=f"capacity:{change.id}",
            kind="capacity",
            change_id=change.id,
            tier="high",
            text=f"Take {name} off the plan?",
            why=change.note
            or f"Their work moves to others in the re-plan. {context.plan_label} keeps "
            "them until you approve.",
            options=[
                QuestionOption(value=NEW, label=f"Yes, take {parsing.first_name(name)} off"),
                QuestionOption(value=KEEP, label=f"No, keep {parsing.first_name(name)}"),
            ],
        )
    return ChangeQuestion(
        id=f"capacity:{change.id}",
        kind="capacity",
        change_id=change.id,
        tier="high",
        text=f"Hours per week for {name}",
        why=f"{context.plan_label} uses {_hours(change.from_)} a week.",
        options=[
            QuestionOption(value=NEW, label=f"Use {_hours(change.to)}"),
            QuestionOption(value=KEEP, label=f"Keep {_hours(change.from_)}"),
        ],
    )


def _team_question(change: DetectedChange, context: ChangeContext) -> ChangeQuestion:
    """How many hours a newcomer from the notes gives the project (the notes don't say)."""
    name = change.member.full_name if change.member else "them"
    return ChangeQuestion(
        id=f"team:{change.id}",
        kind="team",
        change_id=change.id,
        tier="blocking",
        text=f"Hours per week for {name}",
        why="The notes bring someone in but don't say how many hours they give the project. "
        f"{context.plan_label} keeps the team as it is until you approve.",
        options=[
            QuestionOption(value=str(hours), label=f"{parsing.format_hours(hours)} a week")
            for hours in HOURS_CHOICES
        ]
        + [QuestionOption(value=KEEP, label="Don't bring them in")],
    )


def validate_answers(questions: list[ChangeQuestion], answers: dict[str, str]) -> str | None:
    """None when every answer matches a question and one of its options, else the problem."""
    by_id = {question.id: question for question in questions}
    for question_id, value in answers.items():
        question = by_id.get(question_id)
        if question is None:
            return f"Unknown question: {question_id}"
        if value not in {option.value for option in question.options}:
            return f"Not an option for {question_id}: {value}"
    return None
