"""Moved test support."""

from __future__ import annotations

from typing import Any

from app.ai.llm import LLM
from app.changes.llm import ChangeDetector, ProposedChange, ProposedChanges, to_changes
from tests.changes_fixtures import (
    NOTES,
    QA_NOTES,
    SAP,
    SPECIALIST,
    project_context,
    qa_context,
)


# --------------------------------------------------------------------------- Claude path
def test_claude_changes_are_checked_against_notes_and_project() -> None:
    context = project_context()
    out = ProposedChanges(
        changes=[
            ProposedChange(
                kind="budget",
                value="$365,000",
                quote="Finance is trimming the budget to $365K.",
                confidence=0.9,
            ),
            ProposedChange(
                kind="duration",
                target="1.1.2",
                value="30",
                quote="sandbox access slips two weeks.",
                confidence=0.7,
            ),
            # Not in the notes / unknown person: dropped.
            ProposedChange(kind="budget", value="1", quote="Budget is $1.", confidence=1),
            ProposedChange(
                kind="capacity",
                target="Nobody",
                value="0",
                quote="The Salesforce connector is done.",
                confidence=1,
            ),
        ]
    )
    kept = to_changes(out, NOTES, context)
    assert [(change.kind, change.target, change.to) for change in kept] == [
        ("budget", None, 36_500_000),
        ("duration", SAP, 30),
    ]


class _FakeLLM(LLM):
    """Answers every call with `out` and records the prompts; never calls Claude."""

    def __init__(self, out: ProposedChanges) -> None:
        super().__init__(client=None, model="claude-sonnet-5-5")
        self.out = out
        self.prompts: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def parse(self, **fields: Any) -> Any:
        self.prompts.append(fields["prompt"])
        return self.out, None


async def test_claude_may_report_a_newcomer_and_hours() -> None:
    quote = "We agreed to bring in an outside security specialist for the compliance review."
    llm = _FakeLLM(
        ProposedChanges(
            changes=[
                ProposedChange(
                    kind="team",
                    role="Security Specialist",
                    contractor=True,
                    skills=["security & compliance review", "Knitting"],
                    quote=quote,
                    confidence=0.8,
                ),
                ProposedChange(
                    kind="capacity",
                    target="Leo Martins",
                    value="16",
                    quote="Leo can only give 16 hours a week from February.",
                    confidence=0.9,
                ),
                # Already on the team, or without a role: dropped.
                ProposedChange(
                    kind="team", target="Leo Martins", role="Dev", quote=quote, confidence=1
                ),
                ProposedChange(kind="team", quote=quote, confidence=1),
            ]
        )
    )
    found, model = await ChangeDetector(llm).detect(QA_NOTES, qa_context())
    assert model == "claude-sonnet-5-5"
    assert [
        (change.kind, change.action, change.to, change.member and change.member.model_dump())
        for change in found
    ] == [
        ("team", "add", None, SPECIALIST.model_dump()),
        ("capacity", None, 16, None),
    ]
    assert "- Security & Compliance Review" in llm.prompts[0]


class _BrokenLLM(LLM):
    def __init__(self) -> None:
        super().__init__(client=None, model="claude-sonnet-5-5")

    @property
    def configured(self) -> bool:
        return True

    def parse(self, **_: Any) -> Any:
        raise RuntimeError("boom")


async def test_detector_falls_back_to_rules() -> None:
    context = project_context()
    found, model = await ChangeDetector(_BrokenLLM()).detect(NOTES, context)
    assert model == "rules"
    assert [change.kind for change in found] == ["scope", "budget", "duration", "duration"]
    _, model = await ChangeDetector(None).detect(NOTES, context)
    assert model == "rules"
