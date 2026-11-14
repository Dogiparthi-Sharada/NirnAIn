"""AI jobs: endpoints (fake repo), the background runner (fake store / LLM) and
the WBS write SQL (recording connection). No database, no real LLM."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.ai.llm import Usage
from app.ai.wbs_generate import (
    NO_DELIVERABLES,
    GeneratedDeliverable,
    GeneratedPackage,
    GeneratedWbs,
    GenerationInput,
    PlannedDeliverable,
    PlannedDependency,
    PlannedPackage,
    ScopeEntry,
    TeamMember,
    WbsPlan,
)
from app.ai.wbs_review import REVIEW_UNAVAILABLE, ReviewIssue, WbsReview
from app.ai.wbs_validate import NodeInfo, ValidationInput
from app.api import deps
from app.core.config import Settings
from app.core.security import CurrentPrincipal
from app.domain.ai import AiJob
from app.domain.common import AiJobKind, JobStatus, OrgRole, ScopeCategory, ScopeStatus
from app.jobs.ai_jobs import (
    EMPTY_PLAN_ERROR,
    FAILED_ERROR,
    HAS_WBS_ERROR,
    RESTART_ERROR,
    AiJobRunner,
)
from app.repositories.ai_jobs import ActiveJob, ActiveJobExistsError, AiJobStore, ClaimedJob
from tests.conftest import Store
from tests.test_optimization_repo import Rows
from tests.test_repositories import Result

Auth = Callable[[uuid.UUID], dict[str, str]]
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- API
@dataclass
class JobData:
    jobs: dict[uuid.UUID, AiJob] = field(default_factory=dict)
    has_wbs: bool = False
    active: ActiveJob | None = None
    race: bool = False


class FakeJobs:
    def __init__(self, store: Store, data: JobData, user_id: uuid.UUID) -> None:
        self.s, self.d, self.uid = store, data, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def has_wbs(self, project_id: uuid.UUID) -> bool:
        return self.d.has_wbs

    async def active_job(
        self, project_id: uuid.UUID, kind: AiJobKind, stale_after_s: int
    ) -> ActiveJob | None:
        return self.d.active

    async def create(self, project_id: uuid.UUID, kind: AiJobKind) -> uuid.UUID:
        if self.d.race:
            raise ActiveJobExistsError
        job = AiJob(
            id=uuid.uuid4(),
            project_id=project_id,
            kind=kind,
            status=JobStatus.QUEUED,
            error=None,
            result=None,
            created_at=NOW,
            started_at=None,
            finished_at=None,
        )
        self.d.jobs[job.id] = job
        return job.id

    async def get(self, project_id: uuid.UUID, job_id: uuid.UUID) -> AiJob | None:
        return self.d.jobs.get(job_id)

    async def latest(self, project_id: uuid.UUID, kind: AiJobKind) -> AiJob | None:
        rows = [
            j
            for j in self.d.jobs.values()
            if j.project_id == project_id and j.kind == kind and self._role(project_id)
        ]
        return rows[-1] if rows else None


class FakeRunner:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []
        self.failed: list[tuple[uuid.UUID, str]] = []

    async def submit(self, job_id: uuid.UUID) -> None:
        self.submitted.append(job_id)

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        self.failed.append((job_id, error))


@pytest.fixture
def job_data() -> JobData:
    return JobData()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def jobs_client(
    app: FastAPI, store: Store, job_data: JobData, runner: FakeRunner
) -> Iterator[TestClient]:
    def jobs(principal: CurrentPrincipal) -> FakeJobs:
        return FakeJobs(store, job_data, principal.user_id)

    app.dependency_overrides[deps.get_ai_job_repo] = jobs
    app.dependency_overrides[deps.get_ai_runner] = lambda: runner
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _project(client: TestClient, store: Store, auth: Auth) -> tuple[uuid.UUID, uuid.UUID, str]:
    user = store.add_user("Jane Doe")
    org = store.add_org(user, "Acme")
    r = client.post(f"/api/v1/orgs/{org}/projects", json={"name": "CRM"}, headers=auth(user))
    return user, org, r.json()["id"]


def test_generate_and_validate_queue_a_job(
    jobs_client: TestClient, store: Store, auth: Auth, runner: FakeRunner
) -> None:
    user, _, pid = _project(jobs_client, store, auth)
    for kind, path in (("wbs_generate", "generate"), ("wbs_validate", "validate")):
        r = jobs_client.post(f"/api/v1/projects/{pid}/wbs/{path}", json={}, headers=auth(user))
        assert r.status_code == 202, r.text
        body = r.json()
        assert (body["kind"], body["status"], body["projectId"]) == (kind, "queued", pid)
        assert body["id"] == str(runner.submitted[-1])
        latest = jobs_client.get(
            f"/api/v1/projects/{pid}/ai-jobs/latest?kind={kind}", headers=auth(user)
        )
        assert latest.json()["id"] == body["id"]


def test_generate_conflicts(
    jobs_client: TestClient, store: Store, auth: Auth, job_data: JobData, runner: FakeRunner
) -> None:
    user, _, pid = _project(jobs_client, store, auth)
    url = f"/api/v1/projects/{pid}/wbs/generate"
    job_data.has_wbs = True
    r = jobs_client.post(url, headers=auth(user))
    assert r.status_code == 409
    assert r.json()["detail"] == HAS_WBS_ERROR
    # Validation doesn't care whether a WBS exists.
    assert (
        jobs_client.post(f"/api/v1/projects/{pid}/wbs/validate", headers=auth(user)).status_code
        == 202
    )

    job_data.has_wbs = False
    job_data.active = ActiveJob(id=uuid.uuid4(), stale=False)
    r = jobs_client.post(url, headers=auth(user))
    assert r.status_code == 409
    assert r.json()["detail"] == "WBS generation is already running for this project."

    stale = uuid.uuid4()
    job_data.active = ActiveJob(id=stale, stale=True)
    assert jobs_client.post(url, headers=auth(user)).status_code == 202
    assert runner.failed == [(stale, RESTART_ERROR)]

    job_data.active, job_data.race = None, True
    assert jobs_client.post(url, headers=auth(user)).status_code == 409


def test_ai_job_permissions_and_missing(jobs_client: TestClient, store: Store, auth: Auth) -> None:
    user, org, pid = _project(jobs_client, store, auth)
    viewer = store.add_user("Vic Viewer")
    store.members[(org, viewer)] = OrgRole.VIEWER
    stranger = store.add_user("Eve")
    url = f"/api/v1/projects/{pid}/wbs/generate"
    assert jobs_client.post(url, headers=auth(viewer)).status_code == 403
    assert jobs_client.post(url, headers=auth(stranger)).status_code == 404
    latest = f"/api/v1/projects/{pid}/ai-jobs/latest"
    assert jobs_client.get(f"{latest}?kind=wbs_validate", headers=auth(user)).status_code == 404
    assert jobs_client.get(f"{latest}?kind=nope", headers=auth(user)).status_code == 422
    assert jobs_client.get(latest, headers=auth(user)).status_code == 422


# --------------------------------------------------------------------------- runner
class FakeStore:
    def __init__(self, kind: AiJobKind, inp: GenerationInput | ValidationInput | None) -> None:
        self.claimed: ClaimedJob | None = ClaimedJob(uuid.uuid4(), kind)
        self.inp = inp
        self.has_wbs = False
        self.written: list[tuple[WbsPlan, dict[str, uuid.UUID]]] = []
        self.finished: list[tuple[dict[str, Any], str, int | None, int | None]] = []
        self.failed: list[str] = []
        self.stale = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeStore]:
        yield self

    async def claim(self, job_id: uuid.UUID) -> ClaimedJob | None:
        claimed, self.claimed = self.claimed, None
        return claimed

    async def generation_input(self, project_id: uuid.UUID) -> Any:
        return self.inp

    async def validation_input(self, project_id: uuid.UUID) -> Any:
        return self.inp

    async def write_plan(
        self, project_id: uuid.UUID, plan: WbsPlan, owners: dict[str, uuid.UUID]
    ) -> dict[str, int] | None:
        if self.has_wbs:
            return None
        self.written.append((plan, owners))
        return {
            "deliverables": len(plan.deliverables),
            "workPackages": len(plan.packages),
            "dependencies": len(plan.dependencies),
        }

    async def finish(
        self,
        job_id: uuid.UUID,
        result: dict[str, Any],
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.finished.append((result, model, input_tokens, output_tokens))

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        self.failed.append(error)

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        assert error == RESTART_ERROR
        return self.stale


class FakeLLM:
    def __init__(self, output: BaseModel | Exception | None = None) -> None:
        self.output = output
        self.model = "claude-sonnet-5-5"
        self.prompts: list[str] = []

    @property
    def configured(self) -> bool:
        return self.output is not None

    def parse(self, *, system: str, prompt: str, output: type[Any], **_: Any) -> tuple[Any, Usage]:
        self.prompts.append(prompt)
        if isinstance(self.output, Exception):
            raise self.output
        assert isinstance(self.output, output)
        return self.output, Usage(10, 5)


SALESFORCE = TeamMember(uuid.uuid4(), "Ana", None, {"Salesforce": 4})


def gen_input(*scope: ScopeEntry) -> GenerationInput:
    return GenerationInput(
        project_name="CRM",
        description=None,
        project_type="technology",
        methodology="waterfall",
        start_date=None,
        target_end_date=None,
        scope=scope,
        org_skills=("Salesforce",),
        team=(SALESFORCE,),
    )


def deliverable(text: str, status: ScopeStatus = ScopeStatus.ACCEPTED) -> ScopeEntry:
    return ScopeEntry(uuid.uuid4(), ScopeCategory.DELIVERABLE, text, status)


async def _run(store: FakeStore, llm: FakeLLM | None = None) -> AiJobRunner:
    runner = AiJobRunner(store, Settings(_env_file=None), llm=llm or FakeLLM())  # type: ignore[arg-type, call-arg]
    await runner.submit(uuid.uuid4())
    await asyncio.wait(list(runner._tasks))
    return runner


async def test_generate_without_a_key_uses_the_template() -> None:
    store = FakeStore(AiJobKind.WBS_GENERATE, gen_input(deliverable("Data warehouse")))
    await _run(store)
    ((plan, owners),) = store.written
    assert [p.name for p in plan.packages] == ["Design & planning", "Build", "Test & acceptance"]
    assert [p.duration_days for p in plan.packages] == [10, 20, 10]
    assert owners == {}  # template packages carry no skills
    ((result, model, tokens_in, _),) = store.finished
    assert model == "template"
    assert result == {"deliverables": 1, "workPackages": 3, "dependencies": 2, "ownersSuggested": 0}
    assert tokens_in is None


async def test_generate_without_deliverables_fails_with_guidance() -> None:
    store = FakeStore(
        AiJobKind.WBS_GENERATE,
        gen_input(ScopeEntry(uuid.uuid4(), ScopeCategory.RISK, "x", ScopeStatus.ACCEPTED)),
    )
    await _run(store)
    assert store.failed == [NO_DELIVERABLES]
    assert store.written == []


async def test_generate_with_claude_writes_its_plan_and_suggests_owners() -> None:
    gen = GeneratedWbs(
        deliverables=[
            GeneratedDeliverable(
                name="CRM setup",
                description="",
                scope_ref="S1",
                work_packages=[
                    GeneratedPackage(
                        key="a",
                        name="Configure Salesforce",
                        description="",
                        duration_weeks=2,
                        skills=["Salesforce"],
                        scope_ref=None,
                    )
                ],
            )
        ],
        dependencies=[],
    )
    item = deliverable("CRM")
    store = FakeStore(AiJobKind.WBS_GENERATE, gen_input(item))
    llm = FakeLLM(gen)
    await _run(store, llm)
    ((plan, owners),) = store.written
    assert plan.deliverables[0].scope_item_id == item.id
    assert owners == {plan.packages[0].key: SALESFORCE.id}
    ((result, model, tokens_in, tokens_out),) = store.finished
    assert (model, tokens_in, tokens_out) == ("claude-sonnet-5-5", 10, 5)
    assert result["ownersSuggested"] == 1
    assert "S1 [deliverable] CRM" in llm.prompts[0]


async def test_generate_failures_never_leak() -> None:
    store = FakeStore(AiJobKind.WBS_GENERATE, gen_input(deliverable("CRM")))
    await _run(store, FakeLLM(RuntimeError("secret internals")))
    assert store.failed == [FAILED_ERROR]

    store = FakeStore(AiJobKind.WBS_GENERATE, gen_input(deliverable("CRM")))
    await _run(store, FakeLLM(GeneratedWbs(deliverables=[], dependencies=[])))
    assert store.failed == [EMPTY_PLAN_ERROR]

    store = FakeStore(AiJobKind.WBS_GENERATE, gen_input(deliverable("CRM")))
    store.has_wbs = True
    await _run(store)
    assert store.failed == [HAS_WBS_ERROR]


def _validation_input() -> ValidationInput:
    root = NodeInfo(uuid.uuid4(), None, 1, "1", "CRM")
    d = NodeInfo(uuid.uuid4(), root.id, 2, "1.1", "Setup")
    p = NodeInfo(uuid.uuid4(), d.id, 3, "1.1.1", "Build", 10, uuid.uuid4())
    return ValidationInput("CRM", (root, d, p))


async def test_validate_without_a_key_runs_only_the_rules() -> None:
    store = FakeStore(AiJobKind.WBS_VALIDATE, _validation_input())
    await _run(store)
    ((result, model, _, _),) = store.finished
    assert model == "rules"
    assert result == {"structureValid": True, "levels": 3, "issues": []}


async def test_validate_with_claude_adds_its_issues_or_says_it_did_not_run() -> None:
    review = WbsReview(issues=[ReviewIssue(type="missing", text="No UAT work package.")])
    store = FakeStore(AiJobKind.WBS_VALIDATE, _validation_input())
    await _run(store, FakeLLM(review))
    ((result, model, _, _),) = store.finished
    assert model == "claude-sonnet-5-5"
    assert result["issues"] == [{"type": "missing", "text": "No UAT work package.", "source": "ai"}]
    assert result["structureValid"] is False

    store = FakeStore(AiJobKind.WBS_VALIDATE, _validation_input())
    await _run(store, FakeLLM(RuntimeError("secret internals")))
    ((result, model, _, _),) = store.finished
    assert model == "rules"
    assert result["issues"] == [{"type": "warning", "text": REVIEW_UNAVAILABLE, "source": "ai"}]


async def test_recover_stale_and_no_database() -> None:
    store = FakeStore(AiJobKind.WBS_VALIDATE, None)
    store.stale = 3
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert await AiJobRunner(store, settings).recover_stale(600) == 3  # type: ignore[arg-type]
    idle = AiJobRunner(None, settings)
    assert await idle.recover_stale(600) == 0
    await idle.submit(uuid.uuid4())
    assert idle.active == 0
    await idle.shutdown()


# --------------------------------------------------------------------------- WBS write SQL
class PlanConn:
    """Recording connection that answers write_plan's reads and fails one link."""

    def __init__(self, org_id: uuid.UUID, reject_link: int | None = None) -> None:
        self.org_id = org_id
        self.sql: list[str] = []
        self.params: list[Any] = []
        self.skills: dict[str, uuid.UUID] = {"salesforce": uuid.uuid4()}
        self.reject_link = reject_link
        self.links = 0
        self.savepoints = 0

    async def execute(self, stmt: Any, params: Any = None) -> Result:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.sql.append(sql)
        self.params.append(params if params is not None else dict(compiled.params))
        if sql.startswith("SELECT public.projects.org_id"):
            return Rows([{"org_id": self.org_id, "name": "CRM"}])
        if sql.startswith("SELECT public.skills.id"):
            return Rows([{"id": i, "name": n} for n, i in self.skills.items()])
        if sql.startswith("INSERT INTO public.skills"):
            for key, value in compiled.params.items():
                if key.startswith("name"):
                    self.skills[str(value).casefold()] = uuid.uuid4()
        if sql.startswith("INSERT INTO public.dependencies"):
            self.links += 1
            if self.links == self.reject_link:
                raise DBAPIError("INSERT", {}, Exception("cycle"))
        return Rows([])

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        self.savepoints += 1
        yield


