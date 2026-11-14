"""The fixed "now" of the fake stores."""

from __future__ import annotations

from datetime import UTC, datetime

# Noon UTC on Nov 13, 2026: when the fakes say rows were created.
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
