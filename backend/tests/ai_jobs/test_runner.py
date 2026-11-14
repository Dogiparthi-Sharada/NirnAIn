"""Tests of the AI job runner: WBS generation and validation with and without Claude.

Purpose: Prove the template and rules fallbacks run without a key, Claude's plan and review are used
when configured, failures end the job with a user-facing message, and stale jobs are recovered.
How it fits: Runs `AiJobRunner` (app/jobs/ai_jobs.py) on a fake store and a fake Claude client;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel

from app.ai.llm import Usage
from app.ai.wbs_generate import (
    NO_DELIVERABLES,
    GeneratedDeliverable,
    GeneratedPackage,
    GeneratedWbs,
    GenerationInput,
    ScopeEntry,
    TeamMember,
    WbsPlan,
)
from app.ai.wbs_review import REVIEW_UNAVAILABLE, ReviewIssue, WbsReview
from app.ai.wbs_validate import NodeInfo, ValidationInput
from app.core.config import Settings
from app.domain.common import AiJobKind, ScopeCategory, ScopeStatus
from app.jobs.ai_jobs import (
    EMPTY_PLAN_ERROR,
    FAILED_ERROR,
    HAS_WBS_ERROR,
    RESTART_ERROR,
    AiJobRunner,
)
from app.repositories.ai_jobs import ClaimedJob


# --------------------------------------------------------------------------- runner
class FakeStore:
    """In-memory `AiJobStore` that hands out one claimed job and records what the runner writes."""

    def __init__(
        self, kind: AiJobKind, job_input: GenerationInput | ValidationInput | None
    ) -> None:
        """Offer one job of `kind` whose input is `job_input`."""
        self.claimed: ClaimedJob | None = ClaimedJob(uuid.uuid4(), kind)
        self.inp = job_input
        self.has_wbs = False
        self.written: list[tuple[WbsPlan, dict[str, uuid.UUID]]] = []
        self.finished: list[tuple[dict[str, Any], str, int | None, int | None]] = []
        self.failed: list[str] = []
        self.stale = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeStore]:
        """Act as the store factory: yield this store."""
        yield self

    async def claim(self, job_id: uuid.UUID) -> ClaimedJob | None:
        """Return the job once; later claims find nothing queued."""
        claimed, self.claimed = self.claimed, None
        return claimed

    async def generation_input(self, project_id: uuid.UUID) -> Any:
        """Return the generation input the test set."""
        return self.inp

    async def validation_input(self, project_id: uuid.UUID) -> Any:
        """Return the validation input the test set."""
        return self.inp

    async def write_plan(
        self, project_id: uuid.UUID, plan: WbsPlan, owners: dict[str, uuid.UUID]
    ) -> dict[str, int] | None:
        """Record the plan and owners; return counts, or None when the test says a WBS exists."""
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
        """Record the job's result, model and token usage."""
        self.finished.append((result, model, input_tokens, output_tokens))

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        """Record the job's error."""
        self.failed.append(error)

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        """Return the number of stale jobs the test set (checking the restart error)."""
        assert error == RESTART_ERROR
        return self.stale


class FakeLLM:
    """Fake Claude client: answers with a fixed model output or raises a fixed error."""

    def __init__(self, output: BaseModel | Exception | None = None) -> None:
        """Answer with `output`; None means no key is configured."""
        self.output = output
        self.model = "claude-sonnet-5-5"
        self.prompts: list[str] = []

    @property
    def configured(self) -> bool:
        """Tell whether an output (or error) was given, i.e. whether a key is "set"."""
        return self.output is not None

    def parse(self, *, system: str, prompt: str, output: type[Any], **_: Any) -> tuple[Any, Usage]:
        """Record the prompt and return the fixed output, or raise the fixed error."""
        self.prompts.append(prompt)
        if isinstance(self.output, Exception):
            raise self.output
        assert isinstance(self.output, output)
        return self.output, Usage(10, 5)


SALESFORCE = TeamMember(uuid.uuid4(), "Ana", None, {"Salesforce": 4})


