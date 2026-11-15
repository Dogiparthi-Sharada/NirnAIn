"""Tests of what the intake runner extracts and writes.

Purpose: Claude's items are grounded in the notes before they are written; without Claude or when it
fails the rules run instead; captions and uploads reach Claude as readable documents.
How it fits: Exercises app/jobs/intake.py `IntakeRunner` with the fake job store of
tests/support/intake_jobs.py and fake Claude clients; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import anthropic
import pytest
from pydantic import ValidationError

from app.ai.evidence import NO_QUOTE_NOTE
from app.ai.intake import Source
from app.ai.intake_llm import ExtractionOutput
from app.ai.llm import LLM, Usage
from app.domain.common import Evidence, ScopeCategory
from app.jobs.intake import (
    EMPTY_ERROR,
    IntakeRunner,
)
from app.repositories.intake_jobs import ClaimedRun
from tests.support.intake_jobs import FakeJobStore, extracted_item, fake_extract, runner_settings
from tests.support.runners import drain


async def test_run_extracts_and_writes_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run writes Claude's items, grounding quotes and dropping bad ones, with token counts."""
    result = ExtractionOutput(
        items=[
            extracted_item("objective", "Ship the thing", quote="ship  THE thing"),
            extracted_item("risk", "Vendor may slip", quote="not in the notes"),
            extracted_item("not_a_category", "ignored"),
            extracted_item("risk", "  "),  # blank, dropped
        ]
    )
    monkeypatch.setattr(IntakeRunner, "_extract", fake_extract(result))
    store = FakeJobStore(text="We must Ship the thing soon.")
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    assert runner.active == 1
    await drain(runner)
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


async def test_blank_notes_fail_without_calling_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank notes fail the run without calling Claude."""

    def boom(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        """Fail the test if Claude is called."""
        raise AssertionError("should not be called")

    monkeypatch.setattr(IntakeRunner, "_extract", boom)
    store = FakeJobStore(text="   \n  ")
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    run_id = uuid.uuid4()
    await runner.submit(run_id)
    await drain(runner)
    assert store.failed == [(run_id, EMPTY_ERROR)]


async def test_without_api_key_the_rules_extractor_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a key the rules extractor runs and costs no tokens."""

    def boom(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        """Fail the test if Claude is called."""
        raise AssertionError("should not be called")

    monkeypatch.setattr(IntakeRunner, "_extract", boom)
    store = FakeJobStore(text="RISKS:\n- Vendor delay")
    runner = IntakeRunner(store, runner_settings(with_key=False))  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    ((_run_id, _project_id, model, suggestions, input_tokens, output_tokens),) = store.finished
    assert (model, input_tokens, output_tokens) == ("rules", 0, 0)
    assert [(item.category, item.text) for item in suggestions] == [
        (ScopeCategory.RISK, "Vendor delay")
    ]
    assert store.failed == []


class FailingClient:
    """A fake Anthropic client whose structured call fails like the real one can."""

    def __init__(self, error: Exception | None, parsed_output: object = None) -> None:
        """Fail with `error`, or answer with `parsed_output`."""
        self.messages = self
        self.error, self.parsed_output = error, parsed_output

    def parse(self, **_: object) -> object:
        """Raise the error or return the parsed output."""
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
    """API errors, missing or invalid answers from Claude fall back to the rules."""
    store = FakeJobStore(text="RISKS:\n- Vendor delay")
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    runner._llm = LLM(client, "claude-opus-5-5")  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    ((_run_id, _project_id, model, suggestions, input_tokens, output_tokens),) = store.finished
    assert (model, input_tokens, output_tokens) == ("rules", 0, 0)
    assert [(item.category, item.text) for item in suggestions] == [
        (ScopeCategory.RISK, "Vendor delay")
    ]
    assert store.failed == []


async def test_caption_lines_reach_claude_as_speaker_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caption lines are merged into speaker turns before Claude reads them."""
    seen: list[str] = []

    def extract(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        """Record the notes and return one objective quoted from them."""
        seen.append(notes)
        quote = "replace the old billing system by Q3."
        return ExtractionOutput(
            items=[extracted_item("objective", "Replace billing", quote)]
        ), Usage(1, 1)

    monkeypatch.setattr(IntakeRunner, "_extract", extract)
    stored = "Jane Doe: The goal is to\nJane Doe: replace the old billing system by Q3.\nBob: Yes."
    store = FakeJobStore(text=" ", uploads=(Source(uuid.uuid4(), "call.vtt", stored),))
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    assert "Jane Doe: The goal is to replace the old billing system by Q3.\nBob: Yes." in seen[0]
    ((*_, suggestions, _input_tokens, _output_tokens),) = store.finished
    assert suggestions[0].evidence == Evidence.STATED


async def test_pasted_notes_and_uploads_are_analyzed_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pasted notes and uploads reach Claude as escaped documents; blank uploads drop."""
    seen: list[str] = []

    def extract(self: IntakeRunner, notes: str) -> tuple[ExtractionOutput, Usage]:
        """Record the notes and return no items."""
        seen.append(notes)
        return ExtractionOutput(items=[]), Usage(1, 1)

    monkeypatch.setattr(IntakeRunner, "_extract", extract)
    upload = Source(uuid.uuid4(), 'brief "v2".pdf', "Budget <b>$375K</b>")
    store = FakeJobStore(text="Kickoff notes", uploads=(upload, Source(uuid.uuid4(), "x", " ")))
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
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
    """A run with only an upload is analyzed."""
    monkeypatch.setattr(IntakeRunner, "_extract", fake_extract(ExtractionOutput(items=[])))
    store = FakeJobStore()
    store.claimed = ClaimedRun(uuid.uuid4(), [Source(uuid.uuid4(), "call.vtt", "Go-live in Q3")])
    runner = IntakeRunner(store, runner_settings())  # type: ignore[arg-type]
    await runner.submit(uuid.uuid4())
    await drain(runner)
    assert len(store.finished) == 1
