"""Background runner: claim -> extract off-loop -> write items; failures and shutdown."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from pydantic import ValidationError

from app.ai.evidence import NO_QUOTE_NOTE, ScopeSuggestion
from app.ai.intake import Source
from app.ai.intake_llm import ExtractedItem, ExtractionOutput
from app.ai.llm import LLM, Usage
from app.core.config import Settings
from app.domain.common import Evidence, ScopeCategory
from app.jobs.intake import (
    EMPTY_ERROR,
    FAILED_ERROR,
    RESTART_ERROR,
    IntakeRunner,
    engine_store,
)
from app.repositories.intake_jobs import ClaimedRun

Finished = tuple[uuid.UUID, uuid.UUID, str, list[ScopeSuggestion], int, int]


class FakeJobStore:
    def __init__(self, text: str = "Some notes", uploads: tuple[Source, ...] = ()) -> None:
        sources = [Source(None, "Pasted notes", text), *uploads]
        self.claimed: ClaimedRun | None = ClaimedRun(uuid.uuid4(), sources)
        self.finished: list[Finished] = []
        self.failed: list[tuple[uuid.UUID, str]] = []
        self.stale = 0
        self.broken = False

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeJobStore]:
        if self.broken:
            raise RuntimeError("db down")
        yield self

    async def claim(self, run_id: uuid.UUID) -> ClaimedRun | None:
        claimed, self.claimed = self.claimed, None  # claim once
        return claimed

    async def finish(
        self,
        run_id: uuid.UUID,
        project_id: uuid.UUID,
        model: str,
        suggestions: list[ScopeSuggestion],
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.finished.append((run_id, project_id, model, suggestions, input_tokens, output_tokens))

    async def fail(self, run_id: uuid.UUID, error: str) -> None:
        self.failed.append((run_id, error))

    async def fail_stale(self, older_than_s: int, error: str) -> int:
        assert error == RESTART_ERROR
        return self.stale


def settings(with_key: bool = True) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, anthropic_api_key="sk-test-key" if with_key else None
    )


async def _drain(runner: IntakeRunner) -> None:
    tasks = list(runner._tasks)
    if tasks:
        await asyncio.wait(tasks)


def _item(
    category: str, text: str, quote: str | None = None, evidence: Evidence = Evidence.STATED
) -> ExtractedItem:
    return ExtractedItem(
        category=category,
        text=text,
        confidence=0.9,
        source_quote=quote,
        evidence=evidence,
        evidence_note=None,
        suggested_text=None,
    )


def _fake_extract(result: ExtractionOutput, tokens: tuple[int, int] = (100, 50)) -> Any:
    def extract(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        return result, Usage(*tokens)

    return extract


async def test_run_extracts_and_writes_items(monkeypatch: pytest.MonkeyPatch) -> None:
    result = ExtractionOutput(
        items=[
            _item("objective", "Ship the thing", quote="ship  THE thing"),
            _item("risk", "Vendor may slip", quote="not in the notes"),
            _item("not_a_category", "ignored"),
            _item("risk", "  "),  # blank, dropped
        ]
    )
    monkeypatch.setattr(IntakeRunner, "_extract", _fake_extract(result))
    store = FakeJobStore(text="We must Ship the thing soon.")
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    assert runner.active == 1
    await _drain(runner)
    ((finished_run_id, _project_id, model, suggestions, input_tokens, output_tokens),) = (
        store.finished
    )
    assert finished_run_id == run_id
    assert model == "claude-opus-5-5"
    assert [(item.text, item.source_quote, item.evidence) for item in suggestions] == [
        ("Ship the thing", "Ship the thing", Evidence.STATED),
        ("Vendor may slip", None, Evidence.INFERRED),
    ]
    assert suggestions[1].evidence_note == NO_QUOTE_NOTE
    assert (input_tokens, output_tokens) == (100, 50)
    assert store.failed == []


async def test_run_not_queued_is_skipped() -> None:
    store = FakeJobStore()
    store.claimed = None
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await _drain(runner)
    assert (store.finished, store.failed) == ([], [])


async def test_blank_notes_fail_without_calling_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        raise AssertionError("should not be called")

    monkeypatch.setattr(IntakeRunner, "_extract", boom)
    store = FakeJobStore(text="   \n  ")
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await _drain(runner)
    assert store.failed == [(run_id, EMPTY_ERROR)]


async def test_without_api_key_the_rules_extractor_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        raise AssertionError("should not be called")

    monkeypatch.setattr(IntakeRunner, "_extract", boom)
    store = FakeJobStore(text="RISKS:\n- Vendor delay")
    runner = IntakeRunner(store, settings(with_key=False))  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await _drain(runner)
    ((_run_id, _project_id, model, suggestions, input_tokens, output_tokens),) = store.finished
    assert (model, input_tokens, output_tokens) == ("rules", 0, 0)
    assert [(item.category, item.text) for item in suggestions] == [
        (ScopeCategory.RISK, "Vendor delay")
    ]
    assert store.failed == []


class FailingClient:
    """A fake Anthropic client whose structured call fails like the real one can."""

    def __init__(self, error: Exception | None, parsed_output: object = None) -> None:
        self.messages = self
        self.error, self.parsed_output = error, parsed_output

    def parse(self, **_: object) -> object:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(parsed_output=self.parsed_output)


@pytest.mark.parametrize(
    "client",
    [
        FailingClient(anthropic.APIConnectionError(message="down", request=object())),  # type: ignore[arg-type]
        FailingClient(None, parsed_output=None),  # refusal / truncated answer
        FailingClient(ValidationError.from_exception_data("ExtractionOutput", [])),
    ],
    ids=["api-error", "no-structured-output", "invalid-answer"],
)
async def test_claude_failure_falls_back_to_the_rules(client: FailingClient) -> None:
    store = FakeJobStore(text="RISKS:\n- Vendor delay")
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    runner._llm = LLM(client, "claude-opus-5-5")  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await _drain(runner)
    ((_run_id, _project_id, model, suggestions, input_tokens, output_tokens),) = store.finished
    assert (model, input_tokens, output_tokens) == ("rules", 0, 0)
    assert [(item.category, item.text) for item in suggestions] == [
        (ScopeCategory.RISK, "Vendor delay")
    ]
    assert store.failed == []


async def test_caption_lines_reach_claude_as_speaker_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def extract(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        seen.append(notes)
        quote = "replace the old billing system by Q3."
        return ExtractionOutput(items=[_item("objective", "Replace billing", quote)]), Usage(1, 1)

    monkeypatch.setattr(IntakeRunner, "_extract", extract)
    stored = "Jane Doe: The goal is to\nJane Doe: replace the old billing system by Q3.\nBob: Yes."
    store = FakeJobStore(text=" ", uploads=(Source(uuid.uuid4(), "call.vtt", stored),))
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await _drain(runner)
    assert "Jane Doe: The goal is to replace the old billing system by Q3.\nBob: Yes." in seen[0]
    ((*_, suggestions, _input_tokens, _output_tokens),) = store.finished
    assert suggestions[0].evidence == Evidence.STATED


async def test_unexpected_crash_fails_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(IntakeRunner, "_extract", boom)
    store = FakeJobStore()
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await _drain(runner)
    assert store.failed == [(run_id, FAILED_ERROR)]
    assert "secret" not in FAILED_ERROR


async def test_fail_swallows_db_errors() -> None:
    store = FakeJobStore()
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    store.broken = True
    await runner.submit(uuid.uuid4())
    await _drain(runner)  # the claim fails, then marking failed fails: both logged


async def test_recover_stale() -> None:
    store = FakeJobStore()
    store.stale = 2
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    assert await runner.recover_stale(180) == 2
    store.broken = True
    assert await runner.recover_stale(180) == 0
    assert await IntakeRunner(None, settings()).recover_stale(180) == 0


async def test_without_database_runs_stay_queued() -> None:
    runner = IntakeRunner(None, settings())
    await runner.submit(uuid.uuid4())
    assert runner.active == 0
    await runner.shutdown()


async def test_engine_store_uses_a_transaction() -> None:
    log: list[str] = []

    class Engine:
        @asynccontextmanager
        async def begin(self) -> AsyncIterator[str]:
            log.append("begin")
            yield "conn"
            log.append("commit")

    async with engine_store(Engine())() as store:  # type: ignore[arg-type]
        assert store._conn == "conn"  # type: ignore[comparison-overlap]
    assert log == ["begin", "commit"]


async def test_background_task_schedules_on_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.background import BackgroundTasks

    result = ExtractionOutput(items=[_item("risk", "X")])
    monkeypatch.setattr(IntakeRunner, "_extract", _fake_extract(result))
    store = FakeJobStore()
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    tasks = BackgroundTasks()
    tasks.add_task(runner.submit, uuid.uuid4())
    await tasks()  # what Starlette does after sending the response
    await _drain(runner)
    assert len(store.finished) == 1


async def test_pasted_notes_and_uploads_are_analyzed_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def extract(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        seen.append(notes)
        return ExtractionOutput(items=[]), Usage(1, 1)

    monkeypatch.setattr(IntakeRunner, "_extract", extract)
    upload = Source(uuid.uuid4(), 'brief "v2".pdf', "Budget <b>$375K</b>")
    store = FakeJobStore(text="Kickoff notes", uploads=(upload, Source(uuid.uuid4(), "x", " ")))
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await _drain(runner)
    (notes,) = seen
    assert notes == (
        "<project_notes>\n"
        '<document title="Pasted notes">\nKickoff notes\n</document>\n'
        '<document title="brief &quot;v2&quot;.pdf">\n'
        "Budget &lt;b&gt;$375K&lt;/b&gt;\n</document>\n"
        "</project_notes>"
    )
    assert len(store.finished) == 1


async def test_uploads_alone_can_be_analyzed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IntakeRunner, "_extract", _fake_extract(ExtractionOutput(items=[])))
    store = FakeJobStore()
    store.claimed = ClaimedRun(uuid.uuid4(), [Source(uuid.uuid4(), "call.vtt", "Go-live in Q3")])
    runner = IntakeRunner(store, settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await _drain(runner)
    assert len(store.finished) == 1
