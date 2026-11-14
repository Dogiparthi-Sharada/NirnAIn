"""The scenario log of the API end-to-end suite and its markdown report.

Purpose: Record every scenario (expected vs actual, PASS / FAIL / BLOCKED / INFO) and rewrite
a markdown report after each one, so a crash halfway still leaves a readable report.
How it fits: `run_api_suite.py` creates one `ScenarioLog`; every scenario module calls
`check`, `status` or `blocked` on it; layer: tests.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from suite_client import ApiResponse

VERDICTS = ("PASS", "FAIL", "BLOCKED", "INFO")
ICONS = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "BLOCKED": "⛔ BLOCKED", "INFO": "📝 INFO"}
DETAIL_LIMIT = 160


@dataclass
class Outcome:
    """One logged scenario."""

    area: str
    scenario: str
    expected: str
    actual: str
    result: str


def table_cell(text: str) -> str:
    """Makes text safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


@dataclass
class ScenarioLog:
    """Collects outcomes and rewrites the markdown report after each one."""

    report_path: Path
    title: str
    outcomes: list[Outcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(
        self, area: str, scenario: str, expected: str, actual: str, passed: bool | None
    ) -> bool:
        """Records one scenario; `passed=None` logs an observation (INFO), not a verdict."""
        result = "INFO" if passed is None else ("PASS" if passed else "FAIL")
        self.outcomes.append(Outcome(area, scenario, expected, actual, result))
        self.write()
        return bool(passed)

    def blocked(self, area: str, scenario: str, expected: str, reason: str) -> None:
        """Records a scenario that could not run because an earlier step failed."""
        self.outcomes.append(Outcome(area, scenario, expected, reason, "BLOCKED"))
        self.write()

    def status(
        self,
        area: str,
        scenario: str,
        response: ApiResponse,
        expected_statuses: set[int],
        expected_text: str = "",
        must_contain: str = "",
    ) -> bool:
        """Checks an HTTP status (and optionally a phrase in the error detail)."""
        detail = response.detail
        phrase_found = not must_contain or must_contain.lower() in detail.lower()
        passed = response.status in expected_statuses and phrase_found
        codes = "/".join(str(code) for code in sorted(expected_statuses))
        expected = f"HTTP {codes}" + (f" — {expected_text}" if expected_text else "")
        actual = f"HTTP {response.status}" + (f" — {detail[:DETAIL_LIMIT]}" if detail else "")
        return self.check(area, scenario, expected, actual, passed)

    def counts(self) -> dict[str, int]:
        """How many outcomes have each verdict."""
        tally = Counter(outcome.result for outcome in self.outcomes)
        return {verdict: tally.get(verdict, 0) for verdict in VERDICTS}

    def summary_line(self) -> str:
        """`**PASS** 236 · **FAIL** 1 · …`, the line a reader looks at first."""
        return " · ".join(f"**{verdict}** {count}" for verdict, count in self.counts().items())

    def area_summary(self) -> list[str]:
        """A table of verdict counts per area, in the order the areas ran."""
        per_area: dict[str, Counter[str]] = {}
        for outcome in self.outcomes:
            per_area.setdefault(outcome.area, Counter())[outcome.result] += 1
        lines = ["| Area | " + " | ".join(VERDICTS) + " |", "|---" * (len(VERDICTS) + 1) + "|"]
        for area, tally in per_area.items():
            cells = " | ".join(str(tally.get(verdict) or "") for verdict in VERDICTS)
            lines.append(f"| {table_cell(area)} | {cells} |")
        return lines

    def write(self) -> None:
        """Rewrites the report: totals, notes, counts per area, then one table per area."""
        lines = [f"# {self.title}", "", f"Updated {datetime.now():%Y-%m-%d %H:%M:%S}. "]
        lines[-1] += self.summary_line()
        lines += ["", *(f"- {note}" for note in self.notes), "", "## Summary by area", ""]
        lines += self.area_summary()
        current_area = None
        for number, outcome in enumerate(self.outcomes, start=1):
            if outcome.area != current_area:
                current_area = outcome.area
                lines += ["", f"## {current_area}", ""]
                lines += ["| # | Scenario | Expected | Actual | Result |", "|---|---|---|---|---|"]
            cells = " | ".join(
                table_cell(text) for text in (outcome.scenario, outcome.expected, outcome.actual)
            )
            lines.append(f"| {number} | {cells} | {ICONS[outcome.result]} |")
        self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
