"""The fixed "now" of the fake stores.

Purpose: Give every fake row the same creation time so ordering and timestamps in assertions are
deterministic.
How it fits: Imported by tests/support/ai_jobs.py, documents.py and intake.py and their tests;
layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import UTC, datetime

# Noon UTC on Nov 13, 2026: when the fakes say rows were created.
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
