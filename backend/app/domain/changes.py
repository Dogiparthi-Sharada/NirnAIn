"""API models of Changes & Versions (step 7): something changed after a plan was applied.

A change request records what changed (from meeting notes, direct edits or a progress
update), the questions it raises, the answers, and the re-plan it led to. Changes can also
bring people onto the project or take them off it mid-way (kind "team"). JSON is camelCase
(CamelModel). Used by app/api/v1/changes.py, the pure logic in app/changes/ and the
repositories; the frontend mirrors these types in src/lib/changes.ts.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.domain.common import CamelModel, InputModel, SolverStatus
from app.domain.team import HoursPerWeek, PersonName, RoleTitle, SkillNames, clean_skill_names

ChangeSource = Literal["notes", "edits", "progress"]
ChangeKind = Literal["budget", "deadline", "duration", "capacity", "scope", "team"]
QuestionKind = Literal["budget", "deadline", "duration", "capacity", "target", "team", "rate"]
# team: someone joins / leaves the project; scope: a requirement is added / removed.
ChangeAction = Literal["add", "remove"]
# Employees are people already in the company; contractors are hired in at their own rate.
WorkerType = Literal["employee", "contractor"]
# A budget / deadline policy the notes state: hard = must hold, soft = can flex.
ChangeMode = Literal["hard", "soft"]

MAX_TEXT = 50_000
MAX_EDITS = 500
MAX_NEW_MEMBERS = 10
MAX_HOURLY_RATE_CENTS = 10_000_000


class ChangeStatus(StrEnum):
    """Lifecycle: ready → replanning (C1 run started) → proposed (run solved) →
    approved | rejected. `proposed` is derived on read from the run's status."""

    DRAFT = "draft"
    ANALYZING = "analyzing"
    READY = "ready"
    REPLANNING = "replanning"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


DECIDED = (ChangeStatus.APPROVED, ChangeStatus.REJECTED)


def _omit_none(value: object) -> bool:
    return value is None


class TargetOption(CamelModel):
    """A work package (or person) an ambiguous change may refer to."""

    id: uuid.UUID
    label: str
    from_: int | float | None = Field(alias="from")
    to: int | float | None


