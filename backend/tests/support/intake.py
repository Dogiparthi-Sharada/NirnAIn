"""Moved test support."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from app.domain.common import (
    ItemOrigin,
    OrgRole,
    ScopeCategory,
    ScopeStatus,
)
from app.domain.intake import IntakeRun, ScopeItem
from app.repositories.intake import ActiveRun, ActiveRunExistsError
from tests.support.clock import NOW
from tests.support.store import Store


@dataclass
class IntakeStore:
    runs: dict[uuid.UUID, IntakeRun] = field(default_factory=dict)
    items: dict[uuid.UUID, ScopeItem] = field(default_factory=dict)
    texts: dict[uuid.UUID, str] = field(default_factory=dict)
    race: bool = False
    readable_upload: bool = False


class FakeIntakes:
    def __init__(self, store: Store, data: IntakeStore, user_id: uuid.UUID) -> None:
        self.s, self.d, self.uid = store, data, user_id

    def _role(self, project_id: uuid.UUID) -> OrgRole | None:
        p = self.s.projects.get(project_id)
        return self.s.role(p.org_id, self.uid) if p else None

    async def project_exists(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) is not None

    async def can_write(self, project_id: uuid.UUID) -> bool:
        return self._role(project_id) not in (None, OrgRole.VIEWER)

    async def active_run(self, project_id: uuid.UUID, stale_after_s: int) -> ActiveRun | None:
        return None

    async def has_readable_upload(self, project_id: uuid.UUID) -> bool:
        return self.d.readable_upload

    async def create(self, project_id: uuid.UUID, text: str) -> uuid.UUID:
        if self.d.race:
            raise ActiveRunExistsError
        run_id = uuid.uuid4()
        self.d.runs[run_id] = IntakeRun(
            id=run_id,
            project_id=project_id,
            status="queued",  # type: ignore[arg-type]
            model=None,
            items_extracted=None,
            error=None,
            created_at=NOW + timedelta(seconds=len(self.d.runs)),
            started_at=None,
            finished_at=None,
        )
        self.d.texts[run_id] = text
        return run_id

    async def list_runs(self, project_id: uuid.UUID) -> list[IntakeRun]:
        runs = [run for run in self.d.runs.values() if run.project_id == project_id]
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    async def get(self, project_id: uuid.UUID, run_id: uuid.UUID) -> IntakeRun | None:
        run = self.d.runs.get(run_id)
        visible = run and run.project_id == project_id and self._role(project_id) is not None
        return run if visible else None

    async def list_scope_items(
        self, project_id: uuid.UUID, *, include_removed: bool = False
    ) -> list[ScopeItem]:
        return [
            item
            for item in self.d.items.values()
            if item.project_id == project_id
            and (include_removed or item.status != ScopeStatus.REMOVED)
        ]

    async def create_item(
        self, project_id: uuid.UUID, category: ScopeCategory, text: str
    ) -> ScopeItem | None:
        order = sum(1 for i in self.d.items.values() if i.category == category)
        item = ScopeItem(
            id=uuid.uuid4(),
            project_id=project_id,
            category=category,
            text=text,
            status=ScopeStatus.ACCEPTED,
            origin=ItemOrigin.MANUAL,
            confidence=None,
            source_quote=None,
            evidence=None,
            evidence_note=None,
            suggested_text=None,
            source_document_id=None,
            sort_order=order,
            created_at=NOW,
            updated_at=NOW,
        )
        self.d.items[item.id] = item
        return item


class FakeIntaker:
    def __init__(self) -> None:
        self.submitted: list[uuid.UUID] = []

    async def submit(self, run_id: uuid.UUID) -> None:
        self.submitted.append(run_id)
