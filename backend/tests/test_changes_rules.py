"""Step 7 change logic without a database: the rule-based notes reader (including QA's
mid-project meeting with a specialist and someone's hours), questions and overrides,
structured edits, the checks on Claude's output, and the plan comparison.

Shared fixtures (the Steering Committee notes from the approved mockup and a small project
context) live in tests/changes_fixtures.py.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from app.ai.llm import LLM
from app.changes.compare import build_comparison, schedule_dates
from app.changes.context import NodeRow, PlanRow, RunMeta
from app.changes.derive import InvalidEditError, build_impact, derive_overrides, from_edits
from app.changes.detect import detect
from app.changes.llm import ChangeDetector, ProposedChange, ProposedChanges, to_changes
from app.changes.questions import validate_answers
from app.domain.changes import AddedMember, ChangeEdits, NewMember, PlanOverrides
from app.domain.common import SolverStatus
from tests.changes_fixtures import (
    ANA,
    ETL,
    LEO,
    MARK,
    NOTES,
    QA_NOTES,
    SALESFORCE,
    SAP,
    project_context,
    qa_context,
)

SPECIALIST = NewMember(
    full_name="Security Specialist",
    role_title="Security Specialist",
    hours_per_week=40,
    skills=["Security & Compliance Review"],
    worker_type="contractor",
)


# --------------------------------------------------------------------------- rules
def test_rules_read_the_steering_committee_notes() -> None:
    found = detect(NOTES, project_context())
    kinds = [(change.kind, change.target, change.to) for change in found]
    assert kinds == [
        ("scope", None, "Workday has to be added as a data source."),
        ("budget", None, 36_500_000),
        ("duration", SALESFORCE, 0),
        ("duration", None, None),  # "SAP" only partly names 1.1.2: asked, not guessed
    ]
    budget = found[1]
    assert (budget.from_, budget.quote, budget.speaker) == (
        37_500_000,
        "Finance is trimming the budget to $365K.",
        "Robert",
    )
    sap = found[3]
    assert [(option.id, option.from_, option.to) for option in sap.candidates] == [(SAP, 20, 30)]
    assert all(change.quote and change.quote in NOTES for change in found)


def test_rules_never_invent() -> None:
    context = project_context()
    # A meeting date isn't a deadline, "on track" isn't a change, a vague slip names nothing.
    assert detect("Kick-off — March 3, 2025\nEverything is on track.", context) == []
    assert detect("Things might slip a bit.", context) == []
    found = detect("Mark Chen is leaving the company. Go-live moves to Oct 17, 2025.", context)
    assert [(change.kind, change.target, change.to) for change in found] == [
        ("capacity", MARK, 0),
        ("deadline", None, "2025-10-17"),
    ]


def test_rules_read_qa_meeting_specialist_hours_deadline_and_budget() -> None:
    context = qa_context()
    found = detect(QA_NOTES, context)
    assert [(change.kind, change.action, change.target, change.to) for change in found] == [
        ("team", "add", None, None),  # hours not stated: asked
        ("capacity", None, LEO, 16),
        ("budget", None, None, 50_000_000),
        ("deadline", None, None, "2027-07-31"),
    ]
    assert found[0].member is not None
    assert (found[0].member.model_dump(), found[0].speaker, found[1].speaker) == (
        SPECIALIST.model_dump(),
        "Jane Doe",
        "Raj Patel",
    )

    # The API reserves an employee for the newcomer before the impact is built.
    reserved = uuid.uuid4()
    found[0] = found[0].model_copy(update={"target": reserved})
    impact = build_impact(found, "notes", context, "rules")
    by_id = {question.id: question for question in impact.questions}
    # No contractor rate on the project: the specialist's rate is asked too.
    assert set(by_id) == {"team:c1", "rate:c1", "capacity:c2", "budget", "deadline"}
    assert [option.value for option in by_id["team:c1"].options] == ["16", "24", "40", "keep"]
    assert [option.value for option in by_id["rate:c1"].options] == [
        "12500",
        "15000",
        "17500",
        "20000",
        "keep",
    ]
    assert "Security Specialist" in [person.name for person in impact.affects.people]
    answers = {"capacity:c2": "new", "budget": "50000000", "deadline": "2027-07-31"}
    assert derive_overrides(impact, {**answers, "team:c1": "24"}) is None  # the rate is open
    overrides = derive_overrides(impact, {**answers, "team:c1": "24", "rate:c1": "17500"})
    assert overrides == PlanOverrides(
        budget_total_cents=50_000_000,
        must_finish_by=date(2027, 7, 31),
        hours_per_week={str(LEO): 16},
        add_members=[
            AddedMember(
                employee_id=reserved,
                **SPECIALIST.model_copy(
                    update={"hours_per_week": 24, "hourly_rate_cents": 17_500}
                ).model_dump(by_alias=False),
            )
        ],
    )
    declined = derive_overrides(impact, {**answers, "team:c1": "keep", "rate:c1": "17500"})
    assert declined is not None
    assert declined.add_members is None


def test_notes_contractor_takes_the_projects_contractor_rate() -> None:
    context = replace(qa_context(), contractor_rate_cents=18_500)
    [specialist, *_] = detect(QA_NOTES, context)
    assert specialist.member is not None
    assert (specialist.member.worker_type, specialist.member.hourly_rate_cents) == (
        "contractor",
        18_500,
    )
    impact = build_impact(detect(QA_NOTES, context), "notes", context, "rules")
    assert not any(question.id.startswith("rate:") for question in impact.questions)


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("ana will be at 20 hours per week.", ("capacity", ANA, 20, None)),
        ("Leo Martins can only give 16 hours a week.", ("capacity", LEO, 16, None)),
        (
            "We need to hire a contractor for the data migration.",
            (
                "team",
                None,
                None,
                ("Data Migration Contractor", "Data Migration Contractor", "contractor"),
            ),
        ),
        (
            "Bring in Dana Lee as a security specialist at 24 hours a week.",
            ("team", None, 24, ("Dana Lee", "Security Specialist", "contractor")),
        ),
        (
            "We will hire a data engineer.",
            ("team", None, None, ("Data Engineer", "Data Engineer", "employee")),
        ),
        ("We will hire Ana Souza, a data engineer.", None),  # already on the team
    ],
)
def test_rules_read_people_phrases(sentence: str, expected: tuple[Any, ...] | None) -> None:
    found = detect(sentence, qa_context())
    if expected is None:
        assert found == []
        return
    [change] = found
    member = change.member
    newcomer = (member.full_name, member.role_title, member.worker_type) if member else None
    assert (change.kind, change.target, change.to, newcomer) == expected


def test_questions_and_overrides_from_notes() -> None:
    context = project_context()
    impact = build_impact(detect(NOTES, context), "notes", context, "rules")
    by_id = {question.id: question for question in impact.questions}
    assert set(by_id) == {"budget", "target:c4"}
    assert [option.value for option in by_id["budget"].options] == ["36500000", "keep"]
    assert "Plan v1 uses $375,000" in by_id["budget"].text
    assert {task.code for task in impact.affects.tasks} == {"1.1.1", "1.1.2", "1.2.2"}
    assert [person.name for person in impact.affects.people] == ["Mark Chen"]

    assert derive_overrides(impact, {"budget": "36500000"}) is None  # one still open
    option = derive_overrides(impact, {"budget": "36500000", "target:c4": str(SAP)})
    assert option == PlanOverrides(
        budget_total_cents=36_500_000, duration_days={str(SALESFORCE): 0, str(SAP): 30}
    )
    assert option.model_dump(mode="json") == {
        "budgetTotalCents": 36_500_000,
        "durationDays": {str(SALESFORCE): 0, str(SAP): 30},
    }
    kept = derive_overrides(impact, {"budget": "keep", "target:c4": "none"})
    assert kept == PlanOverrides(duration_days={str(SALESFORCE): 0})
    assert validate_answers(impact.questions, {"budget": "1"}) is not None
    assert validate_answers(impact.questions, {"nope": "keep"}) is not None


def test_edits_and_progress_are_taken_as_they_are() -> None:
    context = project_context()
    edits = ChangeEdits(
        budget_total_cents=36_500_000,
        must_finish_by=date(2025, 9, 30),  # unchanged: not a change
        hours_per_week={MARK: 20},
        progress={SAP: 50, SALESFORCE: 100, ETL: 0},
    )
    found = from_edits(edits, context)
    assert [(change.kind, change.target, change.to) for change in found] == [
        ("budget", None, 36_500_000),
        ("capacity", MARK, 20),
        ("duration", SAP, 10),
        ("duration", SALESFORCE, 0),
    ]
    impact = build_impact(found, "edits", context, "rules")
    assert impact.questions == []
    assert derive_overrides(impact, {}) == PlanOverrides(
        budget_total_cents=36_500_000,
        duration_days={str(SAP): 10, str(SALESFORCE): 0},
        hours_per_week={str(MARK): 20},
    )
    with pytest.raises(InvalidEditError):
        from_edits(ChangeEdits(duration_days={uuid.uuid4(): 5}), context)


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


# --------------------------------------------------------------------------- compare
def test_comparison_shows_what_moved_and_why() -> None:
    context = project_context()
    impact = build_impact(detect(NOTES, context), "notes", context, "rules")
    snapshot = {
        "start_date": "2025-01-13",
        "constraints": {"budget_total_cents": 37_500_000, "working_days_per_week": 5},
        "dependencies": [{"predecessor_id": str(SAP), "successor_id": str(ETL)}],
    }
    base = RunMeta(uuid.uuid4(), uuid.uuid4(), SolverStatus.OPTIMAL, 1, 1, None, snapshot)
    proposed = RunMeta(uuid.uuid4(), base.project_id, SolverStatus.OPTIMAL, None, 2, None, snapshot)
    root, group = uuid.uuid4(), uuid.uuid4()
    nodes = [
        NodeRow(root, None, 1, "1", "Platform"),
        NodeRow(group, root, 2, "1.1", "Ingestion"),
        NodeRow(SALESFORCE, group, 3, "1.1.1", "Salesforce connector development"),
        NodeRow(SAP, group, 3, "1.1.2", "SAP integration setup"),
        NodeRow(ETL, group, 3, "1.2.2", "ETL pipeline development"),
    ]
    before = [
        PlanRow(SALESFORCE, MARK, None, 0, 2),
        PlanRow(SAP, None, "SAP specialist", 3, 6),
        PlanRow(ETL, MARK, None, 7, 11),
    ]
    after = [PlanRow(SAP, None, "SAP specialist", 3, 8), PlanRow(ETL, MARK, None, 9, 13)]
    comparison = build_comparison(base, before, proposed, after, nodes, {MARK: "Mark Chen"}, impact)
    rows = {row.code: row for row in comparison.rows}
    assert rows["1.1.1"].after is None
    assert rows["1.1.1"].why == "Done, nothing left to plan"
    assert (rows["1.1.2"].shift_days, rows["1.1.2"].why) == (14, "Effort 4w → 6w")
    assert (rows["1.2.2"].shift_days, rows["1.2.2"].why) == (14, "Follows 1.1.2")
    assert rows["1.2.2"].group_code == "1.1"
    assert rows["1.1.2"].before is not None
    assert rows["1.1.2"].before.start == date(2025, 2, 3)
    assert comparison.base.budget_cents == 37_500_000


def test_comparison_uses_the_days_each_plan_schedules() -> None:
    """QA pack CHANGE-009: ETL moves one day (Jan 11 → Jan 12), not a whole week."""
    snapshot = {"start_date": "2027-01-04", "constraints": {"working_days_per_week": 5}}

    def meta(ends: dict[uuid.UUID, str]) -> RunMeta:
        schedule = schedule_dates(
            [
                {"wbsNodeId": str(node), "start": "2027-01-04", "end": end}
                for node, end in ends.items()
            ]
            + [{"wbsNodeId": "not-a-uuid", "start": "2027-01-04", "end": "2027-01-04"}]
        )
        return RunMeta(
            uuid.uuid4(), uuid.uuid4(), SolverStatus.OPTIMAL, 1, 1, None, snapshot, schedule
        )

    base = meta({SAP: "2027-01-05", ETL: "2027-01-11"})
    proposed = meta({SAP: "2027-01-05", ETL: "2027-01-12"})
    rows = [PlanRow(SAP, MARK, None, 0, 0), PlanRow(ETL, MARK, None, 1, 1)]
    nodes = [
        NodeRow(SAP, None, 2, "1.1", "SAP integration setup"),
        NodeRow(ETL, None, 2, "1.2", "ETL"),
    ]
    comparison = build_comparison(base, rows, proposed, rows, nodes, {MARK: "Mark Chen"}, None)
    by_code = {row.code: row for row in comparison.rows}
    assert by_code["1.1"].before is not None
    assert (by_code["1.1"].before.start, by_code["1.1"].before.end) == (
        date(2027, 1, 4),
        date(2027, 1, 5),
    )
    assert by_code["1.1"].shift_days == 0
    assert by_code["1.2"].shift_days == 1
