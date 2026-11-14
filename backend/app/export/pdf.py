"""Plan export as a PDF (fpdf2, built-in Helvetica: text is reduced to Latin-1)."""

from __future__ import annotations

from collections.abc import Sequence

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from app.export.model import ExportTask, PlanExport
from app.optimization.formatting import day, money

FONT = "Helvetica"
INK = (17, 24, 39)
MUTED = (100, 116, 139)
HEADINGS = FontFace(emphasis="BOLD", fill_color=(238, 242, 247))

# Characters the UI uses that the core fonts can't encode.
_LATIN1 = str.maketrans(
    {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2212": "-",  # minus
        "\u2192": "->",
        "\u2190": "<-",
        "\u2026": "...",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "\u00b7",
        "\u00a0": " ",
        "\u2009": " ",
        "\u202f": " ",
        "\u2713": "v",
        "\u26a0": "!",
    }
)

STATUS_LABELS = {"optimal": "Optimal plan", "feasible": "Feasible plan (time limit reached)"}


def latin1(text: str) -> str:
    """Text the core PDF fonts can draw: known symbols mapped, anything else `?`."""
    return text.translate(_LATIN1).encode("latin-1", "replace").decode("latin-1")


class _Document(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(FONT, size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="C")


def _heading(pdf: FPDF, text: str) -> None:
    pdf.ln(4)
    pdf.set_font(FONT, "B", 12)
    pdf.set_text_color(*INK)
    pdf.cell(0, 8, latin1(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _note(pdf: FPDF, text: str) -> None:
    pdf.set_font(FONT, size=9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5, latin1(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _cells(rows: Sequence[dict[str, str]], keys: Sequence[str]) -> list[list[str]]:
    return [[row.get(key, "") for key in keys] for row in rows]


def _assignee(task: ExportTask) -> str:
    if task.resource is None:
        return "Unassigned"
    if task.is_contractor and "contractor" not in task.resource.lower():
        return f"{task.resource} (contractor)"
    return task.resource


def _table(
    pdf: FPDF, headings: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[float]
) -> None:
    pdf.set_font(FONT, size=9)
    pdf.set_text_color(*INK)
    with pdf.table(
        col_widths=tuple(widths),
        text_align="LEFT",
        line_height=5,
        padding=1.2,
        headings_style=HEADINGS,
    ) as table:
        table.row([latin1(heading) for heading in headings])
        for row in rows:
            table.row([latin1(cell) for cell in row])


def render_pdf(plan: PlanExport) -> bytes:
    pdf = _Document(orientation="L", unit="mm", format="A4")
    pdf.set_title(latin1(f"{plan.project_name} - {plan.title}"))
    pdf.set_creator("NirnAIn")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font(FONT, "B", 18)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 9, latin1(plan.project_name), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(FONT, size=11)
    status = STATUS_LABELS.get(plan.status, plan.status)
    pdf.cell(0, 7, latin1(f"{plan.title} · {status}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    facts = [f"Starts {day(plan.start_date)}"]
    if plan.finish_date is not None:
        facts.append(f"finishes {day(plan.finish_date)}")
    if plan.expected_cost_cents is not None:
        facts.append(f"expected cost {money(plan.expected_cost_cents)}")
    facts.append(f"applied {day(plan.applied_at.date())}" if plan.applied_at else "not applied yet")
    if plan.solver:
        facts.append(f"solved with {plan.solver}")
    _note(pdf, " · ".join(facts))

    if plan.kpis:
        _heading(pdf, "Key figures")
        _table(
            pdf,
            ("Measure", "Value", "Detail"),
            _cells(plan.kpis, ("label", "value", "sub")),
            (50, 50, 177),
        )

    _heading(pdf, "Schedule")
    if plan.tasks:
        _table(
            pdf,
            ("WBS", "Work package", "Assigned to", "Start", "Finish", "Weeks", "Hours/week"),
            [
                (
                    task.code,
                    task.name,
                    _assignee(task),
                    day(task.start),
                    day(task.finish),
                    task.weeks,
                    f"{task.hours_per_week:g}",
                )
                for task in plan.tasks
            ],
            (18, 85, 55, 30, 30, 32, 27),
        )
    else:
        _note(pdf, "This plan has no scheduled work packages.")

    if plan.reassignments:
        _heading(pdf, "Reassignments")
        _table(
            pdf,
            ("Work package", "Current", "Recommended", "Reason"),
            _cells(plan.reassignments, ("task", "current", "recommended", "reason")),
            (80, 55, 55, 87),
        )

    changed = [
        change
        for change in plan.schedule_changes
        if latin1(change.get("delta", "")) not in ("", "-")
    ]
    if changed:
        _heading(pdf, "Schedule changes")
        _table(
            pdf,
            ("WBS", "Work package", "Original", "Optimized", "Change", "Reason"),
            _cells(changed, ("wbs", "name", "original", "optimized", "delta", "reason")),
            (18, 75, 30, 30, 22, 102),
        )

    return bytes(pdf.output())
