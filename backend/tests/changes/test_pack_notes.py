"""Tests of the rules change detection against the QA pack's meetings M02 and M03.

Purpose: QA found the rules reader proposing values the notes never decide and dropping "must hold".
Prove every R8 rule with the pack's sentences: "must hold" makes a date or budget hard; hours change
only with "<Name> … N hours a week"; rejected or replaced values, actual spend, rates, the reserve
and fixed costs are never proposed; "done" needs a named work package.
How it fits: Pure tests of `app.changes.detect`, `questions` and `derive` on the fictional meetings
in tests/fixtures/qa_pack (PRJ-QA-1042); layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from app.changes.context import ChangeContext, PersonRef, TaskRef
from app.changes.derive import build_impact, derive_overrides
from app.changes.detect import detect
from app.domain.changes import DetectedChange

PACK = Path(__file__).parent.parent / "fixtures" / "qa_pack"
# The pack's work packages (W01 to W15 of M01) and people with their M01 hours.
PACKAGES = {
    "W01": ("1.1.1", "Salesforce connector"),
    "W02": ("1.1.2", "SAP connector"),
    "W03": ("1.1.3", "Internal database ingestion"),
    "W04": ("1.2.1", "Schema design"),
    "W05": ("1.2.2", "ETL pipeline"),
    "W07": ("1.3.2", "Standard reports"),
    "W09": ("1.4.1", "RBAC"),
    "W10": ("1.4.2", "Audit logging"),
    "W12": ("1.4.4", "Infrastructure setup"),
    "W13": ("1.5.1", "UAT"),
    "W14": ("1.5.2", "User training"),
}
TASK_IDS = {key: uuid.uuid4() for key in PACKAGES}
PEOPLE = {"Sara Kim": 40, "Mark Chen": 40, "Tom Rivera": 32, "Lisa Park": 20, "James Wu": 16}
PERSON_IDS = {name: uuid.uuid4() for name in [*PEOPLE, "Priya Shah"]}


def pack_context(*, after_m02: bool = False) -> ChangeContext:
    """PRJ-QA-1042 with Plan v1 in force (before M02) or Plan v2 (M02 applied)."""
    done = {"W01", "W04"} if after_m02 else set()
    hours = {**PEOPLE, "Priya Shah": 32}
    if after_m02:
        hours |= {"Mark Chen": 24, "Lisa Park": 32}
    return ChangeContext(
        budget_cents=35_000_000 if after_m02 else 37_500_000,
        deadline=date(2027, 7, 30),
        start=date(2027, 1, 4),
        tasks=tuple(
            TaskRef(TASK_IDS[key], code, f"{key} {name}", 0 if key in done else 10)
            for key, (code, name) in PACKAGES.items()
        ),
        people=tuple(PersonRef(PERSON_IDS[name], name, value) for name, value in hours.items()),
        plan_version=2 if after_m02 else 1,
        today=date(2027, 2, 1) if after_m02 else date(2027, 1, 18),
    )


def summary(found: list[DetectedChange]) -> list[tuple[object, ...]]:
    """(kind, target label, value, mode) of each change, in reading order."""
    return [(change.kind, change.target_label, change.to, change.mode) for change in found]


M02_EXPECTED = [
    ("scope", None, "Add W16 as another predecessor of W13.", None),
    ("budget", None, 35_000_000, "soft"),
    ("capacity", "Mark Chen", 24, None),
    ("capacity", "Lisa Park", 32, None),
    ("capacity", "James Wu", 0, None),  # leave: asked, with the "limited period" note
    ("duration", "1.1.1 W01 Salesforce connector", 0, None),
    ("duration", "1.2.1 W04 Schema design", 0, None),
]
M03_EXPECTED = [
    ("deadline", None, "2027-04-30", "hard"),
    ("budget", None, 20_000_000, "hard"),
    ("duration", "1.4.1 W09 RBAC", 0, None),
    ("duration", "1.4.4 W12 Infrastructure setup", 0, None),
]


@pytest.mark.parametrize("suffix", ["txt", "vtt"])
def test_m02_proposes_only_what_the_meeting_decides(suffix: str) -> None:
    """M02 (text and VTT) yields exactly the seven changes the meeting decides, with speakers."""
    [path] = PACK.glob(f"M02_*.{suffix}")
    found = detect(path.read_text(), pack_context())
    assert summary(found) == M02_EXPECTED
    assert [change.id for change in found] == [f"c{number}" for number in range(1, 8)]
    assert found[1].speaker == "Robert Walsh, sponsor"


@pytest.mark.parametrize("suffix", ["txt", "vtt"])
def test_m03_reads_the_hard_deadline_and_budget(suffix: str) -> None:
    """M03 (text and VTT) yields the hard deadline and budget and the two finished packages."""
    [path] = PACK.glob(f"M03_*.{suffix}")
    found = detect(path.read_text(), pack_context(after_m02=True))
    assert summary(found) == M03_EXPECTED
    assert found[0].quote == "The board now requires go-live by April 30, 2027."


def test_m03_hard_modes_reach_the_question_and_the_re_plan() -> None:
    """A hard deadline shows as "must hold" and reaches the overrides; keeping drops the modes."""
    context = pack_context(after_m02=True)
    [path] = PACK.glob("M03_*.txt")
    impact = build_impact(detect(path.read_text(), context), "notes", context, "rules")
    by_id = {question.id: question for question in impact.questions}
    assert [option.label for option in by_id["deadline"].options] == [
        "Use Apr 30, 2027 · must hold",
        "Keep Jul 30, 2027",
    ]
    overrides = derive_overrides(impact, {"budget": "20000000", "deadline": "2027-04-30"})
    assert overrides is not None
    assert overrides.model_dump(mode="json") == {
        "budgetTotalCents": 20_000_000,
        "mustFinishBy": "2027-04-30",
        "durationDays": {str(TASK_IDS["W09"]): 0, str(TASK_IDS["W12"]): 0},
        "budgetMode": "hard",
        "deadlineMode": "hard",
    }
    kept = derive_overrides(impact, {"budget": "keep", "deadline": "keep"})
    assert kept is not None
    assert (kept.budget_mode, kept.deadline_mode) == (None, None)


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        # "must hold" next to a new date or budget (QA pack mutations).
        (
            "Jane Doe, project manager: Replace go-live target with 2027-01-22 and Must hold.",
            [("deadline", None, "2027-01-22", "hard")],
        ),
        (
            "Jane Doe, project manager: Replace total budget with USD 200000 and Must hold, "
            "reserve stays10%.",
            [("budget", None, 20_000_000, "hard")],
        ),
        (
            "The go-live date of Sep 30, 2027 cannot move.",
            [("deadline", None, "2027-09-30", "hard")],
        ),
        (
            "Go-live Aug 31, 2027 is a hard deadline and the budget rises to $400,000.",
            [("budget", None, 40_000_000, None), ("deadline", None, "2027-08-31", "hard")],
        ),
        # Hours only with "<Name> … N hours a week".
        (
            "Add mandatory W16 (1.4.5) Disaster recovery drill: 80 hours, Lisa, Disaster "
            "Recovery, after W12.",
            [],
        ),
        ("Lisa needs 80 hours for the drill.", []),
        (
            "Lisa now gives 32 hours a week instead of 20 hours a week.",
            [("capacity", "Lisa Park", 32, None)],
        ),
        # Rejected / declined / replaced values are not proposed.
        ("We discussed increasing the budget to $400,000 but rejected that proposal.", []),
        ("The sponsor declined to move go-live to Sep 30, 2027.", []),
        (
            "The budget is $380,000 instead of $375,000 we approved.",
            [("budget", None, 38_000_000, None)],
        ),
        # "Done" only for an explicitly named work package.
        ("I can be considered for future UAT but no completed work should be reassigned.", []),
        ("W05 starts once the SAP connector is done.", []),
        ("The Schema design will be done next week.", []),
        ("We finished the Schema design.", [("duration", "1.2.1 W04 Schema design", 0, None)]),
        # Actual spend, rates, the reserve and fixed costs are never the budget.
        ("W12 has 40 actual hours and $4,400 actual cost.", []),
        ("Priya is still allowed at $175 per hour, 32 hours per week, beginning February 8.", []),
        ("Contingency remains 10% inside the total, so $20,000 is reserved.", []),
        ("Keep fixed infrastructure at $60,000 and the two category targets unchanged.", []),
        ("We have spent $120,000 of the budget so far.", []),
    ],
)
def test_r8_rules_on_single_sentences(sentence: str, expected: list[tuple[object, ...]]) -> None:
    """Each R8 sentence yields exactly the expected changes (often none)."""
    assert summary(detect(sentence, pack_context())) == expected
