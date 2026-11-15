"""Fake team repository for the team endpoint tests.

Purpose: Keep employees, team memberships, skills and rates in memory, so the team rules (D1 rates,
reuse by name, required skills) can be tested without a database.
How it fits: The team tests install `FakeTeam` in place of `TeamRepository`; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.team import ProjectSkill, TeamMember
from app.repositories.access import ProjectRef
from tests.support.store import ProjectScopedFake, Store


@dataclass
class TeamStore:
    """What the fake team tables hold (employees, skills, memberships, bookings, rates)."""

    employees: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    skills: dict[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)  # org, name
    held: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)  # employee, skill
    team: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = field(default_factory=dict)
    project_skills: dict[tuple[uuid.UUID, uuid.UUID], bool] = field(default_factory=dict)
    wbs_skills: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)  # project
    booked: dict[uuid.UUID, float] = field(default_factory=dict)
    weeks: list[Sequence[date]] = field(default_factory=list)
    contractor_rates: dict[uuid.UUID, int] = field(default_factory=dict)  # confirmed, by project


class FakeTeam(ProjectScopedFake):
    """Stand-in for `TeamRepository` over the team data."""

    def __init__(self, store: Store, team_data: TeamStore, user_id: uuid.UUID) -> None:
        """Act as the given user over the store and the team data."""
        super().__init__(store, user_id)
        self.team_data = team_data

    async def window(self, project: ProjectRef) -> tuple[date | None, date | None]:
        """Return the project's own dates as the planning window."""
        return project.start_date, project.target_end_date

    def _skill_names(self, employee_id: uuid.UUID) -> list[str]:
        """Return a person's skill names, alphabetically."""
        return sorted(
            self.team_data.skills[skill_id][1]
            for holder, skill_id in self.team_data.held
            if holder == employee_id
        )

    async def members(self, project: ProjectRef, weeks: Sequence[date]) -> list[TeamMember]:
        """Record the weeks asked for and return the project's team with skills and bookings."""
        self.team_data.weeks.append(weeks)
        return [
            TeamMember(
                employee_id=employee_id,
                full_name=self.team_data.employees[employee_id]["full_name"],
                role_title=row["role_title"],
                hours_per_week=row["hours_per_week"],
                worker_type=(
                    "contractor"
                    if self.team_data.employees[employee_id].get("is_contractor")
                    else "employee"
                ),
                hourly_rate_cents=self.team_data.employees[employee_id].get("hourly_rate_cents"),
                booked_hours=self.team_data.booked.get(employee_id, 0),
                skills=self._skill_names(employee_id),
            )
            for (project_id, employee_id), row in self.team_data.team.items()
            if project_id == project.id
        ]

    async def member_names(self, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
        """Return the full names of the project's team members."""
        return {
            employee_id: self.team_data.employees[employee_id]["full_name"]
            for member_project, employee_id in self.team_data.team
            if member_project == project_id
        }

    async def find_employee(self, org_id: uuid.UUID, full_name: str) -> uuid.UUID | None:
        """Return the organization's employee with this name (case-insensitive), or None."""
        return next(
            (
                employee_id
                for employee_id, employee in self.team_data.employees.items()
                if employee["org_id"] == org_id
                and employee["full_name"].lower() == full_name.lower()
            ),
            None,
        )

    async def create_employee(
        self,
        org_id: uuid.UUID,
        full_name: str,
        role_title: str,
        hours_per_week: int,
        *,
        is_contractor: bool = False,
        hourly_rate_cents: int | None = None,
    ) -> uuid.UUID:
        """Add an employee record and return its id."""
        employee_id = uuid.uuid4()
        self.team_data.employees[employee_id] = {
            "org_id": org_id,
            "full_name": full_name,
            "role_title": role_title,
            "weekly_capacity_hours": hours_per_week,
            "is_contractor": is_contractor,
            "hourly_rate_cents": hourly_rate_cents,
        }
        return employee_id

    async def employee_rate(self, employee_id: uuid.UUID) -> int | None:
        """Return the rate on the employee's record."""
        rate: int | None = self.team_data.employees[employee_id].get("hourly_rate_cents")
        return rate

    async def set_worker_type(self, employee_id: uuid.UUID, is_contractor: bool) -> None:
        """Mark the employee a contractor or not."""
        self.team_data.employees[employee_id]["is_contractor"] = is_contractor

    async def confirmed_contractor_rate(self, project_id: uuid.UUID) -> int | None:
        """Return the project's confirmed contractor rate the test set."""
        return self.team_data.contractor_rates.get(project_id)

    async def set_hourly_rate(self, employee_id: uuid.UUID, hourly_rate_cents: int | None) -> None:
        """Set the rate on the employee's record."""
        self.team_data.employees[employee_id]["hourly_rate_cents"] = hourly_rate_cents

    async def rename_employee(self, employee_id: uuid.UUID, full_name: str) -> None:
        """Rename the employee's record."""
        self.team_data.employees[employee_id]["full_name"] = full_name

    async def add_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, role_title: str, hours_per_week: int
    ) -> None:
        """Put the employee on the project's team."""
        self.team_data.team[(project_id, employee_id)] = {
            "role_title": role_title,
            "hours_per_week": hours_per_week,
        }

    async def update_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, changes: dict[str, Any]
    ) -> bool:
        """Change a team member's role or hours."""
        self.team_data.team[(project_id, employee_id)].update(changes)
        return True

    async def remove_member(self, project_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        """Take the employee off the team; False when they were not on it."""
        return self.team_data.team.pop((project_id, employee_id), None) is not None

    async def ensure_skills(self, org_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
        """Return skill ids for the names, creating the missing organization skills."""
        out = []
        for name in names:
            found = await self.org_skill(org_id, name)
            if found is None:
                found = uuid.uuid4()
                self.team_data.skills[found] = (org_id, name)
            out.append(found)
        return out

    async def add_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        """Give the person these skills."""
        self.team_data.held |= {(employee_id, skill_id) for skill_id in skill_ids}

    async def set_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        """Replace the person's skills."""
        self.team_data.held = {
            (holder, skill_id) for holder, skill_id in self.team_data.held if holder != employee_id
        }
        await self.add_employee_skills(employee_id, skill_ids)

    def _rows(self, project_id: uuid.UUID) -> list[tuple[uuid.UUID, bool]]:
        """Return the project's skill list, or the WBS's skills (all required) until edited."""
        rows = [
            (skill_id, required)
            for (skill_project, skill_id), required in self.team_data.project_skills.items()
            if skill_project == project_id
        ]
        return rows or [
            (skill_id, True) for skill_id in self.team_data.wbs_skills.get(project_id, [])
        ]

    async def skills(self, project_id: uuid.UUID) -> list[ProjectSkill]:
        """Return the project's skills with the team members who hold each."""
        team = [
            member for member_project, member in self.team_data.team if member_project == project_id
        ]
        return [
            ProjectSkill(
                skill_id=skill_id,
                name=self.team_data.skills[skill_id][1],
                required=required,
                holders=[
                    self.team_data.employees[member]["full_name"]
                    for member in team
                    if (member, skill_id) in self.team_data.held
                ],
            )
            for skill_id, required in self._rows(project_id)
        ]

    async def materialize_skills(self, project_id: uuid.UUID) -> None:
        """Store the derived skill list as the project's own."""
        for skill_id, required in self._rows(project_id):
            self.team_data.project_skills[(project_id, skill_id)] = required

    async def org_skill(self, org_id: uuid.UUID, name: str) -> uuid.UUID | None:
        """Return the organization's skill with this name (case-insensitive), or None."""
        return next(
            (
                skill_id
                for skill_id, (skill_org, skill_name) in self.team_data.skills.items()
                if skill_org == org_id and skill_name.lower() == name.lower()
            ),
            None,
        )

    async def add_project_skill(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> None:
        """Add a skill to the project's list."""
        self.team_data.project_skills[(project_id, skill_id)] = required

    async def set_required(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> bool:
        """Mark a project skill required or optional."""
        self.team_data.project_skills[(project_id, skill_id)] = required
        return True

    async def rename_skill(self, skill_id: uuid.UUID, name: str) -> None:
        """Rename an organization skill."""
        self.team_data.skills[skill_id] = (self.team_data.skills[skill_id][0], name)

    async def replace_skill(
        self, project_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID, required: bool
    ) -> None:
        """Swap a project skill for another; team members holding the old one get the new one."""
        del self.team_data.project_skills[(project_id, old_id)]
        self.team_data.project_skills[(project_id, new_id)] = required
        for member_project, member in list(self.team_data.team):
            if member_project == project_id and (member, old_id) in self.team_data.held:
                self.team_data.held.add((member, new_id))

    async def remove_skill(self, project_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        """Remove a skill from the project's list; False when it was not on it."""
        return self.team_data.project_skills.pop((project_id, skill_id), None) is not None
