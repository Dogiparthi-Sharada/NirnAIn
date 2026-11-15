"""The optional Claude review of a WBS (the paid part of AI Validate).

Purpose: Ask Claude for at most a few concise issues a PM should fix before scheduling (missing
work, overlaps, uncovered scope) and keep them short and within the allowed types.
How it fits: Called by the validate job (app/jobs/ai_jobs.py) after the free checks in
wbs_validate.py, whose issues it doesn't repeat; layer: ai.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.prompting import DATA_RULE, as_data, data_block, one_line
from app.ai.wbs_generate import select_scope
from app.ai.wbs_validate import Issue, ValidationInput

ReviewIssueType = Literal["missing", "duplicate", "warning"]

MAX_REVIEW_ISSUES = 6
ISSUE_LIMIT = 240
REVIEW_UNAVAILABLE = "The AI review couldn't run this time; only the built-in checks ran."


# Claude's structured-output schema. The classes deliberately have no docstrings:
# Pydantic would send them to the model as schema descriptions and change its answers.
class ReviewIssue(BaseModel):
    type: ReviewIssueType
    text: str = Field(description="One concise sentence, at most 160 characters")


class WbsReview(BaseModel):
    issues: list[ReviewIssue]


VALIDATE_SYSTEM = (
    "You are a senior project planner reviewing a work breakdown structure (WBS). "
    + DATA_RULE
    + " Report at most 6 concise issues a project manager should fix before scheduling: "
    "missing work (e.g. testing/UAT, data migration, disaster recovery/backup, training, "
    "deployment, handover), work packages that overlap or duplicate each other (type "
    "duplicate), and accepted scope items the WBS does not cover (type missing). Use type "
    "warning for anything else. Refer to WBS items by their code. Don't repeat structural "
    "problems such as missing durations, don't comment on owners, staffing or skills (people "
    "are matched to the work on a later step), and don't invent facts the data doesn't "
    "support. Return an empty list when the WBS is sound."
)


def build_validation_prompt(checks_input: ValidationInput) -> str:
    """Return the review prompt: the WBS as an indented outline plus the accepted scope items."""
    lines = [f"Project: {as_data(one_line(checks_input.project_name, 200))}", "WBS:"]
    for node in checks_input.nodes:
        detail = f" ({node.duration_days} days)" if node.duration_days is not None else ""
        indent = "  " * (node.level - 1)
        lines.append(f"{indent}{node.code} {as_data(one_line(node.name, 200))}{detail}")
    scope = select_scope(checks_input.scope)
    if scope:
        lines.append("Accepted scope items:")
        lines.extend(
            f"- [{item.category}] {as_data(one_line(item.text, 400))}" for item in scope[:80]
        )
    return data_block("\n".join(lines)) + "\n\nReview the WBS."


def review_issues(review: WbsReview) -> list[Issue]:
    """Return Claude's issues trimmed to `MAX_REVIEW_ISSUES`, each on one line within the limit."""
    out: list[Issue] = []
    for issue in review.issues[:MAX_REVIEW_ISSUES]:
        text = one_line(issue.text, ISSUE_LIMIT)
        if text:
            out.append(Issue(issue.type, text))
    return out
