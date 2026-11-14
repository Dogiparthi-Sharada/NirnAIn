"""Moved test support."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.common import OrgRole
from app.domain.team import ProjectSkill, TeamMember
from app.repositories.access import ProjectRef
from tests.support.store import Store


@dataclass
class TeamStore:
    employees: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    skills: dict[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)  # org, name
    held: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)  # employee, skill
    team: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = field(default_factory=dict)
    project_skills: dict[tuple[uuid.UUID, uuid.UUID], bool] = field(default_factory=dict)
    wbs_skills: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)  # project
    booked: dict[uuid.UUID, float] = field(default_factory=dict)
    weeks: list[Sequence[date]] = field(default_factory=list)
    contractor_rates: dict[uuid.UUID, int] = field(default_factory=dict)  # confirmed, by project


class FakeTeam:
    def __init__(self, store: Store, t: TeamStore, user_id: uuid.UUID) -> None:
        self.s, self.t, self.uid = store, t, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project(self, project_id: uuid.UUID) -> ProjectRef | None:
        p = self.s.projects.get(project_id)
        if p is None or self._role(project_id) is None:
            return None
        return ProjectRef(p.id, p.org_id, p.name, p.start_date, p.target_end_date, p.budget_cents)

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def window(self, project: ProjectRef) -> tuple[date | None, date | None]:
        return project.start_date, project.target_end_date

    def _skill_names(self, employee_id: uuid.UUID) -> list[str]:
        return sorted(self.t.skills[s][1] for e, s in self.t.held if e == employee_id)

    async def members(self, project: ProjectRef, weeks: Sequence[date]) -> list[TeamMember]:
        self.t.weeks.append(weeks)
        return [
            TeamMember(
                employee_id=employee_id,
                full_name=self.t.employees[employee_id]["full_name"],
                role_title=row["role_title"],
                hours_per_week=row["hours_per_week"],
                worker_type=(
                    "contractor"
                    if self.t.employees[employee_id].get("is_contractor")
                    else "employee"
                ),
                hourly_rate_cents=self.t.employees[employee_id].get("hourly_rate_cents"),
                booked_hours=self.t.booked.get(employee_id, 0),
                skills=self._skill_names(employee_id),
            )
            for (project_id, employee_id), row in self.t.team.items()
            if project_id == project.id
        ]

    async def member_names(self, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
        return {e: self.t.employees[e]["full_name"] for p, e in self.t.team if p == project_id}

    async def find_employee(self, org_id: uuid.UUID, full_name: str) -> uuid.UUID | None:
        return next(
            (
                i
                for i, e in self.t.employees.items()
                if e["org_id"] == org_id and e["full_name"].lower() == full_name.lower()
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
        employee_id = uuid.uuid4()
        self.t.employees[employee_id] = {
            "org_id": org_id,
            "full_name": full_name,
            "role_title": role_title,
            "weekly_capacity_hours": hours_per_week,
            "is_contractor": is_contractor,
            "hourly_rate_cents": hourly_rate_cents,
        }
        return employee_id

    async def employee_rate(self, employee_id: uuid.UUID) -> int | None:
        rate: int | None = self.t.employees[employee_id].get("hourly_rate_cents")
        return rate

    async def set_worker_type(self, employee_id: uuid.UUID, is_contractor: bool) -> None:
        self.t.employees[employee_id]["is_contractor"] = is_contractor

    async def confirmed_contractor_rate(self, project_id: uuid.UUID) -> int | None:
        return self.t.contractor_rates.get(project_id)

    async def set_hourly_rate(self, employee_id: uuid.UUID, hourly_rate_cents: int | None) -> None:
        self.t.employees[employee_id]["hourly_rate_cents"] = hourly_rate_cents

    async def rename_employee(self, employee_id: uuid.UUID, full_name: str) -> None:
        self.t.employees[employee_id]["full_name"] = full_name

    async def add_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, role_title: str, hours_per_week: int
    ) -> None:
        self.t.team[(project_id, employee_id)] = {
            "role_title": role_title,
            "hours_per_week": hours_per_week,
        }

    async def update_member(
        self, project_id: uuid.UUID, employee_id: uuid.UUID, changes: dict[str, Any]
    ) -> bool:
        self.t.team[(project_id, employee_id)].update(changes)
        return True

    async def remove_member(self, project_id: uuid.UUID, employee_id: uuid.UUID) -> bool:
        return self.t.team.pop((project_id, employee_id), None) is not None

    async def ensure_skills(self, org_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
        out = []
        for name in names:
            found = await self.org_skill(org_id, name)
            if found is None:
                found = uuid.uuid4()
                self.t.skills[found] = (org_id, name)
            out.append(found)
        return out

    async def add_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        self.t.held |= {(employee_id, s) for s in skill_ids}

    async def set_employee_skills(self, employee_id: uuid.UUID, skill_ids: list[uuid.UUID]) -> None:
        self.t.held = {(e, s) for e, s in self.t.held if e != employee_id}
        await self.add_employee_skills(employee_id, skill_ids)

    def _rows(self, project_id: uuid.UUID) -> list[tuple[uuid.UUID, bool]]:
        rows = [(s, r) for (p, s), r in self.t.project_skills.items() if p == project_id]
        return rows or [(s, True) for s in self.t.wbs_skills.get(project_id, [])]

    async def skills(self, project_id: uuid.UUID) -> list[ProjectSkill]:
        team = [e for p, e in self.t.team if p == project_id]
        return [
            ProjectSkill(
                skill_id=s,
                name=self.t.skills[s][1],
                required=r,
                holders=[self.t.employees[e]["full_name"] for e in team if (e, s) in self.t.held],
            )
            for s, r in self._rows(project_id)
        ]

    async def materialize_skills(self, project_id: uuid.UUID) -> None:
        for s, r in self._rows(project_id):
            self.t.project_skills[(project_id, s)] = r

    async def org_skill(self, org_id: uuid.UUID, name: str) -> uuid.UUID | None:
        return next(
            (i for i, (o, n) in self.t.skills.items() if o == org_id and n.lower() == name.lower()),
            None,
        )

    async def add_project_skill(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> None:
        self.t.project_skills[(project_id, skill_id)] = required

    async def set_required(
        self, project_id: uuid.UUID, skill_id: uuid.UUID, required: bool
    ) -> bool:
        self.t.project_skills[(project_id, skill_id)] = required
        return True

    async def rename_skill(self, skill_id: uuid.UUID, name: str) -> None:
        self.t.skills[skill_id] = (self.t.skills[skill_id][0], name)

    async def replace_skill(
        self, project_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID, required: bool
    ) -> None:
        del self.t.project_skills[(project_id, old_id)]
        self.t.project_skills[(project_id, new_id)] = required
        for p, e in list(self.t.team):
            if p == project_id and (e, old_id) in self.t.held:
                self.t.held.add((e, new_id))

    async def remove_skill(self, project_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        return self.t.project_skills.pop((project_id, skill_id), None) is not None