def _plan() -> WbsPlan:
    def pkg(key: str, skills: tuple[str, ...]) -> PlannedPackage:
        return PlannedPackage(key, f"Package {key}", None, 10, skills, None)

    a, b, c = pkg("A", ("Salesforce", "QA")), pkg("B", ("qa",)), pkg("C", ())
    return WbsPlan(
        (PlannedDeliverable("Setup", None, None, (a, b, c)),),
        (PlannedDependency("A", "B"), PlannedDependency("B", "C"), PlannedDependency("A", "Z")),
    )


async def test_write_plan_writes_tree_skills_and_links() -> None:
    owner = uuid.uuid4()
    conn = PlanConn(uuid.uuid4(), reject_link=2)
    counts = await AiJobStore(conn).write_plan(uuid.uuid4(), _plan(), {"A": owner})  # type: ignore[arg-type]
    assert counts == {"deliverables": 1, "workPackages": 3, "dependencies": 1}
    inserts = [s.split(" (")[0] for s in conn.sql if s.startswith("INSERT")]
    assert inserts == [
        "INSERT INTO public.wbs_nodes",
        "INSERT INTO public.wbs_nodes",
        "INSERT INTO public.wbs_nodes",
        "INSERT INTO public.skills",
        "INSERT INTO public.task_skill_requirements",
        "INSERT INTO public.project_skills",
        "INSERT INTO public.dependencies",
        "INSERT INTO public.dependencies",
    ]
    root, level2, level3 = (
        p
        for s, p in zip(conn.sql, conn.params, strict=True)
        if s.startswith("INSERT INTO public.wbs_nodes")
    )
    assert [r["level"] for r in root + level2 + level3] == [1, 2, 3, 3, 3]
    assert root[0]["name"] == "CRM"
    assert all(r["origin"] == "ai" for r in root + level2 + level3)
    assert "estimate_confirmed_at" not in level3[0]
    assert [r["owner_employee_id"] for r in level3] == [owner, None, None]
    skill_sql = next(s for s in conn.sql if s.startswith("INSERT INTO public.skills"))
    assert "ON CONFLICT DO NOTHING" in skill_sql
    reqs = next(
        p
        for s, p in zip(conn.sql, conn.params, strict=True)
        if s.startswith("INSERT INTO public.task_skill")
    )
    assert len(reqs) == 3  # A: Salesforce + QA, B: QA (case-insensitive)
    assert {r["min_proficiency"] for r in reqs} == {2}
    upsert = next(s for s in conn.sql if s.startswith("INSERT INTO public.project_skills"))
    assert "ON CONFLICT (project_id, skill_id) DO UPDATE SET required" in upsert
    assert conn.savepoints == 2  # the dangling A -> Z link is skipped before the DB


async def test_write_plan_skips_when_a_wbs_exists() -> None:
    class Existing(PlanConn):
        async def execute(self, stmt: Any, params: Any = None) -> Result:
            result = await super().execute(stmt, params)
            if self.sql[-1].startswith("SELECT public.wbs_nodes.id"):
                return Rows([{"id": uuid.uuid4()}])
            return result

    conn = Existing(uuid.uuid4())
    assert await AiJobStore(conn).write_plan(uuid.uuid4(), _plan(), {}) is None  # type: ignore[arg-type]
    assert not any(s.startswith("INSERT") for s in conn.sql)