def gen_input(*scope: ScopeEntry) -> GenerationInput:
    """Return a generation input for project CRM with the given scope and Ana (Salesforce 4)."""
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
    """Return a deliverable scope item with the given text and status."""
    return ScopeEntry(uuid.uuid4(), ScopeCategory.DELIVERABLE, text, status)


async def _run(store: FakeStore, llm: FakeLLM | None = None) -> AiJobRunner:
    """Run one job through a fresh runner and wait for it to finish."""
    runner = AiJobRunner(store, Settings(_env_file=None), llm=llm or FakeLLM())  # type: ignore[arg-type, call-arg]
    await runner.submit(uuid.uuid4())
    await asyncio.wait(list(runner._tasks))
    return runner


async def test_generate_without_a_key_uses_the_template() -> None:
    """Without a key, generation writes the template plan (design, build, test)."""
    store = FakeStore(AiJobKind.WBS_GENERATE, gen_input(deliverable("Data warehouse")))
    await _run(store)
    ((plan, owners),) = store.written
    assert [package.name for package in plan.packages] == [
        "Design & planning",
        "Build",
        "Test & acceptance",
    ]
    assert [package.duration_days for package in plan.packages] == [10, 20, 10]
    assert owners == {}  # template packages carry no skills
    ((result, model, tokens_in, _),) = store.finished
    assert model == "template"
    assert result == {"deliverables": 1, "workPackages": 3, "dependencies": 2, "ownersSuggested": 0}
    assert tokens_in is None


async def test_generate_without_deliverables_fails_with_guidance() -> None:
    """Without deliverable scope the job fails with guidance and writes nothing."""
    store = FakeStore(
        AiJobKind.WBS_GENERATE,
        gen_input(ScopeEntry(uuid.uuid4(), ScopeCategory.RISK, "x", ScopeStatus.ACCEPTED)),
    )
    await _run(store)
    assert store.failed == [NO_DELIVERABLES]
    assert store.written == []


async def test_generate_with_claude_writes_its_plan_and_suggests_owners() -> None:
    """Claude's plan is written with its scope links; the skilled member is suggested as owner."""
    generated = GeneratedWbs(
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
    llm = FakeLLM(generated)
    await _run(store, llm)
    ((plan, owners),) = store.written
    assert plan.deliverables[0].scope_item_id == item.id
    assert owners == {plan.packages[0].key: SALESFORCE.id}
    ((result, model, tokens_in, tokens_out),) = store.finished
    assert (model, tokens_in, tokens_out) == ("claude-sonnet-5-5", 10, 5)
    assert result["ownersSuggested"] == 1
    assert "S1 [deliverable] CRM" in llm.prompts[0]


async def test_generate_failures_never_leak() -> None:
    """A Claude crash fails the job with the generic message; an empty plan with its own message."""
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
    """Return a valid three-level WBS (root, deliverable, one work package) to validate."""
    root = NodeInfo(uuid.uuid4(), None, 1, "1", "CRM")
    deliverable = NodeInfo(uuid.uuid4(), root.id, 2, "1.1", "Setup")
    package = NodeInfo(uuid.uuid4(), deliverable.id, 3, "1.1.1", "Build", 10, uuid.uuid4())
    return ValidationInput("CRM", (root, deliverable, package))


async def test_validate_without_a_key_runs_only_the_rules() -> None:
    """Without a key, validation runs only the built-in checks and records model 'rules'."""
    store = FakeStore(AiJobKind.WBS_VALIDATE, _validation_input())
    await _run(store)
    ((result, model, _, _),) = store.finished
    assert model == "rules"
    assert result == {"structureValid": True, "levels": 3, "issues": []}


async def test_validate_with_claude_adds_its_issues_or_says_it_did_not_run() -> None:
    """Claude's review issues are added as 'ai'; a failed review adds a "couldn't run" warning."""
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
    """Stale jobs are failed at start-up; without a database recovery and shutdown do nothing."""
    store = FakeStore(AiJobKind.WBS_VALIDATE, None)
    store.stale = 3
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert await AiJobRunner(store, settings).recover_stale(600) == 3  # type: ignore[arg-type]
    idle = AiJobRunner(None, settings)
    assert await idle.recover_stale(600) == 0
    await idle.submit(uuid.uuid4())
    assert idle.active == 0
    await idle.shutdown()
