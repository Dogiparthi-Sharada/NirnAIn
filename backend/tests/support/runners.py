"""Fake background job runner that records what the API asked it to do.

Purpose: Endpoint tests check that a request queued its job (and failed an orphaned one) without
running any background work.
How it fits: Replaces the optimizer, intake and AI job runners through FastAPI dependency
overrides in the endpoint tests' fixtures; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import uuid


class FakeRunner:
    """Records submitted job ids and failed jobs instead of running them."""

    def __init__(self) -> None:
        """Start with nothing submitted or failed."""
        self.submitted: list[uuid.UUID] = []
        self.failed: list[tuple[uuid.UUID, str]] = []

    async def submit(self, job_id: uuid.UUID) -> None:
        """Record that the job was queued for the background."""
        self.submitted.append(job_id)

    async def fail(self, job_id: uuid.UUID, error: str) -> None:
        """Record that the job was marked failed with `error`."""
        self.failed.append((job_id, error))
