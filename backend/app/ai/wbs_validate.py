"""WBS validation: deterministic structure checks (always) plus an optional
Claude review. Pure functions; the worker and `GET …/wbs/checks` do the I/O.

The deterministic checks (`check_structure`) are free and run live on every
WBS change; only the Claude review costs tokens. Rules they apply:

* Deadline (round-2 decision D4): planning starts today, so when the start
  date has passed (or isn't set) only the working days from today to the
  deadline count, as in the optimizer.
* Step 4 doesn't warn about step 5 (the team comes after the WBS): owners are
  optional (the optimizer assigns people; applying a plan writes the owners),
  so a missing owner is no issue. Once the project has a team, an owner who
  isn't on it is an info line.
* Skills: without a project team, one info line names the skills the WBS
  needs (they are matched to people on step 5). With a team, each skill
  nobody on it has is a warning. Info lines are never counted as issues.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.prompting import DATA_RULE, as_data, data_block, one_line
from app.ai.wbs_generate import ScopeEntry, select_scope
from app.domain.common import DependencyType

ReviewIssueType = Literal["missing", "duplicate", "warning"]
IssueType = Literal["missing", "duplicate", "warning", "info"]
IssueSource = Literal["rules", "ai"]

MAX_LISTED = 3
MAX_SKILLS_NAMED = 8
MAX_REVIEW_ISSUES = 6
ISSUE_LIMIT = 240
EMPTY_WBS = "The WBS is empty. Generate it or add deliverables first."
REVIEW_UNAVAILABLE = "The AI review couldn't run this time; only the built-in checks ran."


@dataclass(frozen=True, slots=True)
class NodeInfo:
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
        return f"{self.code} {self.name}"


@dataclass(frozen=True, slots=True)
class DependencyInfo:
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
    type: DependencyType = DependencyType.FS
    lag_days: int = 0


@dataclass(frozen=True, slots=True)
class ValidationInput:
    project_name: str
    nodes: tuple[NodeInfo, ...]
    dependencies: tuple[DependencyInfo, ...] = ()
    start_date: date | None = None
    deadline: date | None = None
    working_days_per_week: int = 5
    required_skills: tuple[str, ...] = ()
    held_skills: frozenset[str] = frozenset()  # casefolded skill names the project team has
    team_ids: frozenset[uuid.UUID] = frozenset()  # the project team (step 5); empty until set
    scope: tuple[ScopeEntry, ...] = ()
    today: date | None = None

    @property
    def has_team(self) -> bool:
        return bool(self.team_ids)


@dataclass(frozen=True, slots=True)
class Issue:
    type: IssueType
    text: str

    def as_json(self) -> dict[str, str]:
        return {"type": self.type, "text": self.text}


# --------------------------------------------------------------------------- deterministic
def _norm(name: str) -> str:
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
    duration = {p.id: p.duration_days or 0 for p in packages}
    incoming: dict[uuid.UUID, list[DependencyInfo]] = defaultdict(list)
    outgoing: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    indegree = dict.fromkeys(duration, 0)
    for dep in dependencies:
        if dep.predecessor_id in duration and dep.successor_id in duration:
            incoming[dep.successor_id].append(dep)
            outgoing[dep.predecessor_id].append(dep.successor_id)
            indegree[dep.successor_id] += 1
    ready = [node for node, deg in indegree.items() if deg == 0]
    start: dict[uuid.UUID, int] = {}
    while ready:
        node = ready.pop()
        es = 0
        for dep in incoming[node]:
            p_start, p_dur, dur = (
                start[dep.predecessor_id],
                duration[dep.predecessor_id],
                duration[node],
            )
            bound = {
                DependencyType.FS: p_start + p_dur + dep.lag_days,
                DependencyType.SS: p_start + dep.lag_days,
                DependencyType.FF: p_start + p_dur + dep.lag_days - dur,
                DependencyType.SF: p_start + dep.lag_days - dur,
            }[dep.type]
            es = max(es, bound)
        start[node] = es
        for succ in outgoing[node]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
    if len(start) != len(duration):
        return None
    return max((start[n] + duration[n] for n in duration), default=0)


def planning_start(inp: ValidationInput) -> date | None:
    """First day work can still happen: the start date, or today once it has passed (D4)."""
    days = [day for day in (inp.start_date, inp.today) if day is not None]
    return max(days, default=None)


def _deadline_issue(inp: ValidationInput, packages: Sequence[NodeInfo]) -> Issue | None:
    """The critical path against the working days left between the planning start and the
    deadline (None when it fits, or when the dates or the packages aren't there yet)."""
    first = planning_start(inp)
    if first is None or inp.deadline is None or not packages:
        return None
    length = critical_path_days(packages, inp.dependencies)
    window = working_days(first, inp.deadline, inp.working_days_per_week)
    if length is None or length <= window:
        return None
    if inp.deadline < first:
        return Issue(
            "warning",
            f"The critical path takes {length} working days, but the deadline "
            f"{inp.deadline} has already passed.",
        )
    return Issue(
        "warning",
        f"The critical path takes {length} working days, but only {window} are "
        f"available between {first} and {inp.deadline}.",
    )


def _skill_issues(inp: ValidationInput) -> list[Issue]:
    """Without a team, one info line naming the skills the WBS needs (matched on step 5);
    with one, a warning per skill nobody on the project team has."""
    needed: dict[str, str] = {}
    for skill in inp.required_skills:
        needed.setdefault(_norm(skill), skill)
    if not inp.has_team:
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
    return [
        Issue(
            "warning",
            f"Nobody on the project team has {skill}: add someone on step 5 or allow a specialist.",
        )
        for key, skill in needed.items()
        if key not in inp.held_skills
    ]


def check_structure(inp: ValidationInput) -> list[Issue]:
    """Checks that need no AI: tree shape, names, durations and the deadline; owners and
    skills only as far as step 4 can tell before the team is set (see the module rules)."""
    if not inp.nodes:
        return [Issue("missing", EMPTY_WBS)]
    issues: list[Issue] = []
    children: dict[uuid.UUID | None, list[NodeInfo]] = defaultdict(list)
    for node in inp.nodes:
        children[node.parent_id].append(node)

    for root in children[None]:
        if not children[root.id]:
            issues.append(Issue("missing", f"{root.label} has no deliverables."))
    for node in inp.nodes:
        if node.level == 2 and not children[node.id]:
            issues.append(Issue("missing", f"{node.label} has no work packages."))

    for parent_id, siblings in children.items():
        by_name: dict[str, list[NodeInfo]] = defaultdict(list)
        for node in siblings:
            by_name[_norm(node.name)].append(node)
        parent = next((n for n in inp.nodes if n.id == parent_id), None)
        where = f" under {parent.label}" if parent else ""
        for same in by_name.values():
            if len(same) > 1:
                codes = ", ".join(n.code for n in same)
                issues.append(
                    Issue("duplicate", f'Duplicate name "{same[0].name}"{where} ({codes}).')
                )

    packages = [n for n in inp.nodes if n.level == 3]
    issues += _listed(
        [p for p in packages if p.duration_days is None], "has no duration.", "have no duration"
    )
    if inp.has_team:
        issues += _listed(
            [
                package
                for package in packages
                if package.owner_employee_id is not None
                and package.owner_employee_id not in inp.team_ids
            ],
            "is owned by someone who isn't on the project team.",
            "are owned by people who aren't on the project team",
            "info",
        )

    deadline = _deadline_issue(inp, packages)
    if deadline is not None:
        issues.append(deadline)
    return issues + _skill_issues(inp)


def validation_result(inp: ValidationInput, extra: Sequence[Issue] = ()) -> dict[str, Any]:
    """The validate job's result: the built-in checks, then the review's other issues.

    Each issue carries its `source` ('rules' or 'ai'), so the builder can show the rules
    live from `GET …/wbs/checks` and keep only the review's own items from the job.
    """
    rules = check_structure(inp)
    known = {_norm(issue.text) for issue in rules}
    review = [issue for issue in extra if _norm(issue.text) not in known]
    roots = [node for node in inp.nodes if node.parent_id is None]
    valid = len(roots) == 1 and not any(
        issue.type in ("missing", "duplicate") for issue in rules + review
    )
    tagged: list[tuple[Issue, IssueSource]] = [(issue, "rules") for issue in rules]
    tagged += [(issue, "ai") for issue in review]
    return {
        "structureValid": valid,
        "levels": max((node.level for node in inp.nodes), default=0),
        "issues": [{**issue.as_json(), "source": source} for issue, source in tagged],
    }


# --------------------------------------------------------------------------- Claude
class ReviewIssue(BaseModel):
    type: ReviewIssueType
    text: str = Field(description="One concise sentence, at most 160 characters")


class WbsReview(BaseModel):
    issues: list[ReviewIssue]


VALIDATE_SYSTEM = (
    "You are a senior project planner reviewing a work breakdown structure (WBS). "
    + DATA_RULE
    + " Report at most 6 concise issues a project manager should fix before scheduling: "
    "missing work (e.g. testing/UAT, data migration, disaster recovery/backup, training, "
    "deployment, handover), work packages that overlap or duplicate each other (type "
    "duplicate), and accepted scope items the WBS does not cover (type missing). Use type "
    "warning for anything else. Refer to WBS items by their code. Don't repeat structural "
    "problems such as missing durations, don't comment on owners, staffing or skills (people "
    "are matched to the work on a later step), and don't invent facts the data doesn't "
    "support. Return an empty list when the WBS is sound."
)


def build_validation_prompt(inp: ValidationInput) -> str:
    lines = [f"Project: {as_data(one_line(inp.project_name, 200))}", "WBS:"]
    for node in inp.nodes:
        detail = f" ({node.duration_days} days)" if node.duration_days is not None else ""
        indent = "  " * (node.level - 1)
        lines.append(f"{indent}{node.code} {as_data(one_line(node.name, 200))}{detail}")
    scope = select_scope(inp.scope)
    if scope:
        lines.append("Accepted scope items:")
        lines.extend(f"- [{s.category}] {as_data(one_line(s.text, 400))}" for s in scope[:80])
    return data_block("\n".join(lines)) + "\n\nReview the WBS."


def review_issues(review: WbsReview) -> list[Issue]:
    out: list[Issue] = []
    for issue in review.issues[:MAX_REVIEW_ISSUES]:
        text = one_line(issue.text, ISSUE_LIMIT)
        if text:
            out.append(Issue(issue.type, text))
    return out
