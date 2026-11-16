"""WBS validation: the deterministic structure checks behind AI Validate and `GET …/wbs/checks`.

Purpose: Catch structural problems for free on every WBS change; only the Claude review
(wbs_review.py) costs tokens. Rules the checks apply:
* Deadline (round-2 D4): planning starts today, so once the start date has passed (or isn't
  set) only the working days from today to the deadline count, as in the optimizer. The warning
  says which date the window starts from and why ("Nov 15, 2026 (today; the start date Nov 1,
  2026 has passed)" or "Dec 1, 2026 (project start)"), with dates written as the UI shows them.
* Step 4 doesn't warn about step 5: owners are optional (the optimizer assigns people), so a
  missing owner is no issue; once there is a team, an owner who isn't on it is an info line.
* Skills: without a team one info line names the skills the WBS needs; with a team each skill
  nobody has is a warning, unless outside specialists are allowed (then an info line, naming
  the confirmed rate). Info lines are never counted as issues.
How it fits: Pure functions; the validate job (app/jobs/ai_jobs.py) and app/api/v1/wbs.py read
the input through app/repositories/wbs_checks.py; layer: ai.
Created: 2026-11-13 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from app.ai.wbs_generate import ScopeEntry
from app.domain.common import DependencyType
from app.optimization import formatting as fmt

IssueType = Literal["missing", "duplicate", "warning", "info"]
IssueSource = Literal["rules", "ai"]

MAX_LISTED = 3
MAX_SKILLS_NAMED = 8
EMPTY_WBS = "The WBS is empty. Generate it or add deliverables first."


@dataclass(frozen=True, slots=True)
class NodeInfo:
    """A WBS node as the checks read it."""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    code: str
    name: str
    duration_days: int | None = None
    owner_employee_id: uuid.UUID | None = None
    owner_name: str | None = None

    @property
    def label(self) -> str:
        """Return `code name`, as the screens show a node."""
        return f"{self.code} {self.name}"


@dataclass(frozen=True, slots=True)
class DependencyInfo:
    """A dependency link as the checks read it."""

    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    type: DependencyType = DependencyType.FS
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class ValidationInput:
    """Everything the checks (and the AI review) read about a project's WBS."""

    project_name: str
    nodes: tuple[NodeInfo, ...]
    dependencies: tuple[DependencyInfo, ...] = ()
    start_date: date | None = None
    deadline: date | None = None
    working_days_per_week: int = 5
    required_skills: tuple[str, ...] = ()
    held_skills: frozenset[str] = frozenset()  # casefolded skill names the project team has
    team_ids: frozenset[uuid.UUID] = frozenset()  # the project team (step 5); empty until set
    specialists_allowed: bool = False  # step 5 answered 'yes' to outside specialists
    specialist_rate_cents: int | None = None  # their hourly rate, once the PM confirmed it
    scope: tuple[ScopeEntry, ...] = ()
    today: date | None = None

    @property
    def has_team(self) -> bool:
        """Tell whether step 5 has set a project team."""
        return bool(self.team_ids)


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding: missing, duplicate, warning or info, with its text."""

    type: IssueType
    text: str

    def as_json(self) -> dict[str, str]:
        """Return the finding as stored in the job result."""
        return {"type": self.type, "text": self.text}


# --------------------------------------------------------------------------- deterministic
def _norm(name: str) -> str:
    """Return a name with single spaces, casefolded, for duplicate checks."""
    return " ".join(name.split()).casefold()


def _listed(
    nodes: Sequence[NodeInfo], one: str, many: str, kind: IssueType = "warning"
) -> list[Issue]:
    """One line per node, or a single summary once there are more than MAX_LISTED."""
    if len(nodes) <= MAX_LISTED:
        return [Issue(kind, f"{node.label} {one}") for node in nodes]
    codes = ", ".join(node.code for node in nodes[:MAX_LISTED])
    return [Issue(kind, f"{len(nodes)} work packages {many} ({codes}, …).")]


def working_days(start: date, end: date, per_week: int = 5) -> int:
    """Working days in [start, end], counting the first `per_week` weekdays (Mon=0)."""
    if end < start:
        return 0
    per_week = max(1, min(7, per_week))
    total = (end - start).days + 1
    weeks, rest = divmod(total, 7)
    count = weeks * per_week
    day = start + timedelta(days=weeks * 7)
    for _ in range(rest):
        if day.weekday() < per_week:
            count += 1
        day += timedelta(days=1)
    return count


def critical_path_days(
    packages: Sequence[NodeInfo], dependencies: Iterable[DependencyInfo]
) -> int | None:
    """Length of the longest chain of work packages (None when the links form a cycle)."""
    duration = {package.id: package.duration_days or 0 for package in packages}
    incoming: dict[uuid.UUID, list[DependencyInfo]] = defaultdict(list)
    outgoing: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    indegree = dict.fromkeys(duration, 0)
    for link in dependencies:
        if link.predecessor_id in duration and link.successor_id in duration:
            incoming[link.successor_id].append(link)
            outgoing[link.predecessor_id].append(link.successor_id)
            indegree[link.successor_id] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    start: dict[uuid.UUID, int] = {}
    while ready:
        node = ready.pop()
        earliest_start = 0
        for link in incoming[node]:
            predecessor_start, predecessor_days, days = (
                start[link.predecessor_id],
                duration[link.predecessor_id],
                duration[node],
            )
            bound = {
                DependencyType.FS: predecessor_start + predecessor_days + link.lag_days,
                DependencyType.SS: predecessor_start + link.lag_days,
                DependencyType.FF: predecessor_start + predecessor_days + link.lag_days - days,
                DependencyType.SF: predecessor_start + link.lag_days - days,
            }[link.type]
            earliest_start = max(earliest_start, bound)
        start[node] = earliest_start
        for successor in outgoing[node]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if len(start) != len(duration):
        return None
    return max((start[package_id] + duration[package_id] for package_id in duration), default=0)


def planning_start(checks_input: ValidationInput) -> date | None:
    """First day work can still happen: the start date, or today once it has passed (D4)."""
    days = [day for day in (checks_input.start_date, checks_input.today) if day is not None]
    return max(days, default=None)


def window_start_label(checks_input: ValidationInput, first: date) -> str:
    """'Dec 1, 2026 (project start)', or why the window starts today instead (D4)."""
    start = checks_input.start_date
    if start is not None and start >= first:
        return f"{fmt.day(first)} (project start)"
    if start is None:
        return f"{fmt.day(first)} (today; no start date is set)"
    return f"{fmt.day(first)} (today; the start date {fmt.day(start)} has passed)"


def _deadline_issue(checks_input: ValidationInput, packages: Sequence[NodeInfo]) -> Issue | None:
    """The critical path against the working days left between the planning start and the
    deadline (None when it fits, or when the dates or the packages aren't there yet)."""
    first = planning_start(checks_input)
    deadline = checks_input.deadline
    if first is None or deadline is None or not packages:
        return None
    length = critical_path_days(packages, checks_input.dependencies)
    window = working_days(first, deadline, checks_input.working_days_per_week)
    if length is None or length <= window:
        return None
    if deadline < first:
        return Issue(
            "warning",
            f"The critical path takes {length} working days, but the deadline "
            f"{fmt.day(deadline)} has already passed.",
        )
    return Issue(
        "warning",
        f"The critical path takes {length} working days, but only {window} are "
        f"available between {window_start_label(checks_input, first)} and {fmt.day(deadline)}.",
    )


def _skill_issues(checks_input: ValidationInput) -> list[Issue]:
    """Without a team, one info line naming the skills the WBS needs (matched on step 5);
    with one, a line per skill nobody on the project team has: a warning, or an info line
    when step 5 allows the specialist who will cover it."""
    needed: dict[str, str] = {}
    for skill in checks_input.required_skills:
        needed.setdefault(_norm(skill), skill)
    if not checks_input.has_team:
        if not needed:
            return []
        names = ", ".join(list(needed.values())[:MAX_SKILLS_NAMED])
        more = len(needed) - MAX_SKILLS_NAMED
        listed = f"{names} and {more} more" if more > 0 else names
        return [
            Issue(
                "info",
                f"Skills this WBS needs: {listed}. You'll match them to people on step 5.",
            )
        ]
    gaps = [skill for key, skill in needed.items() if key not in checks_input.held_skills]
    if checks_input.specialists_allowed:
        rate = checks_input.specialist_rate_cents
        priced = f" at ${rate / 100:,.0f}/h" if rate is not None else ""
        return [
            Issue(
                "info",
                f"{skill}: no one on the team has it; a specialist will be brought in{priced} "
                "(allowed on step 5).",
            )
            for skill in gaps
        ]
    return [
        Issue(
            "warning",
            f"Nobody on the project team has {skill}: add someone on step 5 or allow a specialist.",
        )
        for skill in gaps
    ]


def check_structure(checks_input: ValidationInput) -> list[Issue]:
    """Checks that need no AI: tree shape, names, durations and the deadline; owners and
    skills only as far as step 4 can tell before the team is set (see the module rules)."""
    if not checks_input.nodes:
        return [Issue("missing", EMPTY_WBS)]
    issues: list[Issue] = []
    children: dict[uuid.UUID | None, list[NodeInfo]] = defaultdict(list)
    for node in checks_input.nodes:
        children[node.parent_id].append(node)

    for root in children[None]:
        if not children[root.id]:
            issues.append(Issue("missing", f"{root.label} has no deliverables."))
    for node in checks_input.nodes:
        if node.level == 2 and not children[node.id]:
            issues.append(Issue("missing", f"{node.label} has no work packages."))

    for parent_id, siblings in children.items():
        by_name: dict[str, list[NodeInfo]] = defaultdict(list)
        for node in siblings:
            by_name[_norm(node.name)].append(node)
        parent = next(
            (wbs_node for wbs_node in checks_input.nodes if wbs_node.id == parent_id), None
        )
        where = f" under {parent.label}" if parent else ""
        for same in by_name.values():
            if len(same) > 1:
                codes = ", ".join(wbs_node.code for wbs_node in same)
                issues.append(
                    Issue("duplicate", f'Duplicate name "{same[0].name}"{where} ({codes}).')
                )

    packages = [wbs_node for wbs_node in checks_input.nodes if wbs_node.level == 3]
    issues += _listed(
        [work_package for work_package in packages if work_package.duration_days is None],
        "has no duration.",
        "have no duration",
    )
    if checks_input.has_team:
        issues += _listed(
            [
                package
                for package in packages
                if package.owner_employee_id is not None
                and package.owner_employee_id not in checks_input.team_ids
            ],
            "is owned by someone who isn't on the project team.",
            "are owned by people who aren't on the project team",
            "info",
        )

    deadline = _deadline_issue(checks_input, packages)
    if deadline is not None:
        issues.append(deadline)
    return issues + _skill_issues(checks_input)


def validation_result(checks_input: ValidationInput, extra: Sequence[Issue] = ()) -> dict[str, Any]:
    """The validate job's result: the built-in checks, then the review's other issues.

    Each issue carries its `source` ('rules' or 'ai'), so the builder can show the rules
    live from `GET …/wbs/checks` and keep only the review's own items from the job.
    """
    rules = check_structure(checks_input)
    known = {_norm(issue.text) for issue in rules}
    review = [issue for issue in extra if _norm(issue.text) not in known]
    roots = [node for node in checks_input.nodes if node.parent_id is None]
    valid = len(roots) == 1 and not any(
        issue.type in ("missing", "duplicate") for issue in rules + review
    )
    tagged: list[tuple[Issue, IssueSource]] = [(issue, "rules") for issue in rules]
    tagged += [(issue, "ai") for issue in review]
    return {
        "structureValid": valid,
        "levels": max((node.level for node in checks_input.nodes), default=0),
        "issues": [{**issue.as_json(), "source": source} for issue, source in tagged],
    }
