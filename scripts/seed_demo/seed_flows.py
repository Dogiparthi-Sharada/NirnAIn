"""Steps 1-5 of the demo seed for one project, and the per-project orchestration.

Purpose: Build every demo project, whatever its stage, the way a project manager builds it in
the app (create → intake → scope review → WBS → team, skills, dependencies, limits), through
the endpoints the screens call, so seeded data looks like data entered by hand. Intake, WBS
validation and step-7 notes are the only endpoints that can call Claude: with
`ai_calls='rules'` they are used and each answer is checked to come from the built-in rules;
with `ai_calls='skip'` they are never called (scope items are typed in from the same notes,
validation is skipped and changes are direct edits).
How it fits: `seed_demo.py` makes one `Seeder` for the demo workspace and calls `seed(spec)`
for each `ProjectSpec`; applied projects continue in `seed_planning.py` (steps 6-7);
layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from seed_client import ApiClient, SeedError, wait_until
from seed_model import ProjectSpec
from seed_planning import RULES_MODEL, optimize_and_apply, record_and_approve
from seed_resources import add_team, build_wbs, link, set_limits, set_skills, validate_wbs

AI_RULES = RULES_MODEL
AI_SKIP = "skip"
HEADING_CATEGORIES = (
    ("objective", re.compile(r"objectives?|goals?", re.I)),
    ("requirement", re.compile(r"requirements?", re.I)),
    ("deliverable", re.compile(r"deliverables?", re.I)),
    ("constraint", re.compile(r"constraints?|budget|timeline", re.I)),
    ("risk", re.compile(r"risks?|concerns?", re.I)),
    ("missing_info", re.compile(r"open items?|unknowns?|questions?", re.I)),
)
BULLET = re.compile(r"^\s*[•\-*]\s+(?P<text>\S.*?)\s*$")


@dataclass
class SeededProject:
    """One project in the summary."""

    name: str
    project_id: str
    code: str
    stage: str
    versions: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    kept: bool = False


def bullets_by_heading(notes: str) -> list[tuple[str, str]]:
    """(category, text) for each bullet under a known heading: the scope items typed in by hand."""
    items: list[tuple[str, str]] = []
    category: str | None = None
    for line in notes.splitlines():
        bullet = BULLET.match(line)
        if bullet and category:
            items.append((category, bullet.group("text")))
        elif line.strip() and not bullet and line.strip().upper() == line.strip():
            category = next(
                (name for name, pattern in HEADING_CATEGORIES if pattern.search(line)), None
            )
    return items


class Seeder:
    """Seeds `ProjectSpec`s into one workspace as its signed-in project manager."""

    def __init__(
        self,
        api: ApiClient,
        org_id: str,
        pm_user_id: str,
        ai_calls: str,
        say: Callable[[str], None],
    ) -> None:
        """Seeds into `org_id` as `pm_user_id`; `say` prints progress lines."""
        self.api = api
        self.org_id = org_id
        self.pm_user_id = pm_user_id
        self.ai_calls = ai_calls
        self.say = say

    # ------------------------------------------------------------------ lookup and reset
    def find(self, name: str) -> list[dict[str, Any]]:
        """Projects in the workspace with exactly this name."""
        page = self.api.expect(
            "GET",
            f"/orgs/{self.org_id}/projects",
            query={"q": name, "limit": "100"},
            step="List projects",
        )
        return [project for project in page.get("items") or [] if project.get("name") == name]

    def delete(self, project: dict[str, Any]) -> None:
        """Deletes one existing demo project (used by --reset)."""
        self.api.expect(
            "DELETE", f"/projects/{project['id']}", step=f"Delete '{project['name']}'", ok=(204,)
        )

    def versions(self, project_id: str) -> list[int]:
        """Applied plan versions, oldest first."""
        listed = (
            self.api.expect("GET", f"/projects/{project_id}/plan-versions", step="Plan versions")
            or []
        )
        return sorted(int(version["version"]) for version in listed)

    # ------------------------------------------------------------------ steps 1-3
    def create(self, spec: ProjectSpec) -> dict[str, Any]:
        """Step 1: creates the project (a draft stays on the create step)."""
        draft = spec.stage == "draft"
        body = {
            "name": spec.name,
            "description": spec.description,
            "type": spec.project_type,
            "department": spec.department,
            "sponsor": spec.sponsor,
            "pmUserId": self.pm_user_id,
            "priority": spec.priority,
            "methodology": spec.methodology,
            "visibility": "internal",
            "startDate": spec.start.isoformat(),
            "targetEndDate": spec.due.isoformat(),
            "budgetCents": spec.budget_cents,
            "currency": "USD",
            "status": "draft" if draft else "planning",
            "currentStep": "create" if draft else "intake",
        }
        step = f"Step 1 create '{spec.name}'"
        created: dict[str, Any] = self.api.expect(
            "POST", f"/orgs/{self.org_id}/projects", body, step=step
        )
        return created

    def intake(self, project_id: str, notes: str) -> int:
        """Step 2: one intake run on the notes (rules), or the same bullets typed in (skip)."""
        if self.ai_calls == AI_SKIP:
            for category, text in bullets_by_heading(notes):
                self.api.expect(
                    "POST",
                    f"/projects/{project_id}/scope-items",
                    {"category": category, "text": text},
                    step="Step 3 add scope item",
                )
            return len(bullets_by_heading(notes))
        started = self.api.expect(
            "POST", f"/projects/{project_id}/intake/runs", {"text": notes}, step="Step 2 intake"
        )
        path = f"/projects/{project_id}/intake/runs/{started['id']}"
        run = wait_until(
            lambda: self.api.expect("GET", path, step="Step 2 intake"),
            lambda latest: latest.get("status") in {"succeeded", "failed", "cancelled"},
            step="Step 2 intake",
        )
        if run.get("model") != RULES_MODEL:
            raise SeedError(
                f"Step 2 intake ran with '{run.get('model')}', not the rules: "
                "this API has an AI key. "
                "Stop and seed with --ai-calls skip, or against an API without ANTHROPIC_API_KEY."
            )
        if run.get("status") != "succeeded":
            raise SeedError(f"Step 2 intake {run.get('status')}: {run.get('error')}")
        return int(run.get("itemsExtracted") or 0)

    def review_scope(self, project_id: str, spec: ProjectSpec) -> tuple[int, int]:
        """Step 3: accept every item except the ones the spec removes; add the PM's own risks."""
        removed = 0
        items = (
            self.api.expect("GET", f"/projects/{project_id}/scope-items", step="Step 3 scope") or []
        )
        for item in items:
            drop = (item["category"], item["text"]) in spec.remove_scope
            removed += drop
            self.api.expect(
                "PATCH",
                f"/scope-items/{item['id']}",
                {"status": "removed" if drop else "accepted"},
                step="Step 3 review item",
            )
        for risk in spec.manual_risks:
            self.api.expect(
                "POST",
                f"/projects/{project_id}/scope-items",
                {"category": "risk", "text": risk},
                step="Step 3 add risk",
            )
        return len(items) - removed + len(spec.manual_risks), removed

    # ------------------------------------------------------------------ one project
    def seed(self, spec: ProjectSpec) -> SeededProject:
        """Builds one project up to its stage."""
        project = self.create(spec)
        seeded = SeededProject(spec.name, str(project["id"]), str(project["code"]), spec.stage)
        pid, say = seeded.project_id, self.say
        say(f"  {spec.name} ({seeded.code}): created as {project['status']}")
        if spec.stage == "draft":
            return seeded
        if spec.intake_notes:
            say(f"    intake: {self.intake(pid, spec.intake_notes)} scope items found")
        kept, removed = self.review_scope(pid, spec)
        say(f"    scope: {kept} accepted, {removed} removed")
        team_ids = add_team(self.api, pid, spec)
        node_ids = build_wbs(self.api, pid, spec, team_ids)
        set_skills(self.api, pid, spec, node_ids)
        link(self.api, pid, spec, node_ids)
        say(
            f"    WBS: {len(spec.deliverables)} deliverables, {len(spec.packages)} work packages, "
            f"{len(spec.links)} dependencies; team of {len(spec.team)}; {len(spec.skills)} skills"
        )
        if spec.validate_wbs and self.ai_calls == AI_RULES:
            validate_wbs(self.api, pid)
        if spec.limits:
            set_limits(self.api, pid, spec, node_ids)
        if spec.stage == "planning":
            return seeded
        optimize_and_apply(self.api, pid, say)
        if spec.status_after_apply or spec.progress_pct:
            update = {"status": spec.status_after_apply, "progressPct": spec.progress_pct}
            self.api.expect(
                "PATCH",
                f"/projects/{pid}",
                {key: value for key, value in update.items() if value},
                step="Status and progress",
            )
        for change in spec.changes:
            decision = record_and_approve(self.api, pid, change, self.ai_calls, team_ids, say)
            seeded.notes.append(f"{change.label}: {decision}")
        seeded.versions = self.versions(pid)
        say(f"    plan versions: {', '.join(f'v{version}' for version in seeded.versions)}")
        return seeded