class NewMember(InputModel):
    """Someone who joins the project mid-way: an employee or a contractor.

    Rates follow the team rule (`app.changes.team.team_edits` enforces it): an employee
    costs their company record's rate, so none is given here; a contractor's hourly rate is
    required, and the project's contractor rate is the default when one is set.
    """

    full_name: PersonName
    role_title: RoleTitle
    hours_per_week: HoursPerWeek
    skills: SkillNames = Field(default_factory=list)
    worker_type: WorkerType = "employee"
    hourly_rate_cents: int | None = Field(default=None, ge=0, le=MAX_HOURLY_RATE_CENTS)

    @field_validator("full_name", "role_title")
    @classmethod
    def _squash(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("skills")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return clean_skill_names(value)


class StoredMember(NewMember):
    """A newcomer as stored in a change's impact. Changes recorded before worker types carry
    `isContractor`: read as the worker type, so those changes still load."""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _legacy_contractor(cls, data: Any) -> Any:
        if isinstance(data, dict) and "workerType" not in data and data.get("isContractor"):
            return {**data, "workerType": "contractor"}
        return data


class AddedMember(NewMember):
    """A new member in the re-plan overrides, with the organization employee the change
    reserved for them (reused by name, or a candidate record activated on approval), so
    the optimizer's plan can name them."""

    employee_id: uuid.UUID


class DetectedChange(CamelModel):
    """One change. Values: budget = cents, deadline = ISO date, duration = working days,
    capacity = hours per week (0 = leaves the project), scope = the requirement text,
    team = hours per week of who joins (`member`; null until the notes say) or leaves.
    `mode` is the policy the notes state for a new budget or deadline ("must hold" = hard)."""

    id: str
    kind: ChangeKind
    action: ChangeAction | None = None
    member: StoredMember | None = None
    target: uuid.UUID | None = None
    target_label: str | None = None
    from_: int | float | str | None = Field(default=None, alias="from")
    to: int | float | str | None = None
    quote: str | None = None
    speaker: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    note: str | None = None
    candidates: list[TargetOption] = Field(default_factory=list)
    mode: ChangeMode | None = None


class QuestionOption(CamelModel):
    """One answer; `value` is "keep", "new", "none", an amount in cents, an ISO date or an id."""

    value: str
    label: str


class ChangeQuestion(CamelModel):
    """A question the change raises; Re-plan stays locked until every one is answered."""

    id: str
    kind: QuestionKind
    change_id: str
    tier: Literal["high", "blocking"]
    text: str
    why: str | None = None
    options: list[QuestionOption]


class AffectedTask(CamelModel):
    """A work package the change reaches."""

    id: uuid.UUID
    code: str
    name: str


class AffectedPerson(CamelModel):
    """Someone whose work or hours the change reaches."""

    id: uuid.UUID
    name: str


class ChangeAffects(CamelModel):
    """What the change touches. cost/completion are the in-force plan's, the baseline
    the change is measured against."""

    tasks: list[AffectedTask] = Field(default_factory=list)
    people: list[AffectedPerson] = Field(default_factory=list)
    cost_cents: int | None = None
    completion_date: date | None = None


class ChangeImpact(CamelModel):
    """Stored as `change_requests.impact`: what changed, the questions, what it touches."""

    changes: list[DetectedChange]
    questions: list[ChangeQuestion]
    affects: ChangeAffects
    # "rules" when the deterministic fallback read the notes, else the Claude model id.
    model: str


class PlanOverrides(CamelModel):
    """Body shape of `overrides` on POST /projects/{id}/optimization/runs (C1)."""

    budget_total_cents: int | None = Field(default=None, exclude_if=_omit_none)
    must_finish_by: date | None = Field(default=None, exclude_if=_omit_none)
    duration_days: dict[str, int] | None = Field(default=None, exclude_if=_omit_none)
    hours_per_week: dict[str, float] | None = Field(default=None, exclude_if=_omit_none)
    add_members: list[AddedMember] | None = Field(default=None, exclude_if=_omit_none)
    remove_members: list[uuid.UUID] | None = Field(default=None, exclude_if=_omit_none)
    # The policy the notes state with the new value (omitted = the project's own policy).
    budget_mode: ChangeMode | None = Field(default=None, exclude_if=_omit_none)
    deadline_mode: ChangeMode | None = Field(default=None, exclude_if=_omit_none)

    @property
    def empty(self) -> bool:
        return not (
            self.budget_total_cents is not None
            or self.must_finish_by is not None
            or self.budget_mode is not None
            or self.deadline_mode is not None
            or self.duration_days
            or self.hours_per_week
            or self.add_members
            or self.remove_members
        )


class ChangeRequest(CamelModel):
    """A change as the API returns it, with the derived re-plan overrides."""

    id: uuid.UUID
    project_id: uuid.UUID
    source: ChangeSource
    input_text: str | None
    status: ChangeStatus
    impact: ChangeImpact | None
    answers: dict[str, str]
    base_run_id: uuid.UUID | None
    base_version: int | None
    proposed_run_id: uuid.UUID | None
    proposed_run_status: SolverStatus | None
    error: str | None
    created_at: datetime
    decided_at: datetime | None
    # Set once every question is answered: the body for the re-plan run.
    overrides: PlanOverrides | None


# --------------------------------------------------------------------------- inputs
Days = Annotated[int, Field(ge=0, le=3650)]
Hours = Annotated[float, Field(ge=0, le=80)]
Percent = Annotated[int, Field(ge=0, le=100)]


class ChangeEdits(InputModel):
    """Direct edits (and progress) are structured: nothing is parsed."""

    budget_total_cents: int | None = Field(default=None, ge=0, le=10**13)
    must_finish_by: date | None = None
    duration_days: dict[uuid.UUID, Days] | None = Field(default=None, max_length=MAX_EDITS)
    hours_per_week: dict[uuid.UUID, Hours] | None = Field(default=None, max_length=MAX_EDITS)
    # Percent complete per work package (100 = done): the rest becomes the remaining duration.
    progress: dict[uuid.UUID, Percent] | None = Field(default=None, max_length=MAX_EDITS)
    # People joining / leaving the project (employee ids of current team members).
    add_members: list[NewMember] | None = Field(default=None, max_length=MAX_NEW_MEMBERS)
    remove_members: list[uuid.UUID] | None = Field(default=None, max_length=MAX_EDITS)


class ChangeCreate(InputModel):
    """POST body: `text` for notes, `edits` for the Edit inputs and Progress update tabs."""

    source: ChangeSource
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    edits: ChangeEdits | None = None


class ChangeAnswers(InputModel):
    """PATCH body: answers by question id, merged into the ones already given."""

    answers: dict[Annotated[str, Field(max_length=80)], Annotated[str, Field(max_length=80)]] = (
        Field(max_length=100)
    )


class ChangeApprove(InputModel):
    """Optional approve body: `runId` is a fallback for when C1's re-plan run was not linked
    to the change (C1 normally sets proposed_run_id itself)."""

    run_id: uuid.UUID | None = None


# --------------------------------------------------------------------------- compare
class PlanSide(CamelModel):
    """One plan of the comparison: KPIs and the budget / deadline it was solved under."""

    run_id: uuid.UUID
    version: int | None
    status: SolverStatus
    expected_cost_cents: int | None
    completion_date: date | None
    budget_cents: int | None
    must_finish_by: date | None


class Bar(CamelModel):
    """A work package's dates and owner in one plan."""

    start: date
    end: date
    owner: str | None


class CompareRow(CamelModel):
    """A work package in both plans; `before`/`after` is null when it is new / done."""

    wbs_node_id: uuid.UUID
    code: str
    name: str
    group_code: str | None
    group_name: str | None
    before: Bar | None
    after: Bar | None
    shift_days: int | None
    owner_changed: bool
    why: str | None


class ChangeComparison(CamelModel):
    """GET /changes/{id}/compare: Plan vN (base) against the proposed Plan vN+1."""

    base: PlanSide
    proposed: PlanSide
    rows: list[CompareRow]
