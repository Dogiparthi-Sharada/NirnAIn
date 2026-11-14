"""People joining or leaving the project mid-way (step 7, change kind "team").

What: turns the "Add person" / "Remove" edits into changes (with the 422 rules), maps the
answered changes to the re-plan's `addMembers` / `removeMembers`, and says which reserved
employees a decision leaves unused.
Why: the PM must be able to bring in a specialist or contractor, or take someone off the
project, after Plan v1 is applied; the team inputs are locked then, so it goes through step 7.
How it fits: pure functions used by `app.changes.derive` (edits, overrides), the rules and
Claude readers (`addition`) and the API (which candidate employees to release); the hours
question a hire from notes raises lives with the other questions (`app.changes.questions`).
The API reserves an organization employee for every newcomer before the impact is built
(`target`), so the optimizer's plan can name them; approval puts them on the team.
"""

from __future__ import annotations

import uuid

from app.changes.context import ChangeContext
from app.changes.questions import KEEP
from app.domain.changes import (
    AddedMember,
    ChangeEdits,
    ChangeImpact,
    DetectedChange,
    NewMember,
    PlanOverrides,
    StoredMember,
)

# A newcomer from meeting notes works full time unless the notes or the PM's answer say less.
DEFAULT_NEW_MEMBER_HOURS = 40


class TeamEditError(ValueError):
    """An add / remove edit that can't apply to this team; the message is user-facing."""


def is_addition(change: DetectedChange) -> bool:
    """A newcomer: kind "team", action "add", with the person's details."""
    return change.kind == "team" and change.action == "add" and change.member is not None


def is_removal(change: DetectedChange) -> bool:
    """Someone leaving the team: kind "team", action "remove", targeting the employee."""
    return change.kind == "team" and change.action == "remove" and change.target is not None


def addition(member: NewMember, hours: float | None, **fields: object) -> dict[str, object]:
    """Fields of an add-member change. `to` is the hours the source states (None = ask)."""
    return {
        "kind": "team",
        "action": "add",
        "target_label": member.full_name,
        "to": hours,
        "member": StoredMember.model_validate(member.model_dump(by_alias=False)),
        **fields,
    }


def priced(member: NewMember, context: ChangeContext) -> NewMember:
    """The newcomer with the rate rule applied (round 2, D1).

    An employee is costed from their company record, so a rate given here is refused; a
    contractor needs an hourly rate, defaulting to the project's contractor rate.
    """
    if member.worker_type == "employee":
        if member.hourly_rate_cents is not None:
            raise TeamEditError("Employee rates come from the company record.")
        return member
    if member.hourly_rate_cents is not None:
        return member
    if context.contractor_rate_cents is None:
        raise TeamEditError(f"Enter the contractor's hourly rate for {member.full_name}.")
    return member.model_copy(update={"hourly_rate_cents": context.contractor_rate_cents})


def team_edits(edits: ChangeEdits, context: ChangeContext) -> list[dict[str, object]]:
    """The add / remove edits as change fields, in that order.

    Business rules (422 via TeamEditError): a newcomer can't share a name with someone on
    the team or another newcomer (case-insensitive); rates follow `priced`; only current
    members can be removed; and the plan must keep at least one person.
    """
    on_team = {person.name.casefold() for person in context.people}
    seen: set[str] = set()
    found: list[dict[str, object]] = []
    for member in edits.add_members or []:
        name = member.full_name.casefold()
        if name in on_team:
            raise TeamEditError(f"{member.full_name} is already on the team.")
        if name in seen:
            raise TeamEditError(f"{member.full_name} is listed twice.")
        seen.add(name)
        found.append(addition(priced(member, context), member.hours_per_week))
    leaving = list(dict.fromkeys(edits.remove_members or []))
    for employee_id in leaving:
        person = context.person(employee_id)
        if person is None:
            raise TeamEditError("Someone you want to remove isn't on the team.")
        found.append(
            {
                "kind": "team",
                "action": "remove",
                "target": person.id,
                "target_label": person.name,
                "from_": person.hours_per_week,
                "to": 0,
            }
        )
    if leaving and len(context.people) - len(leaving) + len(seen) < 1:
        raise TeamEditError(
            "A plan needs at least one person. Add someone before you remove the last person."
        )
    return found


def member_overrides(
    changes: list[DetectedChange], answers: dict[str, str]
) -> tuple[list[AddedMember], list[uuid.UUID]]:
    """(addMembers, removeMembers) for the re-plan once the questions are answered.

    The answers give a notes newcomer's hours (`team:<id>`) and a contractor's rate
    (`rate:<id>`, cents). A newcomer the PM declined ("Don't bring them in") is left out;
    one without a reserved employee (never the case through the API) can't be planned and
    is left out as well.
    """
    added: list[AddedMember] = []
    removed: list[uuid.UUID] = []
    for change in changes:
        if is_removal(change) and change.target is not None:
            removed.append(change.target)
            continue
        if not is_addition(change) or change.member is None or change.target is None:
            continue
        hours = answers.get(f"team:{change.id}")
        rate = answers.get(f"rate:{change.id}")
        if KEEP in (hours, rate):
            continue
        updates: dict[str, int] = {}
        if hours is not None:
            updates["hours_per_week"] = int(hours)
        if rate is not None:
            updates["hourly_rate_cents"] = int(rate)
        member = change.member.model_copy(update=updates)
        added.append(AddedMember(employee_id=change.target, **member.model_dump(by_alias=False)))
    return added, removed


def reserved_employees(impact: ChangeImpact | None) -> list[uuid.UUID]:
    """Employees reserved for the change's newcomers (released when they don't join)."""
    if impact is None:
        return []
    return [change.target for change in impact.changes if is_addition(change) and change.target]


def unused_reservations(impact: ChangeImpact | None, overrides: PlanOverrides) -> list[uuid.UUID]:
    """Reserved employees the approved overrides don't add (declined newcomers)."""
    joining = {member.employee_id for member in overrides.add_members or []}
    return [employee_id for employee_id in reserved_employees(impact) if employee_id not in joining]
