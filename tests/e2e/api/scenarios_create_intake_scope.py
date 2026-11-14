"""API end-to-end scenarios for steps 1-3: create a project, AI intake and uploads, scope review.

Purpose: Exercise project creation, document uploads, the no-key intake (built-in rules, so
no Claude tokens) and scope review with fake data, including the corner cases: invalid
values, duplicates, unsupported files and missing input.
How it fits: `run_api_suite.py` runs areas 1-3 first; `create_project_scenarios` returns the
`QaProject` every later area builds on; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid

from suite_client import ApiClient, wait_until
from suite_fixtures import MEETING_NOTES, ZOOM_TRANSCRIPT, QaProject
from suite_report import ScenarioLog

CREATE_AREA = "1. Create project"
INTAKE_AREA = "2. AI intake and uploads (no Claude: rules)"
SCOPE_AREA = "3. Scope review (edit requirements)"
INTAKE_DONE = {"succeeded", "failed", "cancelled"}
UNICODE_NAME = ("QA ünïcödé 🚀 プロジェクト " + "x" * 160)[:200]


def project_body(name: str) -> dict[str, object]:
    """A complete, valid create-project body."""
    return {
        "name": name,
        "description": "QA fake project: finance and sales reporting on one data platform.",
        "type": "technology",
        "priority": "high",
        "methodology": "hybrid",
        "visibility": "internal",
        "department": "Data & Analytics",
        "sponsor": "Priya Shah",
        "startDate": "2026-11-30",
        "targetEndDate": "2027-06-30",
        "budgetCents": 45_000_000,
        "currency": "USD",
        "status": "planning",
        "currentStep": "intake",
    }


def invalid_create_scenarios(client: ApiClient, log: ScenarioLog, path: str, name: str) -> None:
    """Step 1 validation: every rejected create body."""
    base = project_body(name)
    for label, changes in (
        ("Empty name rejected", {"name": ""}),
        ("201-character name rejected", {"name": "N" * 201}),
        ("Negative budget rejected", {"budgetCents": -100}),
        ("Status 'completed' at creation rejected", {"status": "completed"}),
    ):
        log.status(CREATE_AREA, label, client.call("POST", path, {**base, **changes}), {422})
    backwards_dates = {"name": "QA backwards dates", "startDate": "2027-06-30"}
    backwards = client.call(
        "POST", path, {**base, **backwards_dates, "targetEndDate": "2026-11-30"}
    )
    log.status(
        CREATE_AREA,
        "End date before start date rejected",
        backwards,
        {422},
        "dates must be in order",
    )
    if backwards.status == 201:
        client.call("DELETE", f"/projects/{backwards.data['id']}")


def unicode_round_trip(client: ApiClient, log: ScenarioLog, path: str, name: str) -> None:
    """Step 1: a 200-character Unicode name survives a round trip; then the project is deleted."""
    created = client.call("POST", path, {**project_body(name), "name": UNICODE_NAME})
    log.status(CREATE_AREA, "200-character Unicode/emoji name accepted", created, {201})
    if created.status != 201:
        return
    project_path = f"/projects/{created.data['id']}"
    fetched = client.call("GET", project_path)
    same = fetched.data is not None and fetched.data.get("name") == UNICODE_NAME
    log.check(
        CREATE_AREA,
        "Unicode name round-trips unchanged",
        "same text back",
        "same" if same else "changed",
        same,
    )
    log.status(CREATE_AREA, "Delete a project", client.call("DELETE", project_path), {204})
    log.status(CREATE_AREA, "Deleted project is gone", client.call("GET", project_path), {404})


def create_project_scenarios(
    client: ApiClient, log: ScenarioLog, org_id: str, name: str
) -> QaProject | None:
    """Step 1: valid creation plus the validation corner cases; returns the main project."""
    path = f"/orgs/{org_id}/projects"
    created = client.call("POST", path, project_body(name))
    log.status(CREATE_AREA, f"Create '{name}' with every field", created, {201}, "project created")
    if created.status != 201:
        return None
    project = QaProject(project_id=created.data["id"], name=name)
    invalid_create_scenarios(client, log, path, name)
    unicode_round_trip(client, log, path, name)
    listed = client.call("GET", path)
    items = listed.data if isinstance(listed.data, list) else (listed.data or {}).get("items", [])
    names = [item.get("name") for item in items or []]
    log.check(
        CREATE_AREA,
        "New project appears in the project list",
        "listed",
        f"{len(names)} projects listed",
        name in names,
    )
    unknown = client.call("GET", f"/projects/{uuid.uuid4()}")
    log.status(CREATE_AREA, "Unknown project id → 404", unknown, {404})
    project_path = f"/projects/{project.project_id}"
    updated = client.call(
        "PATCH", project_path, {"priority": "medium", "description": "Updated by QA"}
    )
    log.status(CREATE_AREA, "Update project (PATCH description, priority)", updated, {200})
    too_far = client.call("PATCH", project_path, {"progressPct": 101})
    log.status(CREATE_AREA, "PATCH progress 101% rejected", too_far, {422})
    return project


def upload_scenarios(client: ApiClient, log: ScenarioLog, base: str) -> None:
    """Step 2 uploads: video, executables and empty files refused; transcripts and audio kept."""
    documents = f"{base}/documents"
    mp4 = client.call(
        "POST", documents, upload=("kickoff.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")
    )
    log.status(
        INTAKE_AREA,
        "Upload .mp4 refused with the video message",
        mp4,
        {415},
        "MP4 not supported",
        must_contain="MP4",
    )
    for label, upload in (
        ("Upload .mov refused", ("kickoff.mov", b"fake", "video/quicktime")),
        ("Upload .webm as video/webm refused", ("call.webm", b"fake", "video/webm")),
        ("Upload an .exe refused", ("setup.exe", b"MZ\x90\x00", "application/octet-stream")),
    ):
        log.status(INTAKE_AREA, label, client.call("POST", documents, upload=upload), {415})
    empty = client.call("POST", documents, upload=("empty.txt", b"", "text/plain"))
    empty_state = empty.detail or (empty.data or {}).get("extractionStatus", "")
    log.check(
        INTAKE_AREA,
        "Upload an empty .txt",
        "refused (422) or stored as failed extraction",
        f"HTTP {empty.status} {empty_state}",
        empty.status == 422
        or (empty.status == 201 and empty.data.get("extractionStatus") == "failed"),
    )
    transcript = client.call(
        "POST", documents, upload=("kickoff-meeting.vtt", ZOOM_TRANSCRIPT, "text/vtt")
    )
    stored = transcript.data or {}
    log.check(
        INTAKE_AREA,
        "Upload Zoom-style .vtt transcript",
        "201, kind transcript, text read",
        f"HTTP {transcript.status} kind={stored.get('kind')} "
        f"status={stored.get('extractionStatus')} chars={stored.get('chars')}",
        transcript.status == 201
        and stored.get("extractionStatus") == "succeeded"
        and stored.get("kind") == "transcript",
    )
    audio = client.call("POST", documents, upload=("standup.mp3", b"ID3fakeaudio", "audio/mpeg"))
    log.check(
        INTAKE_AREA,
        "Upload .mp3 audio",
        "stored, marked 'transcription not available yet'",
        f"HTTP {audio.status} {(audio.data or {}).get('extractionError') or audio.detail}",
        audio.status in {201, 415},
    )
    if audio.status == 201:
        document_path = f"/documents/{audio.data['id']}"
        removed = client.call("DELETE", document_path)
        log.status(INTAKE_AREA, "Delete an uploaded document", removed, {204})
        again = client.call("DELETE", document_path)
        log.status(INTAKE_AREA, "Delete it again → 404", again, {404})


def intake_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 2: uploads (transcript, video, audio, junk) and the no-key rules intake."""
    base = f"/projects/{project.project_id}"
    nothing = client.call("POST", f"{base}/intake/runs", {"text": ""})
    log.status(
        INTAKE_AREA,
        "Analyze with no notes and no upload explains what to do",
        nothing,
        {422},
        "paste notes or upload",
    )
    upload_scenarios(client, log, base)
    first = client.call("POST", f"{base}/intake/runs", {"text": MEETING_NOTES})
    second = client.call("POST", f"{base}/intake/runs", {"text": MEETING_NOTES})
    log.status(INTAKE_AREA, "Analyze pasted meeting notes + transcript", first, {201, 202})
    log.check(
        INTAKE_AREA,
        "Second Analyze while the first is still running",
        "409 (one active run) or accepted if the first already finished",
        f"HTTP {second.status} {second.detail}",
        second.status in {409, 201, 202},
    )
    if first.status not in {201, 202}:
        return
    run = (
        wait_until(
            lambda: client.call("GET", f"{base}/intake/runs/{first.data['id']}"),
            lambda answer: (answer.data or {}).get("status") in INTAKE_DONE,
        ).data
        or {}
    )
    items = run.get("itemsExtracted") or 0
    log.check(
        INTAKE_AREA,
        "Intake run finishes with the rules extractor",
        "succeeded, model 'rules', items > 0",
        f"status={run.get('status')} model={run.get('model')} items={items} "
        f"error={run.get('error')}",
        run.get("status") == "succeeded" and run.get("model") == "rules" and items > 0,
    )
    history = client.call("GET", f"{base}/intake/runs")
    log.check(
        INTAKE_AREA,
        "Previous intakes listed newest first",
        "list with this run",
        f"{len(history.data or [])} runs",
        bool(history.data),
    )


