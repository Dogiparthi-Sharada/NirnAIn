"""Display strings, matching what the Optimization Results screen shows.

Purpose: Format money, dates, durations and week ranges the same way everywhere the plan is
described, so KPI cards, reasons and remedies agree.
How it fits: Used by explain.py, run_summary.py, conflicts.py and remedy_options.py; layer:
optimization.
Created: 2026-10-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from datetime import date

EN_DASH = chr(0x2013)


def money(cents: int | float) -> str:
    """`$361,200` (whole dollars, rounded half away from zero)."""
    dollars = int(abs(cents) / 100 + 0.5)
    return f"{'-' if cents < 0 and dollars else ''}${dollars:,}"


def day(when: date) -> str:
    """`Oct 14, 2025` (portable: no platform-specific strftime flags)."""
    return f"{when:%b} {when.day}, {when.year}"


def plural(count: int, word: str) -> str:
    """Return `count word` with an English plural `s` when the count is not one."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def weeks_label(first_week: int, last_week: int) -> str:
    """`Wk 3-6` (with an en dash, as the UI shows) from 0-based week indices."""
    if first_week == last_week:
        return f"Wk {first_week + 1}"
    return f"Wk {first_week + 1}{EN_DASH}{last_week + 1}"


def shift(days: int, per_week: int) -> str:
    """`+2w`, `-3d`, `+1.5w`, or `—` for no change."""
    if days == 0:
        return "—"
    sign = "+" if days > 0 else "-"
    magnitude = abs(days)
    if magnitude < per_week:
        return f"{sign}{magnitude}d"
    weeks = magnitude / per_week
    text = f"{weeks:.0f}" if magnitude % per_week == 0 else f"{weeks:.1f}"
    return f"{sign}{text}w"


def money_short(cents: int | float) -> str:
    """`$28K`, `$1.2M`, `$950`: for headlines where the exact figure follows."""
    dollars = abs(cents) / 100
    sign = "-" if cents < 0 else ""
    if dollars >= 1_000_000:
        return f"{sign}${dollars / 1_000_000:.1f}M".replace(".0M", "M")
    if dollars >= 1_000:
        return f"{sign}${round(dollars / 1_000):,}K"
    return f"{sign}${round(dollars):,}"


def short_day(when: date) -> str:
    """`Oct 17` (no year)."""
    return f"{when:%b} {when.day}"
