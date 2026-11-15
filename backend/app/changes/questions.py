"""The questions a change raises (step 7) and checking the answers.

Purpose: "Never invent": a value from the notes that conflicts with one you confirmed is asked,
never overwritten; a work package or person the notes don't name exactly is asked about; so are a
newcomer's hours and a contractor's rate when the project has none. Edits need no questions.
How it fits: Built by rules here even when Claude read the notes, so each answer maps to an override
deterministically (derive.py); validated by `validate_answers` in the API; layer: changes.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Literal

from app.changes import parsing
from app.changes.context import ChangeContext
from app.domain.changes import (
    ChangeMode,
    ChangeQuestion,
    ChangeSource,
    DetectedChange,
    QuestionOption,
)

# Option values with a fixed meaning; other values are amounts, ISO dates or target ids.
KEEP = "keep"
NEW = "new"
NONE = "none"
# Hours a week offered for a newcomer from the notes who comes without hours.
HOURS_CHOICES = (16, 24, 40)
# How an option names the policy the notes state with its value.
MODE_LABELS: dict[ChangeMode, str] = {"hard": " · must hold", "soft": " · can flex"}
# Hourly rates (cents) offered for a contractor from the notes when the project has none.
RATE_CHOICES = (12_500, 15_000, 17_500, 20_000)


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
        member = change.member
        if member and member.worker_type == "contractor" and member.hourly_rate_cents is None:
            questions.append(_rate_question(change))
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
                    why=f"{context.plan_label} uses {parsing.days_text(change.from_)}. "
                    "The notes change it; confirm before it is used.",
                    options=[
                        QuestionOption(value=NEW, label=f"Confirm {parsing.days_text(change.to)}"),
                        QuestionOption(
                            value=KEEP,
                            label=f"Keep {parsing.days_text(change.from_)}",
                        ),
                    ],
                )
            )
        elif change.kind == "capacity":
            questions.append(_capacity_question(change, context))
    return questions


def _target_question(change: DetectedChange) -> ChangeQuestion:
    """Ask which work package or person an ambiguous change refers to."""

    def format_value(value: int | float | None) -> str:
        """Format a candidate's value as days (durations) or hours (capacity)."""
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
    # A value the notes give with a policy says so: "Use Apr 30, 2027 · must hold".
    modes = {str(change.to): MODE_LABELS[change.mode] for change in changes if change.mode}
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
            QuestionOption(
                value=value,
                label=f"Use {format_value(value)}{modes.get(value, '')}",
            )
            for value in values
        ]
        + [QuestionOption(value=KEEP, label=keep_label)],
    )


def _capacity_question(change: DetectedChange, context: ChangeContext) -> ChangeQuestion:
    """Ask whether to take someone off the plan, or which weekly hours to plan them at."""
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
        why=f"{context.plan_label} uses {parsing.hours_text(change.from_)} a week.",
        options=[
            QuestionOption(value=NEW, label=f"Use {parsing.hours_text(change.to)}"),
            QuestionOption(value=KEEP, label=f"Keep {parsing.hours_text(change.from_)}"),
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


def _rate_question(change: DetectedChange) -> ChangeQuestion:
    """The hourly rate of a contractor the notes bring in (the project has no contractor rate)."""
    name = change.member.full_name if change.member else "the contractor"
    return ChangeQuestion(
        id=f"rate:{change.id}",
        kind="rate",
        change_id=change.id,
        tier="blocking",
        text=f"Hourly rate for {name}",
        why="Contractors count toward the budget at their own rate, and the project has no "
        "contractor rate yet.",
        options=[
            QuestionOption(value=str(cents), label=f"{parsing.format_money(cents)}/h")
            for cents in RATE_CHOICES
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