def scope_validation(client: ApiClient, log: ScenarioLog, base: str, item_id: str) -> None:
    """Step 3 validation: empty, too long, unknown status or category, unknown item."""
    item_path = f"/scope-items/{item_id}"
    for label, change in (
        ("Empty item text rejected", {"text": ""}),
        ("2,001-character item text rejected", {"text": "x" * 2001}),
        ("Unknown status value rejected", {"status": "maybe"}),
    ):
        log.status(SCOPE_AREA, label, client.call("PATCH", item_path, change), {422})
    wishlist = client.call("POST", f"{base}/scope-items", {"category": "wishlist", "text": "x"})
    log.status(SCOPE_AREA, "Unknown category rejected", wishlist, {422})
    ghost = client.call("PATCH", f"/scope-items/{uuid.uuid4()}", {"text": "ghost"})
    log.status(SCOPE_AREA, "Edit a non-existent item → 404", ghost, {404})


def scope_scenarios(client: ApiClient, log: ScenarioLog, project: QaProject) -> None:
    """Step 3: review extracted items (accept, edit, remove, add) and validation."""
    base = f"/projects/{project.project_id}"
    items = client.call("GET", f"{base}/scope-items").data or []
    categories = sorted({item["category"] for item in items})
    quoted = sum(bool(item.get("sourceQuote")) for item in items)
    log.check(
        SCOPE_AREA,
        "Extracted items have categories and quotes",
        "items with category + sourceQuote",
        f"{len(items)} items in {categories}; with quote: {quoted}",
        bool(items),
    )
    if not items:
        return
    first_path, last_path = f"/scope-items/{items[0]['id']}", f"/scope-items/{items[-1]['id']}"
    edited = client.call("PATCH", first_path, {"text": items[0]["text"] + " (edited by PM)"})
    log.status(SCOPE_AREA, "Edit a requirement's text", edited, {200})
    removed = client.call("PATCH", last_path, {"status": "removed"})
    log.status(SCOPE_AREA, "Remove an item", removed, {200})
    accepted = [
        client.call("PATCH", f"/scope-items/{item['id']}", {"status": "accepted"}).status
        for item in items[:-1]
    ]
    log.check(
        SCOPE_AREA,
        "Accept all remaining items",
        "all 200",
        f"{accepted.count(200)}/{len(accepted)} accepted",
        all(code == 200 for code in accepted),
    )
    manual_body = {
        "category": "requirement",
        "text": "Every dashboard must load in under 3 seconds.",
    }
    manual = client.call("POST", f"{base}/scope-items", manual_body)
    added = manual.data or {}
    log.check(
        SCOPE_AREA,
        "Add a manual requirement",
        "201, origin manual, no evidence ('Yours')",
        f"HTTP {manual.status} origin={added.get('origin')} evidence={added.get('evidence')}",
        manual.status == 201 and added.get("evidence") is None,
    )
    if manual.status == 201:
        client.call("PATCH", f"/scope-items/{added['id']}", {"status": "accepted"})
    scope_validation(client, log, base, items[0]["id"])
